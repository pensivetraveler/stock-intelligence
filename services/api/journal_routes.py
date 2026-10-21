"""Write and read routes for the manual (actual) and hypothetical trade ledgers."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StringConstraints
from starlette.responses import Response

from packages.contracts.validator import validate_document
from services.journal.csv_import import (
    MAPPING_FIELDS,
    MAX_FILE_BYTES,
    ImportRequest,
    StatementImportError,
    batch_id_for,
    classify,
    file_sha256,
    normalize_rows,
    read_rows,
    suggest_mapping,
    summarize,
    validate_broker_label,
    validate_mapping,
)
from services.journal.ledger import (
    ExchangeTimeError,
    exchange_local_to_utc,
    manual_positions,
    scenario_pnl,
)
from services.journal.reconcile import Parameters, reconcile
from services.journal.store import JournalConflictError, JournalStore, request_hash
from services.market_data.instruments import calendar_for, market_of

DecimalText = Annotated[str, StringConstraints(pattern=r"^(0|[1-9][0-9]*)(\.[0-9]{1,8})?$")]
RecordId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._:-]{8,64}$")]
InstrumentId = Annotated[str, StringConstraints(min_length=1, max_length=200)]
UtcText = Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")]
ErrorResponse = Callable[[Request, int, str, str], JSONResponse]


class ManualExecutionRequest(BaseModel):
    """A user's claim about one of their own fills. The clicked candle is context only."""

    model_config = ConfigDict(extra="forbid")
    execution_id: RecordId
    instrument_id: InstrumentId
    side: Literal["buy", "sell"]
    quantity: DecimalText
    price: DecimalText
    fees: DecimalText = "0"
    time_precision: Literal["exact", "minute", "approximate", "unknown"]
    executed_date_et: date | None = None
    executed_time_et: time | None = None
    selected_interval_start_utc: UtcText
    selected_interval_end_utc: UtcText
    memo: Annotated[str, StringConstraints(max_length=500)] = ""


class ScenarioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: RecordId
    instrument_id: InstrumentId
    direction: Literal["long", "short"]
    quantity: DecimalText
    entry_price: DecimalText
    exit_price: DecimalText
    fee_assumption: DecimalText = "0"
    slippage_assumption: DecimalText = "0"
    entry_interval_start_utc: UtcText
    exit_date_et: date
    exit_time_et: time


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class JournalRequestError(ValueError):
    """A request that cannot become a valid journal record."""


def _executed_at(request: ManualExecutionRequest) -> str | None:
    given = (request.executed_date_et, request.executed_time_et)
    if request.time_precision == "unknown":
        if any(value is not None for value in given):
            raise JournalRequestError("leave the execution time empty when precision is unknown")
        return None
    if request.executed_date_et is None or request.executed_time_et is None:
        raise JournalRequestError("enter the execution date and time in exchange-local time")
    if request.time_precision == "minute" and request.executed_time_et.second:
        raise JournalRequestError("minute precision times must not include seconds")
    return _utc_text(
        exchange_local_to_utc(
            request.executed_date_et,
            request.executed_time_et,
            calendar_for(request.instrument_id).zone,
        )
    )


def _currency(instrument_id: str) -> str:
    """Korean listings trade in KRW, everything else in USD."""
    return "KRW" if market_of(instrument_id) == "KR" else "USD"


