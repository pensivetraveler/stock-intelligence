"""Trade review routes (ADR 0042): trades grouped from the journal, with labels.

Journal records are only read. Strategy assignments, grouping rules, manual groups and
notes are written to the review store.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import threading
import uuid
from collections.abc import Callable
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from starlette.responses import Response

from packages.contracts.validator import validate_document
from services.agents.evidence import instrument_currency
from services.api.journal_routes import ErrorResponse
from services.backtest.data import GetBars
from services.journal.store import JournalStore
from services.market_data.provider import MarketDataError
from services.review.claude import (
    DEFAULT_MODEL,
    MODELS,
    ReportError,
    ask_for_report,
    convert_principles,
)
from services.review.conversation import (
    Conversation,
    ConversationError,
    call_claude,
    first_content,
    system_prompt,
)
from services.review.principles import PrincipleError, check_trade, clean_items, version_for
from services.review.report import Capital, report_evidence
from services.review.store import (
    CapitalNotFoundError,
    ConversationNotFoundError,
    DraftNotFoundError,
    GroupNotFoundError,
    ReviewStore,
)
from services.review.tools import ToolContext
from services.review.trades import (
    Fill,
    Rule,
    Trade,
    build_trades,
    fills_of,
    last_key,
    status_of,
    trade_document,
)

Ledger = Literal["manual", "imported", "broker"]
Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)]
Strategies = Callable[[], list[dict[str, Any]] | None]


class ExecutionRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ledger: Ledger
    execution_id: str = Field(min_length=1, max_length=200)

    @property
    def ref(self) -> str:
        return f"{self.ledger}:{self.execution_id}"


class AssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    executions: list[ExecutionRef] = Field(min_length=1, max_length=2000)
    strategy_id: str | None = Field(default=None, min_length=1, max_length=200)


class RuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule: Rule


class GroupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    executions: list[ExecutionRef] = Field(min_length=1, max_length=2000)


class AnnotationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(default="", max_length=4000)
    tags: list[Tag] = Field(default_factory=list, max_length=20)
    reviewed: bool = False


Model = Literal["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"]
AmountText = Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]{0,14}(\.[0-9]{1,2})?$")]
MAX_IMAGE_BYTES = 3_000_000
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class SettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    report_model: Model


class CapitalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_ref: str = Field(min_length=1, max_length=200)
    # One account can trade in both currencies (Kiwoom: US and Korean stocks).
    currency: Literal["USD", "KRW"]
    effective_from: date
    amount: AmountText


class DraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include_note: bool = True


class ImageUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["entry", "exit", "context"]
    png_base64: str = Field(min_length=8, max_length=MAX_IMAGE_BYTES * 2)


class ReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: Literal["en", "ko"] = "ko"
    images: list[ImageUpload] = Field(default_factory=list, max_length=3)


class QuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=4000)
    language: Literal["en", "ko"] = "ko"


class DrawnImage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_use_id: str = Field(min_length=1, max_length=200)
    png_base64: str = Field(min_length=8, max_length=MAX_IMAGE_BYTES * 2)


class DrawnImagesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    images: list[DrawnImage] = Field(min_length=1, max_length=6)


class PrincipleTextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=8000)
    language: Literal["en", "ko"] = "ko"


class PrincipleItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=40)
    value: str | None = Field(default=None, max_length=40)
    text: str = Field(default="", max_length=500)


class PrinciplesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[PrincipleItem] = Field(max_length=100)
    source_text: str = Field(default="", max_length=8000)


class _TradeIndex:
    """Fills and trades kept between requests: reading ~100k broker orders takes seconds.

    Fills are rebuilt when the journal gains a record, trades when fills or the review
    store's grouping (assignments, rules, groups) change.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._journal_revision: tuple[int, ...] | None = None
        self._fills: dict[str, list[Fill]] = {}
        self._undated: dict[str, int] = {}
        self._by_ref: dict[str, Fill] = {}
        self._trades_key: tuple[Any, ...] | None = None
        self._trades: list[Trade] = []
        self._by_id: dict[str, Trade] = {}

    def _refresh_fills(self, journal: JournalStore) -> tuple[int, ...]:
        revision = journal.execution_revision()
        if revision != self._journal_revision:
            sources = {
                "manual": journal.manual_executions(),
                "imported": journal.imported_executions(),
                "broker": journal.broker_executions(),
            }
            for ledger, documents in sources.items():
                self._fills[ledger], self._undated[ledger] = fills_of(ledger, documents)
            self._by_ref = {fill.ref: fill for fills in self._fills.values() for fill in fills}
            self._journal_revision = revision
        return revision

    def fills(self, journal: JournalStore) -> dict[str, Fill]:
        with self._lock:
            self._refresh_fills(journal)
            return self._by_ref

    def trades(
        self, journal: JournalStore, review: ReviewStore
    ) -> tuple[list[Trade], dict[str, Trade], dict[str, int]]:
        with self._lock:
            revision = self._refresh_fills(journal)
            key = (revision, review.grouping_version)
            if key != self._trades_key:
                trades = build_trades(
                    (fill for fills in self._fills.values() for fill in fills),
                    review.assignments(),
                    review.rules(),
                    review.groups(),
                )
                trades.sort(key=last_key, reverse=True)
                self._trades = trades
                self._by_id = {trade.trade_id: trade for trade in trades}
                self._trades_key = key
            return self._trades, self._by_id, dict(self._undated)


