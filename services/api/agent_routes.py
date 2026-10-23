"""Review, filings and dark pool Agents (ADR 0022, 0024). Read-only: writes no ledger."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from packages.contracts.validator import validate_document
from services.agents.commentary import DEFAULT_MODEL, CommentaryError, ask_claude, payload_for
from services.agents.dark_pool import dark_pool_review
from services.agents.evidence import (
    SettlementFees,
    backtest_review,
    chart_point,
    execution_of,
    trade_review,
)
from services.agents.filings import UnsupportedTargetError, filings_review
from services.agents.store import AgentStore, AnalysisNotFoundError
from services.api.journal_routes import ErrorResponse
from services.backtest.data import GetBars
from services.backtest.jobs import BacktestRunner
from services.backtest.store import BacktestNotFoundError
from services.journal.store import JournalStore
from services.market_data.provider import MarketDataError
from services.public_sources.http import SourceConfigError, SourceError
from services.public_sources.sources import PublicSources

Ledger = Literal["manual", "imported", "broker"]


class ChartReviewRequest(BaseModel):
    instrument_id: str = Field(min_length=1, max_length=200)
    timeframe: str = Field(pattern=r"^([1-9][0-9]{0,2}m|1d|1w)$")
    cutoff_utc: datetime


class ExecutionRef(BaseModel):
    ledger: Ledger
    execution_id: str = Field(min_length=1, max_length=200)


class TradeReviewRequest(BaseModel):
    instrument_id: str = Field(min_length=1, max_length=200)
    executions: list[ExecutionRef] = Field(min_length=1, max_length=200)


class BacktestReviewRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=200)


class FilingsReviewRequest(BaseModel):
    instrument_id: str = Field(min_length=1, max_length=200)
    cutoff_utc: datetime
    window_days: int = Field(default=30, ge=1, le=365)
    after_days: int = Field(default=0, ge=0, le=30)


class DarkPoolReviewRequest(BaseModel):
    instrument_id: str = Field(min_length=1, max_length=200)
    cutoff_utc: datetime
    weeks: int = Field(default=26, ge=4, le=104)


class CommentaryRequest(BaseModel):
    language: Literal["en", "ko"] = "en"


def settlement_fees(
    broker: list[dict[str, Any]], fee_lines: list[dict[str, Any]]
) -> dict[str, SettlementFees]:
    """Per broker account: its fee lines and every order of the instrument (ADR 0029)."""
    if not fee_lines:
        return {}
    orders: dict[str, list[tuple[str, date]]] = {}
    for document in broker:
        try:
            fill = execution_of("broker", document, str(document["instrument_id"]))
        except MarketDataError:
            continue
        orders.setdefault(fill.book, []).append((fill.ref, fill.day))
    lines: dict[str, list[tuple[date, Decimal]]] = {}
    for line in fee_lines:
        book = f"broker:{line['broker_account_ref']}"
        fee = Decimal(str(line["commission"])) + Decimal(str(line["tax"]))
        lines.setdefault(book, []).append((date.fromisoformat(line["settlement_date"]), fee))
    return {
        book: SettlementFees(tuple(book_lines), tuple(orders.get(book, [])))
        for book, book_lines in lines.items()
    }


def register_agent_routes(
    application: FastAPI,
    *,
    store: Callable[[], AgentStore | None],
    get_bars: Callable[[], GetBars],
    journal: Callable[[], JournalStore | None],
    backtests: Callable[[], BacktestRunner | None],
    api_key: Callable[[], str | None],
    now: Callable[[], datetime],
    public_sources: Callable[[], PublicSources | None] = lambda: None,
    error_response: ErrorResponse,
    ask: Callable[..., dict[str, Any]] = ask_claude,
) -> None:
    def unavailable(request: Request) -> JSONResponse:
        return error_response(
            request,
            503,
            "agent_storage_unconfigured",
            "Set SIW_DATA_DIR to keep Agent analyses; nothing was analysed",
        )

    def respond(document: dict[str, Any], status_code: int = 200) -> JSONResponse:
        validate_document(document)
        return JSONResponse(status_code=status_code, content=document)

    @application.post("/v1/agents/review/chart", response_model=None)
    def review_chart(request: Request, body: ChartReviewRequest) -> Response:
        agents = store()
        if agents is None:
            return unavailable(request)
        try:
            evidence = chart_point(get_bars(), body.instrument_id, body.timeframe, body.cutoff_utc)
        except MarketDataError as exc:
            return error_response(request, 422, "agent_data_unavailable", str(exc))
        return respond(agents.save(evidence), 201)

    @application.post("/v1/agents/review/trades", response_model=None)
    def review_trades(request: Request, body: TradeReviewRequest) -> Response:
        agents, ledgers = store(), journal()
        if agents is None or ledgers is None:
            return unavailable(request)
        instrument = body.instrument_id
        documents = {
            "manual": ledgers.manual_executions(instrument),
            "imported": ledgers.imported_executions(instrument),
            "broker": ledgers.broker_executions(instrument),
        }
        chosen = []
        for ref in body.executions:
            found = next(
                (d for d in documents[ref.ledger] if d["execution_id"] == ref.execution_id), None
            )
            if found is None:
                return error_response(
                    request,
                    404,
                    "execution_not_found",
                    f"No {ref.ledger} execution {ref.execution_id} for {instrument}",
                )
            chosen.append(execution_of(ref.ledger, found, instrument))
        try:
            fees = settlement_fees(documents["broker"], ledgers.broker_fee_lines(instrument))
            evidence = trade_review(get_bars(), instrument, chosen, now(), fees)
        except MarketDataError as exc:
            return error_response(request, 422, "agent_data_unavailable", str(exc))
        return respond(agents.save(evidence), 201)

    @application.post("/v1/agents/review/backtest", response_model=None)
    def review_backtest(request: Request, body: BacktestReviewRequest) -> Response:
        agents, runner = store(), backtests()
        if agents is None or runner is None:
            return unavailable(request)
        try:
            run = runner.store.run(body.run_id)
            results = runner.store.results(body.run_id)
        except BacktestNotFoundError:
            return error_response(request, 404, "backtest_not_found", "No such run")
        if results is None:
            return error_response(
                request, 409, "backtest_results_unavailable", f"The run is {run['status']}"
            )
        return respond(agents.save(backtest_review(run, results)), 201)

    def from_public_source(request: Request, build: Callable[[], Any]) -> Response:
        try:
            evidence = build()
        except UnsupportedTargetError as exc:
            return error_response(request, 422, "agent_target_unsupported", str(exc))
        except SourceConfigError as exc:
            return error_response(request, 409, exc.code, str(exc))
        except SourceError as exc:
            return error_response(request, 502, "public_source_unavailable", str(exc))
        except MarketDataError as exc:
            return error_response(request, 422, "agent_data_unavailable", str(exc))
        agents = store()
        assert agents is not None
        return respond(agents.save(evidence), 201)

    def cutoff_problem(request: Request, cutoff: datetime) -> JSONResponse | None:
        if cutoff.tzinfo is None:
            return error_response(
                request, 422, "agent_cutoff_invalid", "cutoff_utc must carry a time zone"
            )
        return None

    @application.post("/v1/agents/review/filings", response_model=None)
    def review_filings(request: Request, body: FilingsReviewRequest) -> Response:
        agents, sources = store(), public_sources()
        if agents is None or sources is None:
            return unavailable(request)
        problem = cutoff_problem(request, body.cutoff_utc)
        if problem is not None:
            return problem
        return from_public_source(
            request,
            lambda: filings_review(
                sources.sec(),
                body.instrument_id,
                body.cutoff_utc,
                body.window_days,
                body.after_days,
                now(),
            ),
        )

    @application.post("/v1/agents/review/dark-pool", response_model=None)
    def review_dark_pool(request: Request, body: DarkPoolReviewRequest) -> Response:
        agents, sources = store(), public_sources()
        if agents is None or sources is None:
            return unavailable(request)
        problem = cutoff_problem(request, body.cutoff_utc)
        if problem is not None:
            return problem
        return from_public_source(
            request,
            lambda: dark_pool_review(
                sources.finra(), get_bars(), body.instrument_id, body.cutoff_utc, body.weeks
            ),
        )

    @application.get("/v1/agents/analyses", response_model=None)
    def list_analyses(request: Request, limit: int = Query(default=30, ge=1, le=200)) -> Response:
        agents = store()
        if agents is None:
            return unavailable(request)
        return respond(
            {
                "kind": "agent_analysis_list_response",
                "schema_version": "0.1",
                "analyses": agents.analyses(limit),
            }
        )

    @application.get("/v1/agents/analyses/{analysis_id}", response_model=None)
    def get_analysis(request: Request, analysis_id: str) -> Response:
        agents = store()
        if agents is None:
            return unavailable(request)
        try:
            return respond(agents.analysis(analysis_id))
        except AnalysisNotFoundError:
            return error_response(request, 404, "analysis_not_found", "No such analysis")

    @application.get("/v1/agents/analyses/{analysis_id}/payload", response_model=None)
    def get_payload(request: Request, analysis_id: str) -> Response:
        """Exactly what "Ask Claude" would send, for the user to read first."""
        agents = store()
        if agents is None:
            return unavailable(request)
        try:
            return JSONResponse(content=payload_for(agents.raw(analysis_id)))
        except AnalysisNotFoundError:
            return error_response(request, 404, "analysis_not_found", "No such analysis")

    @application.post("/v1/agents/analyses/{analysis_id}/commentary", response_model=None)
    def add_commentary(request: Request, analysis_id: str, body: CommentaryRequest) -> Response:
        agents = store()
        if agents is None:
            return unavailable(request)
        try:
            raw = agents.raw(analysis_id)
        except AnalysisNotFoundError:
            return error_response(request, 404, "analysis_not_found", "No such analysis")
        key = api_key()
        if key is None:
            return error_response(
                request,
                409,
                "agent_key_missing",
                "Register an enabled Claude (Anthropic) API key on the Keys screen",
            )
        model = os.getenv("SIW_AGENT_MODEL", "").strip() or DEFAULT_MODEL
        try:
            commentary = ask(raw, key, body.language, model=model, now=now)
        except CommentaryError as exc:
            return error_response(request, 502, "agent_commentary_failed", str(exc))
        return respond(agents.add_commentary(analysis_id, commentary))
