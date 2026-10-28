"""Trade review routes (ADR 0042): trades grouped from the journal, with labels.

Journal records are only read. Strategy assignments, grouping rules, manual groups and
notes are written to the review store.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import threading
import uuid
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
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
from services.review.checker import CheckRunner
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
    period_system_prompt,
    system_prompt,
)
from services.review.period import Row, period_sections, rows_for
from services.review.period_ai import (
    PERIOD_TOOLS,
    PeriodTools,
    ask_period_commentary,
    best_and_worst,
    period_payload,
)
from services.review.principles import PrincipleError, check_trade, clean_items, version_for
from services.review.report import Capital, report_evidence
from services.review.store import (
    CapitalNotFoundError,
    ConversationNotFoundError,
    DraftNotFoundError,
    GroupNotFoundError,
    PeriodReviewNotFoundError,
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
from services.review.whatif import principles_what_if, strategy_what_if, suggest_strategies

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


AUTO_CHECK_DAYS = 90


class PeriodRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_date: date
    to_date: date
    ledger: Ledger = "broker"
    account_ref: str | None = Field(default=None, min_length=1, max_length=200)
    # A strategy id, or "none" for trades without one.
    strategy_id: str | None = Field(default=None, min_length=1, max_length=200)


MAX_PERIOD_DAYS = 400


class CommentaryLanguage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: Literal["en", "ko"] = "ko"


class CheckRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_date: date
    to_date: date


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
    period_ask: Callable[..., dict[str, Any]] = ask_period_commentary,
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
        principles: Literal["broken", "kept", "unchecked"] | None = None,
        limit: Annotated[int, Query(ge=1, le=1000)] = 300,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> Response:
        ledgers, review = journal(), store()
        if ledgers is None or review is None:
            return unavailable(request)
        trades, _by_id, undated = index.trades(ledgers, review)
        notes = review.annotations()
        rules = review.rules()
        broken_counts = stored_checks(review)
        queue_recent(ledgers, review)
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
            if principles is not None:
                broken = broken_counts.get(trade.trade_id)
                if principles == "unchecked" and broken is not None:
                    continue
                if principles == "broken" and not broken:
                    continue
                if principles == "kept" and broken != 0:
                    continue
            chosen.append(trade)
        documents = []
        for trade in chosen[offset : offset + limit]:
            document = trade_document(trade)
            note = notes.get(trade.trade_id)
            document["annotation"] = note
            document["principles_broken"] = broken_counts.get(trade.trade_id)
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
        try:
            what_if = what_ifs(review, trade, None)
        except MarketDataError as exc:
            what_if = None
            evidence.warnings.append(f"What-ifs were not computed: {exc}")
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
            "what_if": what_if,
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
        same_day = opened_per_day(trades).get((trade.ledger, document["opened_on"]), 0)
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

    day_counts: dict[int, dict[tuple[str, str], int]] = {}

    def opened_per_day(trades: list[Trade]) -> dict[tuple[str, str], int]:
        """Trades opened per (ledger, day), counted once per grouping of the trades."""
        key = id(trades)
        if key not in day_counts:
            counts: dict[tuple[str, str], int] = {}
            for other in trades:
                day = min(p.fill.day for p in other.allocations).isoformat()
                counts[(other.ledger, day)] = counts.get((other.ledger, day), 0) + 1
            day_counts.clear()  # only the current grouping is kept
            day_counts[key] = counts
        return day_counts[key]

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
            checked = check_with_what_if(ledgers, review, trade)
        except MarketDataError as exc:
            return error_response(request, 422, "agent_data_unavailable", str(exc))
        key = basis(review, trade, review.principle_versions())
        if checked is not None and key is not None:
            review.save_check(trade_id, key, checked)
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

    # --- stored checks, what-ifs and strategy suggestions (step 3b) --------------------

    def capital_key(review: ReviewStore) -> str:
        return hashlib.sha256(json.dumps(review.capital(), sort_keys=True).encode()).hexdigest()[
            :12
        ]

    def basis(
        review: ReviewStore,
        trade: Trade,
        versions: list[dict[str, Any]],
        capital: str | None = None,
    ) -> str | None:
        """What a stored check depends on; None when there are no principles."""
        if not versions:
            return None
        fills = sorted(p.fill.ref for p in trade.allocations)
        return (
            f"v{versions[-1]['version']}|c{capital or capital_key(review)}|s{trade.strategy_id}|"
            f"{len(fills)}:{fills[-1]}:{trade.open_quantity}"
        )

    def stored_checks(review: ReviewStore) -> dict[str, int]:
        """Broken counts of stored checks that are still current (by their basis)."""
        ledgers = journal()
        if ledgers is None:
            return {}
        versions = review.principle_versions()
        if not versions:
            return {}
        _trades, by_id, _undated = index.trades(ledgers, review)
        capital = capital_key(review)
        current = {}
        for trade_id, (stored, broken) in review.check_summaries().items():
            trade = by_id.get(trade_id)
            if trade is not None and stored == basis(review, trade, versions, capital):
                current[trade_id] = broken
        return current

    def check_one(trade_id: str) -> None:
        ledgers, review = journal(), store()
        if ledgers is None or review is None:
            return
        _trades, by_id, _undated = index.trades(ledgers, review)
        trade = by_id.get(trade_id)
        if trade is None:
            return  # regrouped since it was queued
        versions = review.principle_versions()
        key = basis(review, trade, versions)
        if key is None:
            return
        checked = check_with_what_if(ledgers, review, trade)
        if checked is not None:
            review.save_check(trade_id, key, checked)

    def check_with_what_if(
        ledgers: JournalStore, review: ReviewStore, trade: Trade
    ) -> dict[str, Any] | None:
        """The principles check, with the principles what-if riding along so period
        reviews can total it (step 4a). What is stored is this whole document."""
        checked = principle_check(ledgers, review, trade, capital_entries(review))
        if checked is None or get_bars is None:
            return checked
        version = next(v for v in review.principle_versions() if v["version"] == checked["version"])
        try:
            checked["what_if"] = principles_what_if(get_bars(), trade, version["items"], now())
        except MarketDataError:
            checked["what_if"] = None
        return checked

    checks = CheckRunner(check_one)

    def queue_stale(ledgers: JournalStore, review: ReviewStore, first: date, last: date) -> int:
        if get_bars is None:
            return 0
        versions = review.principle_versions()
        if not versions:
            return 0
        trades, _by_id, _undated = index.trades(ledgers, review)
        stored = review.check_summaries()
        capital = capital_key(review)
        stale = []
        for trade in trades:
            opened = min(p.fill.day for p in trade.allocations)
            if not first <= opened <= last:
                continue
            key = basis(review, trade, versions, capital)
            if stored.get(trade.trade_id, ("", 0))[0] != key:
                stale.append(trade.trade_id)
        return checks.enqueue(stale)

    def queue_recent(ledgers: JournalStore, review: ReviewStore) -> None:
        today = now().date()
        queue_stale(ledgers, review, today - timedelta(days=AUTO_CHECK_DAYS), today)

    def status_document() -> dict[str, Any]:
        return {
            "kind": "review_check_status",
            "schema_version": "0.1",
            "auto_from_date": (now().date() - timedelta(days=AUTO_CHECK_DAYS)).isoformat(),
            **checks.status(),
        }

    @application.get("/v1/review/principle-checks/status", response_model=None)
    def check_status(request: Request) -> Response:
        if store() is None:
            return unavailable(request)
        return respond(status_document())

    @application.post("/v1/review/principle-checks/run", response_model=None)
    def run_checks(request: Request, body: CheckRunRequest) -> Response:
        ledgers, review = journal(), store()
        if ledgers is None or review is None or get_bars is None:
            return unavailable(request)
        if body.to_date < body.from_date:
            return error_response(
                request, 422, "invalid_check_range", "to_date is before from_date"
            )
        queue_stale(ledgers, review, body.from_date, body.to_date)
        return respond(status_document(), 202)

    def trade_or_error(
        request: Request, trade_id: str
    ) -> tuple[JournalStore, ReviewStore, Trade] | Response:
        ledgers, review = journal(), store()
        if ledgers is None or review is None or get_bars is None:
            return unavailable(request)
        _trades, by_id, _undated = index.trades(ledgers, review)
        trade = by_id.get(trade_id)
        if trade is None:
            return error_response(request, 404, "trade_not_found", f"No trade {trade_id}")
        return ledgers, review, trade

    def what_ifs(review: ReviewStore, trade: Trade, strategy_id: str | None) -> dict[str, Any]:
        assert get_bars is not None
        document = trade_document(trade)
        opened_text = document["opened_at_utc"] or f"{document['opened_on']}T00:00:00Z"
        opened = datetime.fromisoformat(opened_text.replace("Z", "+00:00"))
        version, _after = version_for(review.principle_versions(), opened)
        principles_part = (
            None
            if version is None
            else principles_what_if(get_bars(), trade, version["items"], now())
        )
        chosen = strategy_id or trade.strategy_id
        found = next((s for s in strategy_list() if s["strategy_id"] == chosen), None)
        strategy_part = None if found is None else strategy_what_if(get_bars(), trade, found, now())
        return {"principles": principles_part, "strategy": strategy_part}

    @application.get("/v1/review/trades/{trade_id:path}/what-if", response_model=None)
    def get_what_if(
        request: Request,
        trade_id: str,
        strategy_id: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    ) -> Response:
        found = trade_or_error(request, trade_id)
        if isinstance(found, Response):
            return found
        _ledgers, review, trade = found
        try:
            parts = what_ifs(review, trade, strategy_id)
        except MarketDataError as exc:
            return error_response(request, 422, "agent_data_unavailable", str(exc))
        return respond(
            {"kind": "review_what_if", "schema_version": "0.1", "trade_id": trade_id, **parts}
        )

    @application.get("/v1/review/trades/{trade_id:path}/strategy-suggestions", response_model=None)
    def get_suggestions(request: Request, trade_id: str) -> Response:
        found = trade_or_error(request, trade_id)
        if isinstance(found, Response):
            return found
        _ledgers, _review, trade = found
        assert get_bars is not None
        return respond(
            {
                "kind": "review_strategy_suggestions",
                "schema_version": "0.1",
                "trade_id": trade_id,
                "suggestions": suggest_strategies(get_bars(), trade, strategy_list()),
            }
        )

    # --- period reviews (step 4a) ------------------------------------------------------

    def current_check_documents(review: ReviewStore) -> dict[str, dict[str, Any]]:
        """Stored checks whose basis still matches the trade, by trade id."""
        ledgers = journal()
        versions = review.principle_versions()
        if ledgers is None or not versions:
            return {}
        _trades, by_id, _undated = index.trades(ledgers, review)
        capital = capital_key(review)
        current = {}
        for trade_id, (stored, document) in review.check_documents().items():
            trade = by_id.get(trade_id)
            if trade is not None and stored == basis(review, trade, versions, capital):
                current[trade_id] = document
        return current

    def period_rows(
        ledgers: JournalStore,
        review: ReviewStore,
        filters: dict[str, Any],
        first: date,
        last: date,
    ) -> list[Row]:
        """Closed trades of the filters closed between the dates."""
        trades, _by_id, _undated = index.trades(ledgers, review)
        account, strategy = filters["account_ref"], filters["strategy_id"]
        chosen = [
            t
            for t in trades
            if t.ledger == filters["ledger"]
            and (account is None or t.book.split(":", 1)[-1] == account)
            and (strategy is None or (t.strategy_id or "none") == strategy)
            # A closed trade closes on its last fill's day: skip the rest before formatting.
            and first <= max(p.fill.day for p in t.allocations) <= last
        ]
        return rows_for(chosen, first, last, review.annotations(), current_check_documents(review))

    def period_summary(document: dict[str, Any]) -> dict[str, Any]:
        return {
            "review_id": document["review_id"],
            "created_at_utc": document["created_at_utc"],
            "from_date": document["from_date"],
            "to_date": document["to_date"],
            "filters": document["filters"],
            "totals": [
                {
                    "currency": section["currency"],
                    "trades": section["summary"]["trades"],
                    "net_pnl": section["summary"]["net_pnl"],
                }
                for section in document["sections"]
            ],
        }

    @application.post("/v1/review/periods", response_model=None)
    def make_period_review(request: Request, body: PeriodRequest) -> Response:
        ledgers, review = journal(), store()
        if ledgers is None or review is None:
            return unavailable(request)
        if body.to_date < body.from_date:
            return error_response(request, 422, "invalid_period", "to_date is before from_date")
        length = (body.to_date - body.from_date).days + 1
        if length > MAX_PERIOD_DAYS:
            return error_response(
                request, 422, "invalid_period", f"a period covers at most {MAX_PERIOD_DAYS} days"
            )
        before_end = body.from_date - timedelta(days=1)
        before_start = before_end - timedelta(days=length - 1)
        filters = {
            "ledger": body.ledger,
            "account_ref": body.account_ref,
            "strategy_id": body.strategy_id,
        }
        rows = period_rows(ledgers, review, filters, body.from_date, body.to_date)
        previous = period_rows(ledgers, review, filters, before_start, before_end)
        versions = review.principle_versions()
        items = {i["item_id"]: i["text"] for i in (versions[-1]["items"] if versions else [])}
        # Trades not checked yet are queued, so a later review of the period has them.
        queue_stale(ledgers, review, before_start, body.to_date)
        document = {
            "kind": "review_period",
            "schema_version": "0.1",
            "review_id": f"period-{uuid.uuid4().hex[:12]}",
            "created_at_utc": utc(now()),
            "from_date": body.from_date.isoformat(),
            "to_date": body.to_date.isoformat(),
            "previous_from_date": before_start.isoformat(),
            "previous_to_date": before_end.isoformat(),
            "filters": filters,
            "principles_version": versions[-1]["version"] if versions else None,
            "sections": period_sections(rows, previous, items),
        }
        validate_document(document)
        review.save_period_review(document)
        return respond(document, 201)

    @application.get("/v1/review/periods", response_model=None)
    def list_period_reviews(request: Request) -> Response:
        review = store()
        if review is None:
            return unavailable(request)
        return respond(
            {
                "kind": "review_period_list",
                "schema_version": "0.1",
                "reviews": [period_summary(d) for d in review.period_reviews()],
            }
        )

    @application.get("/v1/review/periods/{review_id}", response_model=None)
    def get_period_review(request: Request, review_id: str) -> Response:
        review = store()
        if review is None:
            return unavailable(request)
        try:
            return respond(review.period_review(review_id))
        except PeriodReviewNotFoundError:
            return error_response(request, 404, "period_review_not_found", "No such review")

    # --- period commentary and follow-up questions (step 4b) ---------------------------

    def period_context(
        request: Request, review_id: str
    ) -> tuple[ReviewStore, dict[str, Any], list[Row], dict[str, Any]] | Response:
        """The saved review, its trades as they are now, and the payload for Claude."""
        ledgers, review = journal(), store()
        if ledgers is None or review is None:
            return unavailable(request)
        try:
            saved = review.period_review(review_id)
        except PeriodReviewNotFoundError:
            return error_response(request, 404, "period_review_not_found", "No such review")
        rows = period_rows(
            ledgers,
            review,
            saved["filters"],
            date.fromisoformat(saved["from_date"]),
            date.fromisoformat(saved["to_date"]),
        )
        versions = review.principle_versions()
        payload = period_payload(
            saved,
            versions[-1]["items"] if versions else [],
            best_and_worst(rows, review.annotations()),
        )
        return review, saved, rows, payload

    @application.get("/v1/review/periods/{review_id}/payload", response_model=None)
    def get_period_payload(request: Request, review_id: str) -> Response:
        found = period_context(request, review_id)
        if isinstance(found, Response):
            return found
        _review, _saved, _rows, payload = found
        return respond(
            {
                "kind": "review_period_payload",
                "schema_version": "0.1",
                "review_id": review_id,
                "payload": payload,
            }
        )

    @application.post("/v1/review/periods/{review_id}/commentary", response_model=None)
    def make_period_commentary(
        request: Request, review_id: str, body: CommentaryLanguage
    ) -> Response:
        found = period_context(request, review_id)
        if isinstance(found, Response):
            return found
        review, _saved, _rows, payload = found
        key = api_key()
        if key is None:
            return error_response(
                request,
                409,
                "agent_key_missing",
                "Register an enabled Claude (Anthropic) API key on the Keys screen",
            )
        try:
            answer = period_ask(
                payload,
                key,
                body.language,
                model=review.setting("report_model") or DEFAULT_MODEL,
                now=now,
            )
        except ReportError as exc:
            return error_response(request, 502, "report_failed", str(exc))
        document = {
            "kind": "review_period_commentary",
            "schema_version": "0.1",
            "review_id": review_id,
            **answer,
        }
        validate_document(document)
        review.add_period_commentary(review_id, document)
        return respond(document, 201)

    @application.get("/v1/review/periods/{review_id}/commentaries", response_model=None)
    def list_period_commentaries(request: Request, review_id: str) -> Response:
        review = store()
        if review is None:
            return unavailable(request)
        return respond(
            {
                "kind": "review_period_commentary_list",
                "schema_version": "0.1",
                "review_id": review_id,
                "commentaries": review.period_commentaries(review_id),
            }
        )

    def period_key(review_id: str) -> str:
        return f"period:{review_id}"

    @application.get("/v1/review/periods/{review_id}/conversation", response_model=None)
    def get_period_conversation(request: Request, review_id: str) -> Response:
        review = store()
        if review is None:
            return unavailable(request)
        key = period_key(review_id)
        return respond(conversation_document(review, key, review.conversation_for(key)))

    @application.post("/v1/review/periods/{review_id}/conversation/messages", response_model=None)
    def ask_period_question(request: Request, review_id: str, body: QuestionRequest) -> Response:
        found = period_context(request, review_id)
        if isinstance(found, Response):
            return found
        review, _saved, rows, payload = found
        claude_key = api_key()
        if claude_key is None:
            return error_response(
                request,
                409,
                "agent_key_missing",
                "Register an enabled Claude (Anthropic) API key on the Keys screen",
            )
        key = period_key(review_id)
        conversation = review.conversation_for(key)
        opening = None
        if conversation is None:
            latest = next(iter(review.period_commentaries(review_id)), None)
            context = {
                "period_review": payload,
                "your_commentary": None
                if latest is None
                else {
                    k: latest[k]
                    for k in (
                        "summary",
                        "repeated_mistakes",
                        "went_well",
                        "focus_next",
                        "principle_suggestions",
                    )
                },
            }
            opening = [{"type": "text", "text": json.dumps(context, ensure_ascii=False)}]
            conversation = review.start_conversation(
                key,
                review.setting("report_model") or DEFAULT_MODEL,
                period_system_prompt(body.language),
                {"status": "idle"},
            )

        def call(
            model: str, system: str, tools: list[dict[str, Any]], messages: list[Any]
        ) -> dict[str, Any]:
            return converse(model, system, tools, messages, claude_key)

        loop = Conversation(
            review,
            conversation,
            PeriodTools(rows, review.annotations()),
            call,
            now,
            specs=PERIOD_TOOLS,
        )
        try:
            loop.ask(body.question, opening)
        except ConversationError as exc:
            return error_response(request, 409, "conversation_busy", str(exc))
        return respond(conversation_document(review, key, review.conversation(loop.id)))
