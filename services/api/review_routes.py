"""Trade review routes (ADR 0042): trades grouped from the journal, with labels.

Journal records are only read. Strategy assignments, grouping rules, manual groups and
notes are written to the review store.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from starlette.responses import Response

from packages.contracts.validator import validate_document
from services.api.journal_routes import ErrorResponse
from services.journal.store import JournalStore
from services.review.store import GroupNotFoundError, ReviewStore
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