def register_journal_routes(
    application: FastAPI,
    *,
    journal: Callable[[], JournalStore | None],
    known_instruments: Callable[[], set[str]],
    now: Callable[[], datetime],
    error_response: ErrorResponse,
) -> None:
    def unavailable(request: Request) -> JSONResponse:
        return error_response(
            request,
            503,
            "journal_storage_unconfigured",
            "Set SIW_DATA_DIR to record trades; nothing was stored",
        )

    def store_record(
        request: Request,
        build: Callable[[], dict[str, Any]],
        record: Callable[[JournalStore, dict[str, Any]], tuple[dict[str, Any], bool]],
    ) -> Response:
        store = journal()
        if store is None:
            return unavailable(request)
        try:
            document = build()
            stored, created = record(store, document)
        except JournalConflictError as exc:
            return error_response(request, 409, "journal_conflict", str(exc))
        except (JournalRequestError, ExchangeTimeError, ValueError) as exc:
            return error_response(request, 422, "invalid_journal_entry", str(exc))
        return JSONResponse(status_code=201 if created else 200, content=stored)

    def require_known(instrument_id: str) -> None:
        if instrument_id not in known_instruments():
            raise JournalRequestError("instrument is not available from the configured provider")

    @application.post("/v1/trades/manual", response_model=None)
    def record_manual_execution(request: Request, body: ManualExecutionRequest) -> Response:
        def build() -> dict[str, Any]:
            require_known(body.instrument_id)
            return {
                "kind": "manual_execution",
                "schema_version": "0.1",
                "execution_id": body.execution_id,
                "instrument_id": body.instrument_id,
                "side": body.side,
                "quantity": body.quantity,
                "price": body.price,
                "currency": _currency(body.instrument_id),
                "asserted_at_utc": _utc_text(now()),
                "time_precision": body.time_precision,
                "executed_at_utc": _executed_at(body),
                "selected_interval_start_utc": body.selected_interval_start_utc,
                "selected_interval_end_utc": body.selected_interval_end_utc,
                "evidence_status": "unverified",
                "fees": body.fees,
                "memo": body.memo,
            }

        digest = request_hash(body.model_dump(mode="json"))
        return store_record(request, build, lambda store, doc: store.record_manual(doc, digest))

    @application.get("/v1/trades/manual", response_model=None)
    def manual_ledger(
        request: Request,
        instrument_id: Annotated[str | None, Query(max_length=200)] = None,
    ) -> Response:
        store = journal()
        if store is None:
            return unavailable(request)
        executions = store.manual_executions(instrument_id)
        document = {
            "kind": "manual_ledger_response",
            "schema_version": "0.1",
            "ledger": "manual_actual",
            "executions": executions,
            "positions": manual_positions(executions),
        }
        validate_document(document)
        return JSONResponse(content=document)

    @application.get("/v1/trades/reconciliation", response_model=None)
    def reconciliation(
        request: Request,
        instrument_id: Annotated[str | None, Query(max_length=200)] = None,
        since: Annotated[
            str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        ] = None,
        price_tolerance_bps: Annotated[
            str | None, Query(pattern=r"^(0|[1-9][0-9]{0,3})(\.[0-9]{1,2})?$")
        ] = None,
    ) -> Response:
        """Compare the broker API ledger with manual and imported executions; read-only."""
        store = journal()
        if store is None:
            return unavailable(request)
        parameters = (
            Parameters()
            if price_tolerance_bps is None
            else Parameters(price_tolerance_bps=Decimal(price_tolerance_bps))
        )
        result = reconcile(
            store.broker_executions(instrument_id),
            store.manual_executions(instrument_id),
            store.imported_executions(instrument_id),
            since=None if since is None else datetime.fromisoformat(since.replace("Z", "+00:00")),
            parameters=parameters,
        )
        document = {
            "kind": "trade_reconciliation_response",
            "schema_version": "0.1",
            "view": "computed",
            "instrument_id": instrument_id,
            "since_utc": since,
            **result,
        }
        validate_document(document)
        return JSONResponse(content=document)

    @application.post("/v1/scenarios", response_model=None)
    def record_scenario(request: Request, body: ScenarioRequest) -> Response:
        def build() -> dict[str, Any]:
            require_known(body.instrument_id)
            return {
                "kind": "scenario_definition",
                "schema_version": "0.1",
                "scenario_id": body.scenario_id,
                "version": 1,
                "instrument_id": body.instrument_id,
                "origin": "hypothetical",
                "direction": body.direction,
                "entry_price": body.entry_price,
                "exit_price": body.exit_price,
                "quantity": body.quantity,
                "currency": _currency(body.instrument_id),
                "fee_assumption": body.fee_assumption,
                "slippage_assumption": body.slippage_assumption,
                "entry_interval_start_utc": body.entry_interval_start_utc,
                "exit_interval_start_utc": _utc_text(
                    exchange_local_to_utc(
                        body.exit_date_et,
                        body.exit_time_et,
                        calendar_for(body.instrument_id).zone,
                    )
                ),
                "created_at_utc": _utc_text(now()),
            }

        digest = request_hash(body.model_dump(mode="json"))
        return store_record(request, build, lambda store, doc: store.record_scenario(doc, digest))

    @application.get("/v1/scenarios", response_model=None)
    def scenario_ledger(
        request: Request,
        instrument_id: Annotated[str | None, Query(max_length=200)] = None,
    ) -> Response:
        store = journal()
        if store is None:
            return unavailable(request)
        document = {
            "kind": "scenario_ledger_response",
            "schema_version": "0.1",
            "ledger": "hypothetical",
            "scenarios": [
                {"scenario": scenario, **scenario_pnl(scenario)}
                for scenario in store.scenarios(instrument_id)
            ],
        }
        validate_document(document)
        return JSONResponse(content=document)


class StatementImportRequest(BaseModel):
    """A broker statement CSV as text, read by the browser; it is never stored."""

    model_config = ConfigDict(extra="forbid")
    file_name: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    content: Annotated[str, StringConstraints(max_length=MAX_FILE_BYTES)]
    broker_label: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    timestamp_timezone: Literal["America/New_York", "Asia/Seoul", "UTC"] = "America/New_York"
    delimiter: Literal[",", ";", "\t"] = ","
    mapping: dict[str, str | None] | None = None