def register_review_routes(
    application: FastAPI,
    *,
    journal: Callable[[], JournalStore | None],
    store: Callable[[], ReviewStore | None],
    strategies: Strategies,
    error_response: ErrorResponse,
    get_bars: Callable[[], GetBars] | None = None,
    api_key: Callable[[], str | None] = lambda: None,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ask: Callable[..., dict[str, Any]] = ask_for_report,
    converse: Callable[..., dict[str, Any]] = call_claude,
    convert: Callable[..., dict[str, Any]] = convert_principles,
    resolve_symbol: Callable[[str], str | None] = lambda _symbol: None,
) -> None:
    index = _TradeIndex()

    def unavailable(request: Request) -> JSONResponse:
        return error_response(
            request,
            503,
            "review_storage_unconfigured",
            "Set SIW_DATA_DIR to review trades; nothing was read or written",
        )

    def respond(document: dict[str, Any], status_code: int = 200) -> JSONResponse:
        validate_document(document)
        return JSONResponse(status_code=status_code, content=document)

    def unknown_refs(ledgers: JournalStore, refs: list[str]) -> list[str]:
        known = index.fills(ledgers)
        return [ref for ref in refs if ref not in known]

    def strategy_list() -> list[dict[str, Any]]:
        return strategies() or []

    @application.get("/v1/review/trades", response_model=None)
    def list_trades(
        request: Request,
        ledger: Ledger = "broker",
        instrument_id: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
        strategy_id: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
        status: Literal["open", "closed", "incomplete"] | None = None,
        reviewed: bool | None = None,
        tag: Annotated[str | None, Query(min_length=1, max_length=32)] = None,
        limit: Annotated[int, Query(ge=1, le=1000)] = 300,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> Response:
        ledgers, review = journal(), store()
        if ledgers is None or review is None:
            return unavailable(request)
        trades, _by_id, undated = index.trades(ledgers, review)
        notes = review.annotations()
        rules = review.rules()
        chosen: list[Trade] = []
        for trade in trades:
            if trade.ledger != ledger:
                continue
            if instrument_id is not None and trade.instrument_id != instrument_id:
                continue
            if strategy_id is not None and (trade.strategy_id or "none") != strategy_id:
                continue
            if status is not None and status_of(trade) != status:
                continue
            note = notes.get(trade.trade_id)
            if reviewed is not None and bool(note and note["reviewed"]) != reviewed:
                continue
            if tag is not None and (note is None or tag not in note["tags"]):
                continue
            chosen.append(trade)
        documents = []
        for trade in chosen[offset : offset + limit]:
            document = trade_document(trade)
            note = notes.get(trade.trade_id)
            document["annotation"] = note
            documents.append(document)
        tags = sorted({tag for note in notes.values() for tag in note["tags"]})
        return respond(
            {
                "kind": "review_trade_list",
                "schema_version": "0.1",
                "ledger": ledger,
                "total": len(chosen),
                "offset": offset,
                "undated_execution_count": undated.get(ledger, 0),
                "strategies": [
                    {
                        "strategy_id": item["strategy_id"],
                        "name": item["name"],
                        "rule": rules.get(item["strategy_id"], "flat"),
                    }
                    for item in strategy_list()
                ],
                "tags": tags,
                "trades": documents,
            }
        )

    @application.put("/v1/review/assignments", response_model=None)
    def assign(request: Request, body: AssignmentRequest) -> Response:
        ledgers, review = journal(), store()
        if ledgers is None or review is None:
            return unavailable(request)
        refs = [item.ref for item in body.executions]
        missing = unknown_refs(ledgers, refs)
        if missing:
            return error_response(request, 404, "execution_not_found", f"No execution {missing[0]}")
        if body.strategy_id is not None and body.strategy_id not in {
            item["strategy_id"] for item in strategy_list()
        }:
            return error_response(
                request, 404, "strategy_not_found", f"No strategy {body.strategy_id}"
            )
        updated = review.assign(refs, body.strategy_id)
        return respond(
            {
                "kind": "review_assignment_result",
                "schema_version": "0.1",
                "strategy_id": body.strategy_id,
                "updated": updated,
            }
        )

    @application.put("/v1/review/strategy-rules/{strategy_id}", response_model=None)
    def set_rule(request: Request, strategy_id: str, body: RuleRequest) -> Response:
        review = store()
        if review is None:
            return unavailable(request)
        if strategy_id not in {item["strategy_id"] for item in strategy_list()}:
            return error_response(request, 404, "strategy_not_found", f"No strategy {strategy_id}")
        review.set_rule(strategy_id, body.rule)
        return respond(
            {
                "kind": "review_strategy_rule",
                "schema_version": "0.1",
                "strategy_id": strategy_id,
                "rule": body.rule,
            }
        )

    @application.post("/v1/review/groups", response_model=None)
    def make_group(request: Request, body: GroupRequest) -> Response:
        ledgers, review = journal(), store()
        if ledgers is None or review is None:
            return unavailable(request)
        refs = [item.ref for item in body.executions]
        missing = unknown_refs(ledgers, refs)
        if missing:
            return error_response(request, 404, "execution_not_found", f"No execution {missing[0]}")
        fills = index.fills(ledgers)
        streams = {(fills[ref].ledger, fills[ref].book, fills[ref].instrument_id) for ref in refs}
        if len(streams) > 1:
            return error_response(
                request,
                422,
                "invalid_review_group",
                "A trade groups fills of one ledger, account and instrument",
            )
        group_id = review.make_group(refs)
        return respond(
            {
                "kind": "review_group",
                "schema_version": "0.1",
                "group_id": group_id,
                "execution_count": len(set(refs)),
            },
            201,
        )

    @application.delete("/v1/review/groups/{group_id}", response_model=None)
    def remove_group(request: Request, group_id: str) -> Response:
        review = store()
        if review is None:
            return unavailable(request)
        try:
            review.remove_group(group_id)
        except GroupNotFoundError:
            return error_response(request, 404, "review_group_not_found", f"No group {group_id}")
        return Response(status_code=204)

    @application.put("/v1/review/trades/{trade_id:path}/annotation", response_model=None)
    def annotate(request: Request, trade_id: str, body: AnnotationRequest) -> Response:
        ledgers, review = journal(), store()
        if ledgers is None or review is None:
            return unavailable(request)
        _trades, by_id, _undated = index.trades(ledgers, review)
        if trade_id not in by_id:
            return error_response(request, 404, "trade_not_found", f"No trade {trade_id}")
        tags = list(dict.fromkeys(body.tags))
        note = review.annotate(trade_id, body.note, tags, body.reviewed)
        return respond(
            {"kind": "review_annotation", "schema_version": "0.1", "trade_id": trade_id, **note}
        )

    # --- report settings and account capital (step 2a) --------------------------------

    def settings_document(review: ReviewStore) -> dict[str, Any]:
        return {
            "kind": "review_settings",
            "schema_version": "0.1",
            "report_model": review.setting("report_model") or DEFAULT_MODEL,
            "models": list(MODELS),
        }

    @application.get("/v1/review/settings", response_model=None)
    def get_settings(request: Request) -> Response:
        review = store()
        if review is None:
            return unavailable(request)
        return respond(settings_document(review))

    @application.put("/v1/review/settings", response_model=None)
    def put_settings(request: Request, body: SettingsRequest) -> Response:
        review = store()
        if review is None:
            return unavailable(request)
        review.set_setting("report_model", body.report_model)
        return respond(settings_document(review))

    def capital_document(ledgers: JournalStore, review: ReviewStore) -> dict[str, Any]:
        fills = index.fills(ledgers).values()
        accounts = sorted(
            {
                (fill.book, instrument_currency(fill.instrument_id))
                for fill in fills
                if fill.ledger != "manual"
            }
        )
        return {
            "kind": "review_account_capital_list",
            "schema_version": "0.1",
            "accounts": [
                {"account_ref": book.split(":", 1)[1], "currency": currency}
                for book, currency in accounts
            ],
            "entries": review.capital(),
        }

    @application.get("/v1/review/account-capital", response_model=None)
    def list_capital(request: Request) -> Response:
        ledgers, review = journal(), store()
        if ledgers is None or review is None:
            return unavailable(request)
        return respond(capital_document(ledgers, review))

    @application.post("/v1/review/account-capital", response_model=None)
    def add_capital(request: Request, body: CapitalRequest) -> Response:
        ledgers, review = journal(), store()
        if ledgers is None or review is None:
            return unavailable(request)
        known = {
            (item["account_ref"], item["currency"])
            for item in capital_document(ledgers, review)["accounts"]
        }
        if (body.account_ref, body.currency) not in known:
            return error_response(
                request,
                404,
                "account_not_found",
                f"No {body.currency} fills of account {body.account_ref}",
            )
        review.add_capital(
            body.account_ref, body.effective_from.isoformat(), body.amount, body.currency
        )
        return respond(capital_document(ledgers, review), 201)

    @application.delete("/v1/review/account-capital/{entry_id}", response_model=None)
    def remove_capital(request: Request, entry_id: str) -> Response:
        review = store()
        if review is None:
            return unavailable(request)
        try:
            review.remove_capital(entry_id)
        except CapitalNotFoundError:
            return error_response(request, 404, "capital_not_found", f"No entry {entry_id}")
        return Response(status_code=204)

    # --- single-trade report (step 2a) -------------------------------------------------

    def utc(moment: datetime) -> str:
        return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    @application.post("/v1/review/trades/{trade_id:path}/report-drafts", response_model=None)
    def make_draft(request: Request, trade_id: str, body: DraftRequest) -> Response:
        ledgers, review = journal(), store()
        if ledgers is None or review is None or get_bars is None:
            return unavailable(request)
        _trades, by_id, _undated = index.trades(ledgers, review)
        trade = by_id.get(trade_id)
        if trade is None:
            return error_response(request, 404, "trade_not_found", f"No trade {trade_id}")
        document = trade_document(trade)
        capital = [
            Capital(
                entry["account_ref"],
                date.fromisoformat(entry["effective_from"]),
                Decimal(entry["amount"]),
                entry["currency"],
            )
            for entry in review.capital()
        ]
        try:
            evidence, images = report_evidence(
                get_bars(), trade, document["account_ref"], capital, now()
            )
        except MarketDataError as exc:
            return error_response(request, 422, "agent_data_unavailable", str(exc))
        try:
            principles = principle_check(ledgers, review, trade, capital)
        except MarketDataError as exc:
            return error_response(request, 422, "agent_data_unavailable", str(exc))
        note = review.annotations().get(trade_id) if body.include_note else None
        draft = {
            "kind": "review_report_draft",
            "schema_version": "0.1",
            "draft_id": f"draft-{uuid.uuid4().hex[:12]}",
            "trade_id": trade_id,
            "created_at_utc": utc(now()),
            "model": review.setting("report_model") or DEFAULT_MODEL,
            "agent_version": evidence.agent_version,
            # What Claude is told about the trade: no account or record ids.
            "trade": {
                "instrument_id": trade.instrument_id,
                "currency": instrument_currency(trade.instrument_id),
                "rule": trade.rule,
                "status": document["status"],
                "opened_on": document["opened_on"],
                "closed_on": document["closed_on"],
                "time_uncertain": document["time_uncertain"],
            },
            "facts": [fact.document() for fact in evidence.facts],
            "missing": list(dict.fromkeys(evidence.missing)),
            "uncertainty": list(dict.fromkeys(evidence.uncertainty)),
            "warnings": list(dict.fromkeys(evidence.warnings)),
            "dataset_revisions": sorted(evidence.dataset_revisions),
            "note": None
            if not note or not (note["note"] or note["tags"])
            else {
                "text": note["note"],
                "tags": note["tags"],
                "written_at_utc": note["recorded_at_utc"],
            },
            "images": images,
            "principles": principles,
        }
        validate_document(draft)
        review.save_draft(draft)
        return respond(draft, 201)

    @application.post("/v1/review/report-drafts/{draft_id}/report", response_model=None)
    def send_report(request: Request, draft_id: str, body: ReportRequest) -> Response:
        review = store()
        if review is None:
            return unavailable(request)
        try:
            draft = review.draft(draft_id)
        except DraftNotFoundError:
            return error_response(request, 404, "draft_not_found", f"No draft {draft_id}")
        expected = [image["kind"] for image in draft["images"]]
        if [image.kind for image in body.images] != expected:
            return error_response(
                request,
                422,
                "invalid_report_images",
                f"Send the draft's images in its order: {', '.join(expected) or 'none'}",
            )
        sent: list[tuple[str, str]] = []
        hashes = []
        for image in body.images:
            try:
                raw = base64.b64decode(image.png_base64, validate=True)
            except (binascii.Error, ValueError):
                return error_response(
                    request, 422, "invalid_report_images", f"Image {image.kind} is not base64"
                )
            if not raw.startswith(PNG_MAGIC) or len(raw) > MAX_IMAGE_BYTES:
                return error_response(
                    request,
                    422,
                    "invalid_report_images",
                    f"Image {image.kind} must be a PNG of at most {MAX_IMAGE_BYTES} bytes",
                )
            sent.append((image.kind, image.png_base64))
            hashes.append(
                {"kind": image.kind, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
            )
        key = api_key()
        if key is None:
            return error_response(
                request,
                409,
                "agent_key_missing",
                "Register an enabled Claude (Anthropic) API key on the Keys screen",
            )
        try:
            answer = ask(draft, sent, key, body.language, model=draft["model"], now=now)
        except ReportError as exc:
            return error_response(request, 502, "report_failed", str(exc))
        report = {
            "kind": "review_trade_report",
            "schema_version": "0.1",
            "report_id": f"report-{uuid.uuid4().hex[:12]}",
            "draft_id": draft_id,
            "trade_id": draft["trade_id"],
            **answer,
            "images": hashes,
        }
        validate_document(report)
        review.save_report(report)
        review.save_report_images(
            report["report_id"],
            [(kind, base64.b64decode(data)) for kind, data in sent],
        )
        return respond(report, 201)

    @application.get("/v1/review/trades/{trade_id:path}/reports", response_model=None)
    def list_reports(request: Request, trade_id: str) -> Response:
        review = store()
        if review is None:
            return unavailable(request)
        return respond(
            {
                "kind": "review_trade_report_list",
                "schema_version": "0.1",
                "trade_id": trade_id,
                "reports": review.reports(trade_id),
            }
        )

    # --- follow-up questions (step 2b) -------------------------------------------------

    def conversation_document(
        review: ReviewStore, report_id: str, conversation: dict[str, Any] | None
    ) -> dict[str, Any]:
        if conversation is None:
            return {
                "kind": "review_conversation",
                "schema_version": "0.1",
                "conversation_id": None,
                "report_id": report_id,
                "model": None,
                "status": "idle",
                "error": None,
                "events": [],
                "pending_images": [],
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                },
            }
        state = conversation["state"]
        pending = state.get("pending") or {}
        return {
            "kind": "review_conversation",
            "schema_version": "0.1",
            "conversation_id": conversation["conversation_id"],
            "report_id": report_id,
            "model": conversation["model"],
            "status": state.get("status", "idle"),
            "error": state.get("error"),
            "events": review.events(conversation["conversation_id"]),
            "pending_images": pending.get("images", []),
            "usage": {
                "input_tokens": state.get("input_tokens", 0),
                "output_tokens": state.get("output_tokens", 0),
                "cache_read_tokens": state.get("cache_read_tokens", 0),
                "cache_write_tokens": state.get("cache_write_tokens", 0),
            },
        }

    def runner(
        request: Request, review: ReviewStore, conversation: dict[str, Any]
    ) -> Conversation | JSONResponse:
        ledgers = journal()
        key = api_key()
        if ledgers is None or get_bars is None:
            return unavailable(request)
        if key is None:
            return error_response(
                request,
                409,
                "agent_key_missing",
                "Register an enabled Claude (Anthropic) API key on the Keys screen",
            )
        report = review.report(conversation["report_id"])
        if report is None:
            return error_response(request, 404, "report_not_found", "No such report")
        _trades, by_id, _undated = index.trades(ledgers, review)
        trade = by_id.get(report["trade_id"])
        if trade is None:
            return error_response(
                request,
                409,
                "trade_regrouped",
                "The report's trade no longer exists as grouped then (its rule or strategy "
                "changed); start a new report",
            )
        tools = ToolContext(get_bars(), trade, now(), resolve_symbol, lambda: strategy_list())

        def call(
            model: str, system: str, tool_list: list[dict[str, Any]], messages: list[Any]
        ) -> dict[str, Any]:
            return converse(model, system, tool_list, messages, key)

        return Conversation(review, conversation, tools, call, now)

    @application.get("/v1/review/reports/{report_id}/conversation", response_model=None)
    def get_conversation(request: Request, report_id: str) -> Response:
        review = store()
        if review is None:
            return unavailable(request)
        if review.report(report_id) is None:
            return error_response(request, 404, "report_not_found", "No such report")
        return respond(conversation_document(review, report_id, review.conversation_for(report_id)))

    @application.post("/v1/review/reports/{report_id}/conversation/messages", response_model=None)
    def ask_question(request: Request, report_id: str, body: QuestionRequest) -> Response:
        review = store()
        if review is None:
            return unavailable(request)
        report = review.report(report_id)
        if report is None:
            return error_response(request, 404, "report_not_found", "No such report")
        conversation = review.conversation_for(report_id)
        opening = None
        if conversation is None:
            # The same evidence the report was written from, then the report itself.
            draft = review.draft(report["draft_id"])
            opening = first_content(draft, report, review.report_images(report_id))
            conversation = review.start_conversation(
                report_id,
                report["model"] if report["model"] in MODELS else DEFAULT_MODEL,
                system_prompt(body.language),
                {"status": "idle"},
            )
        loop = runner(request, review, conversation)
        if isinstance(loop, JSONResponse):
            return loop
        try:
            loop.ask(body.question, opening)
        except ConversationError as exc:
            return error_response(request, 409, "conversation_busy", str(exc))
        return respond(conversation_document(review, report_id, review.conversation(loop.id)))

    @application.post("/v1/review/conversations/{conversation_id}/images", response_model=None)
    def add_images(request: Request, conversation_id: str, body: DrawnImagesRequest) -> Response:
        review = store()
        if review is None:
            return unavailable(request)
        try:
            conversation = review.conversation(conversation_id)
        except ConversationNotFoundError:
            return error_response(request, 404, "conversation_not_found", "No such conversation")
        drawn: dict[str, str] = {}
        for image in body.images:
            try:
                raw = base64.b64decode(image.png_base64, validate=True)
            except (binascii.Error, ValueError):
                return error_response(request, 422, "invalid_report_images", "Not base64")
            if not raw.startswith(PNG_MAGIC) or len(raw) > MAX_IMAGE_BYTES:
                return error_response(
                    request, 422, "invalid_report_images", "Each image must be a PNG"
                )
            drawn[image.tool_use_id] = image.png_base64
        loop = runner(request, review, conversation)
        if isinstance(loop, JSONResponse):
            return loop
        try:
            loop.add_images(drawn)
        except ConversationError as exc:
            return error_response(request, 409, "conversation_busy", str(exc))
        return respond(
            conversation_document(review, conversation["report_id"], review.conversation(loop.id))
        )

    # --- trading principles (step 3a) --------------------------------------------------

    def capital_entries(review: ReviewStore) -> list[Capital]:
        return [
            Capital(
                entry["account_ref"],
                date.fromisoformat(entry["effective_from"]),
                Decimal(entry["amount"]),
                entry["currency"],
            )
            for entry in review.capital()
        ]

    def principle_check(
        ledgers: JournalStore, review: ReviewStore, trade: Trade, capital: list[Capital]
    ) -> dict[str, Any] | None:
        """The trade against the principles in effect when it opened (None: no principles)."""
        if get_bars is None:
            return None
        document = trade_document(trade)
        # When it opened: the first fill's time, or the start of its day when only that.
        opened_text = document["opened_at_utc"] or f"{document['opened_on']}T00:00:00Z"
        opened = datetime.fromisoformat(opened_text.replace("Z", "+00:00"))
        version, after = version_for(review.principle_versions(), opened)
        if version is None:
            return None
        trades, _by_id, _undated = index.trades(ledgers, review)
        same_day = sum(
            1
            for other in trades
            if other.ledger == trade.ledger
            and min(p.fill.day for p in other.allocations).isoformat() == document["opened_on"]
        )
        results = check_trade(
            get_bars(),
            trade,
            version["items"],
            document["account_ref"],
            capital,
            same_day,
            now(),
        )
        return {"version": version["version"], "after_the_fact": after, "results": results}

    def principles_document(review: ReviewStore) -> dict[str, Any]:
        versions = review.principle_versions()
        current = versions[-1] if versions else None
        return {
            "kind": "review_principles",
            "schema_version": "0.1",
            "current": current,
            "versions": [
                {
                    "version": v["version"],
                    "created_at_utc": v["created_at_utc"],
                    "item_count": len(v["items"]),
                }
                for v in versions
            ],
        }

    @application.get("/v1/review/principles", response_model=None)
    def get_principles(request: Request) -> Response:
        review = store()
        if review is None:
            return unavailable(request)
        return respond(principles_document(review))

    @application.post("/v1/review/principles/draft", response_model=None)
    def draft_principles(request: Request, body: PrincipleTextRequest) -> Response:
        review = store()
        if review is None:
            return unavailable(request)
        key = api_key()
        if key is None:
            return error_response(
                request,
                409,
                "agent_key_missing",
                "Register an enabled Claude (Anthropic) API key on the Keys screen",
            )
        listed = [{"strategy_id": s["strategy_id"], "name": s["name"]} for s in strategy_list()]
        try:
            proposed = convert(
                body.text,
                listed,
                key,
                body.language,
                model=review.setting("report_model") or DEFAULT_MODEL,
            )
        except ReportError as exc:
            return error_response(request, 502, "principles_conversion_failed", str(exc))
        return respond({"kind": "review_principle_draft", "schema_version": "0.1", **proposed})

    @application.put("/v1/review/principles", response_model=None)
    def save_principles(request: Request, body: PrinciplesRequest) -> Response:
        review = store()
        if review is None:
            return unavailable(request)
        try:
            items = clean_items(
                [item.model_dump() for item in body.items],
                {s["strategy_id"] for s in strategy_list()},
            )
        except PrincipleError as exc:
            return error_response(request, 422, "invalid_principles", str(exc))
        review.save_principles(items, body.source_text)
        return respond(principles_document(review), 201)

    @application.get("/v1/review/trades/{trade_id:path}/principle-check", response_model=None)
    def get_principle_check(request: Request, trade_id: str) -> Response:
        ledgers, review = journal(), store()
        if ledgers is None or review is None or get_bars is None:
            return unavailable(request)
        _trades, by_id, _undated = index.trades(ledgers, review)
        trade = by_id.get(trade_id)
        if trade is None:
            return error_response(request, 404, "trade_not_found", f"No trade {trade_id}")
        try:
            checked = principle_check(ledgers, review, trade, capital_entries(review))
        except MarketDataError as exc:
            return error_response(request, 422, "agent_data_unavailable", str(exc))
        return respond(
            {
                "kind": "review_principle_check",
                "schema_version": "0.1",
                "trade_id": trade_id,
                "version": None if checked is None else checked["version"],
                "after_the_fact": False if checked is None else checked["after_the_fact"],
                "results": [] if checked is None else checked["results"],
            }
        )
