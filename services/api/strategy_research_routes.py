"""Routes for strategy research with Claude (ADR 0042, strategy research step 2).

Conversations live in the review store's conversation tables under `strategy:<id>`. Claude's
scripts are run by the app, which posts each result back; profit goes to Claude only through
`/results`, which the investor sends.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import Response

from packages.contracts.validator import validate_document
from services.api.indicator_assistant_routes import ChartContext, RunRequest
from services.api.journal_routes import ErrorResponse
from services.assistant.indicator import MAX_REFERENCE, MAX_RUNS, call_indicator_claude
from services.assistant.strategy import (
    DEFAULT_MODEL,
    EFFORT,
    KEY_PREFIX,
    MAX_SENT_TRADES,
    RETRY_MODEL,
    StrategyConversation,
    system_prompt,
)
from services.review.conversation import ConversationError
from services.review.store import ConversationNotFoundError, ReviewStore

Decimal = Field(pattern=r"^-?[0-9]+(\.[0-9]+)?$", max_length=40)


class Trade(BaseModel):
    model_config = ConfigDict(extra="forbid")
    direction: Literal["long", "short"]
    qty: str = Decimal
    entry_time_utc: str = Field(max_length=30)
    entry_price: str = Decimal
    exit_time_utc: str | None = Field(default=None, max_length=30)
    exit_price: str | None = Field(default=None, pattern=r"^-?[0-9]+(\.[0-9]+)?$", max_length=40)
    profit: str = Decimal
    assumed: bool


class OpenPosition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    size: str = Field(max_length=40)
    average_price: str = Decimal
    open_profit: str = Decimal


class TradeDay(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    trades: int = Field(ge=1)


class Concentration(BaseModel):
    """Trades per exchange day over every trade, not only those sent."""

    model_config = ConfigDict(extra="forbid")
    days_with_trades: int = Field(ge=0)
    top_days: list[TradeDay] = Field(max_length=3)
    top3_share_percent: str = Decimal


class Results(BaseModel):
    """One backtest of a Pine strategy on the chart. Amounts are decimal text."""

    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=40)
    timeframe: str = Field(min_length=1, max_length=20)
    bars: int = Field(ge=0, le=1_000_000)
    from_utc: str = Field(max_length=30)
    to_utc: str = Field(max_length=30)
    version: int | None = Field(default=None, ge=1)
    currency: str = Field(max_length=10)
    initial_capital: str = Decimal
    net_profit: str = Decimal
    net_profit_percent: str = Decimal
    gross_profit: str = Decimal
    gross_loss: str = Decimal
    profit_factor: str | None = Field(default=None, max_length=40)
    closed_trades: int = Field(ge=0)
    win_trades: int = Field(ge=0)
    loss_trades: int = Field(ge=0)
    win_rate: str | None = Field(default=None, max_length=40)
    max_drawdown: str = Decimal
    commission: str = Decimal
    assumed_fills: int = Field(ge=0)
    skipped_orders: int = Field(ge=0)
    open_position: OpenPosition | None = None
    concentration: Concentration
    trades: list[Trade] = Field(max_length=MAX_SENT_TRADES)


class Origin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["saved", "tradingview"]
    detail: str | None = Field(default=None, max_length=4000)


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=4000)
    language: Literal["en", "ko"] = "ko"
    reference: str = Field(min_length=1, max_length=MAX_REFERENCE)
    context: ChartContext
    origin: Origin | None = None


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=4000)
    context: ChartContext
    # After five failed fixes: this request alone goes to Opus at high effort.
    retry: bool = False


class ResultsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=4000)
    context: ChartContext
    results: Results


class StrategyRunRequest(RunRequest):
    # Kept with the version for the investor; Claude does not get it from a run.
    results: Results | None = None


def register_strategy_research_routes(
    application: FastAPI,
    *,
    store: Callable[[], ReviewStore | None],
    error_response: ErrorResponse,
    api_key: Callable[[], str | None] = lambda: None,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    converse: Callable[..., dict[str, Any]] = call_indicator_claude,
) -> None:
    def unavailable(request: Request) -> JSONResponse:
        return error_response(
            request,
            503,
            "review_storage_unconfigured",
            "Set SIW_DATA_DIR to keep strategy conversations; nothing was read or written",
        )

    def respond(document: dict[str, Any], status_code: int = 200) -> JSONResponse:
        validate_document(document)
        return JSONResponse(status_code=status_code, content=document)

    def document(review: ReviewStore, conversation: dict[str, Any]) -> dict[str, Any]:
        state = conversation["state"]
        pending = state.get("pending")
        return {
            "kind": "strategy_conversation",
            "schema_version": "0.1",
            "conversation_id": conversation["conversation_id"],
            "model": conversation["model"],
            "status": state.get("status", "idle"),
            "error": state.get("error"),
            "events": review.events(conversation["conversation_id"]),
            "pending_run": None
            if not pending
            else {"tool_use_id": pending["tool_use_id"], "code": pending["code"]},
            "runs": state.get("runs", 0),
            "max_runs": MAX_RUNS,
            "usage": {
                "input_tokens": state.get("input_tokens", 0),
                "output_tokens": state.get("output_tokens", 0),
                "cache_read_tokens": state.get("cache_read_tokens", 0),
                "cache_write_tokens": state.get("cache_write_tokens", 0),
            },
        }

    def found(
        request: Request, conversation_id: str
    ) -> tuple[ReviewStore, dict[str, Any]] | JSONResponse:
        review = store()
        if review is None:
            return unavailable(request)
        try:
            conversation = review.conversation(conversation_id)
        except ConversationNotFoundError:
            conversation = None
        if conversation is None or not conversation["report_id"].startswith(KEY_PREFIX):
            return error_response(
                request, 404, "conversation_not_found", "No such strategy conversation"
            )
        return review, conversation

    def missing_key(request: Request) -> JSONResponse:
        return error_response(
            request,
            409,
            "agent_key_missing",
            "Register an enabled Claude (Anthropic) API key on the Keys screen",
        )

    def loop(
        request: Request, review: ReviewStore, conversation: dict[str, Any]
    ) -> StrategyConversation | JSONResponse:
        key = api_key()
        if key is None:
            return missing_key(request)

        def call(
            model: str,
            system: str,
            tools: list[dict[str, Any]],
            messages: list[Any],
            effort: str,
        ) -> dict[str, Any]:
            return converse(model, system, tools, messages, key, effort=effort)

        return StrategyConversation(review, conversation, call, now)

    @application.post("/v1/strategy-research/conversations", response_model=None)
    def start(request: Request, body: StartRequest) -> Response:
        review = store()
        if review is None:
            return unavailable(request)
        if api_key() is None:
            return missing_key(request)
        conversation = review.start_conversation(
            f"{KEY_PREFIX}{uuid.uuid4().hex[:12]}",
            DEFAULT_MODEL,
            system_prompt(body.language, body.reference),
            {"status": "idle"},
        )
        runner = loop(request, review, conversation)
        if isinstance(runner, JSONResponse):
            return runner
        runner.ask(
            body.question,
            body.context.model_dump(),
            origin=body.origin.model_dump(exclude_none=True) if body.origin else None,
        )
        return respond(document(review, review.conversation(runner.id)), 201)

    @application.get("/v1/strategy-research/conversations/{conversation_id}", response_model=None)
    def get_conversation(request: Request, conversation_id: str) -> Response:
        result = found(request, conversation_id)
        if isinstance(result, JSONResponse):
            return result
        review, conversation = result
        return respond(document(review, conversation))

    @application.post(
        "/v1/strategy-research/conversations/{conversation_id}/messages", response_model=None
    )
    def ask(request: Request, conversation_id: str, body: AskRequest) -> Response:
        result = found(request, conversation_id)
        if isinstance(result, JSONResponse):
            return result
        review, conversation = result
        runner = loop(request, review, conversation)
        if isinstance(runner, JSONResponse):
            return runner
        try:
            runner.ask(
                body.question,
                body.context.model_dump(),
                RETRY_MODEL if body.retry else None,
                EFFORT if body.retry else None,
            )
        except ConversationError as exc:
            return error_response(request, 409, "conversation_busy", str(exc))
        return respond(document(review, review.conversation(runner.id)))

    @application.post(
        "/v1/strategy-research/conversations/{conversation_id}/results", response_model=None
    )
    def send_results(request: Request, conversation_id: str, body: ResultsRequest) -> Response:
        result = found(request, conversation_id)
        if isinstance(result, JSONResponse):
            return result
        review, conversation = result
        runner = loop(request, review, conversation)
        if isinstance(runner, JSONResponse):
            return runner
        try:
            runner.send_results(body.question, body.context.model_dump(), body.results.model_dump())
        except ConversationError as exc:
            return error_response(request, 409, "conversation_busy", str(exc))
        return respond(document(review, review.conversation(runner.id)))

    @application.post(
        "/v1/strategy-research/conversations/{conversation_id}/runs", response_model=None
    )
    def add_run(request: Request, conversation_id: str, body: StrategyRunRequest) -> Response:
        result = found(request, conversation_id)
        if isinstance(result, JSONResponse):
            return result
        review, conversation = result
        runner = loop(request, review, conversation)
        if isinstance(runner, JSONResponse):
            return runner
        try:
            runner.add_run(
                body.tool_use_id,
                body.ok,
                body.error,
                body.summary,
                results=body.results.model_dump() if body.results else None,
            )
        except ConversationError as exc:
            return error_response(request, 409, "conversation_busy", str(exc))
        return respond(document(review, review.conversation(runner.id)))