class StatementCommitRequest(StatementImportRequest):
    # Row number (as text, from JSON) -> what to do with a possible duplicate.
    decisions: dict[str, Literal["import", "skip"]] = {}


def register_import_routes(
    application: FastAPI,
    *,
    journal: Callable[[], JournalStore | None],
    symbols: Callable[[], dict[str, str]],
    now: Callable[[], datetime],
    error_response: ErrorResponse,
) -> None:
    def preview(store: JournalStore, body: StatementImportRequest) -> dict[str, Any]:
        broker_label = validate_broker_label(body.broker_label)
        request = ImportRequest(
            file_name=body.file_name,
            content=body.content,
            broker_label=broker_label,
            timestamp_timezone=body.timestamp_timezone,
            delimiter=body.delimiter,
            mapping=body.mapping,
        )
        columns, rows = read_rows(request)
        mapping = {**dict.fromkeys(MAPPING_FIELDS), **(body.mapping or suggest_mapping(columns))}
        validate_mapping(mapping, columns)
        sha256 = file_sha256(body.content)
        normalized = normalize_rows(request, rows, mapping, symbols(), sha256, now())
        preview_rows = classify(normalized, store.imported_executions())
        document = {
            "kind": "import_preview_response",
            "schema_version": "0.1",
            "batch_id": batch_id_for(sha256),
            "file_name": body.file_name,
            "file_sha256": sha256,
            "broker_label": broker_label,
            "columns": columns,
            "mapping": mapping,
            "timestamp_timezone": body.timestamp_timezone,
            "already_imported": store.batch_for_file(sha256) is not None,
            "rows": preview_rows,
            "summary": summarize(preview_rows),
        }
        validate_document(document)
        return document

    def unavailable(request: Request) -> JSONResponse:
        return error_response(
            request,
            503,
            "journal_storage_unconfigured",
            "Set SIW_DATA_DIR to import statements; nothing was stored",
        )

    @application.post("/v1/imports/trades/preview", response_model=None)
    def preview_import(request: Request, body: StatementImportRequest) -> Response:
        store = journal()
        if store is None:
            return unavailable(request)
        try:
            return JSONResponse(content=preview(store, body))
        except StatementImportError as exc:
            return error_response(request, 422, "invalid_import", str(exc))

    @application.post("/v1/imports/trades/commit", response_model=None)
    def commit_import(request: Request, body: StatementCommitRequest) -> Response:
        store = journal()
        if store is None:
            return unavailable(request)
        try:
            planned = preview(store, body)
        except StatementImportError as exc:
            return error_response(request, 422, "invalid_import", str(exc))
        existing = store.batch_for_file(planned["file_sha256"])
        if existing is not None:
            return JSONResponse(status_code=200, content=existing)
        undecided = [
            row["row_number"]
            for row in planned["rows"]
            if row["status"] == "possible_duplicate"
            and str(row["row_number"]) not in body.decisions
        ]
        if undecided:
            return error_response(
                request,
                422,
                "duplicate_review_required",
                f"decide whether to import possible duplicate rows: {undecided}",
            )
        imported: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for row in planned["rows"]:
            number, status = row["row_number"], row["status"]
            if status == "new":
                imported.append(row["execution"])
            elif status == "possible_duplicate" and body.decisions[str(number)] == "import":
                imported.append(
                    {
                        **row["execution"],
                        "duplicate_review": {
                            "decision": "imported_after_review",
                            "related": row["matches"],
                        },
                    }
                )
            else:
                reason = "skipped_after_review" if status == "possible_duplicate" else status
                skipped.append({"row_number": number, "reason": reason})
        batch = {
            "kind": "import_batch",
            "schema_version": "0.1",
            "batch_id": planned["batch_id"],
            "file_name": planned["file_name"],
            "file_sha256": planned["file_sha256"],
            "broker_label": planned["broker_label"],
            "committed_at_utc": _utc_text(now()),
            "row_count": len(planned["rows"]),
            "imported_execution_ids": [item["execution_id"] for item in imported],
            "skipped_rows": skipped,
        }
        try:
            stored, created = store.commit_import(batch, imported)
        except JournalConflictError as exc:
            return error_response(request, 409, "journal_conflict", str(exc))
        return JSONResponse(status_code=201 if created else 200, content=stored)

    @application.get("/v1/trades/imported", response_model=None)
    def imported_ledger(
        request: Request,
        instrument_id: Annotated[str | None, Query(max_length=200)] = None,
    ) -> Response:
        store = journal()
        if store is None:
            return unavailable(request)
        executions = store.imported_executions(instrument_id)
        document = {
            "kind": "imported_ledger_response",
            "schema_version": "0.1",
            "ledger": "statement_imported",
            "executions": executions,
            "positions": manual_positions(executions),
            "batches": store.import_batches(),
        }
        validate_document(document)
        return JSONResponse(content=document)
