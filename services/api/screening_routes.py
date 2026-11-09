"""Daily candidate screening (ADR 0052 S1): candidates and the entry check.

Candidates are the symbols the realtime condition record (ADR 0051) listed or added in a
US session window, plus symbols the user added. Checks are stored in screening.sqlite3
and never place orders or approve plans: the user picks from the cards.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from packages.contracts.validator import validate_document
from services.api.journal_routes import ErrorResponse
from services.backtest.data import GetBars
from services.backtest.store import BacktestNotFoundError
from services.market_data.calendar import NEW_YORK, UsEquityCalendar
from services.market_data.provider import MarketDataError
from services.screening.candidates import read_watched, utc_text, watch_path
from services.screening.entry import entry_check
from services.screening.store import CheckNotFoundError, ScreeningStore

CONDITION_NAME = "SIW_당일급등주"
SYMBOL = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")
MAX_DAYS_BACK = 10


class CandidateRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=10)
    session_date: date | None = None


class EntryCheckRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=10)
    strategy_id: str = Field(min_length=1, max_length=200)
    strategy_version: int | None = Field(default=None, ge=1)
    session_date: date | None = None


def register_screening_routes(
    application: FastAPI,
    *,
    store: Callable[[], ScreeningStore | None],
    data_dir: Callable[[], Path | None],
    strategy: Callable[[str, int | None], dict[str, Any] | None],
    listing_for: Callable[[str], dict[str, Any] | None],
    get_bars: Callable[[], GetBars],
    get_adjusted: Callable[[], Callable[..., Any] | None],
    error_response: ErrorResponse,
    clock: Callable[[], datetime],
) -> None:
    calendar = UsEquityCalendar("XNAS")

    def session_window(requested: date | None) -> Any:
        """The requested session, or today's (or the latest before today) in New York."""
        if requested is not None:
            return calendar.window_for_date(requested)
        day = clock().astimezone(NEW_YORK).date()
        for _ in range(MAX_DAYS_BACK):
            window = calendar.window_for_date(day)
            if window is not None:
                return window
            day -= timedelta(days=1)
        return None

    def unavailable(request: Request) -> JSONResponse:
        return error_response(
            request,
            503,
            "screening_storage_unconfigured",
            "Set SIW_DATA_DIR to keep screening results; nothing was stored",
        )

    def bad_date(request: Request) -> JSONResponse:
        return error_response(request, 422, "not_a_session", "that date has no US session")

    def candidates_document(window: Any) -> dict[str, Any]:
        screening, root = store(), data_dir()
        assert screening is not None and root is not None
        now = clock()
        cutoff = min(now, window.post_close_utc)
        watched = read_watched(watch_path(root), window, cutoff, CONDITION_NAME)
        latest = screening.latest(window.session_date)
        rows: dict[str, dict[str, Any]] = {}
        for item in watched.symbols:
            rows[item.symbol] = {
                "symbol": item.symbol,
                "sources": ["condition"],
                "first_seen_utc": item.first_seen_utc,
                "in_list": item.in_list,
                "kiwoom_exchange": item.kiwoom_exchange,
            }
        for symbol, added in screening.manual(window.session_date):
            if symbol in rows:
                rows[symbol]["sources"].append("manual")
            else:
                rows[symbol] = {
                    "symbol": symbol,
                    "sources": ["manual"],
                    "first_seen_utc": added,
                    "in_list": None,
                    "kiwoom_exchange": None,
                }
        coverage = watched.coverage
        return {
            "kind": "screening_candidates",
            "schema_version": "0.1",
            "session_date": window.session_date.isoformat(),
            "cutoff_utc": utc_text(cutoff),
            "condition_name": CONDITION_NAME,
            "recorded": watched.recorded,
            "watch_coverage": {
                "watches": coverage.watches,
                "interrupted": coverage.interrupted,
                "first_started_utc": coverage.first_started_utc,
                "last_heard_utc": coverage.last_heard_utc,
                "open_now": coverage.open_now,
            },
            "candidates": [
                {
                    **row,
                    "listing": listing_for(row["symbol"]),
                    "checks": latest.get(row["symbol"], {}),
                }
                for row in sorted(rows.values(), key=lambda r: (r["first_seen_utc"], r["symbol"]))
            ],
        }

    def respond(document: dict[str, Any]) -> JSONResponse:
        validate_document(document)
        return JSONResponse(content=document)

    @application.get("/v1/screening/candidates", response_model=None)
    def candidates(request: Request, session_date: date | None = None) -> Response:
        """Today's candidates (or a past session's) with the latest result of each check."""
        if store() is None or data_dir() is None:
            return unavailable(request)
        window = session_window(session_date)
        if window is None:
            return bad_date(request)
        return respond(candidates_document(window))

    @application.post("/v1/screening/candidates", response_model=None)
    def add_candidate(request: Request, body: CandidateRequest) -> Response:
        """A symbol the user adds to a session's candidates."""
        screening = store()
        if screening is None or data_dir() is None:
            return unavailable(request)
        symbol = body.symbol.strip().upper()
        if not SYMBOL.fullmatch(symbol):
            return error_response(request, 422, "invalid_symbol", "a US ticker such as AAPL")
        window = session_window(body.session_date)
        if window is None:
            return bad_date(request)
        screening.add_manual(window.session_date, symbol)
        return respond(candidates_document(window))

    @application.post("/v1/screening/checks/entry", response_model=None)
    def run_entry_check(request: Request, body: EntryCheckRequest) -> Response:
        """Check A: a saved rules strategy's entry condition on the last closed daily bar."""
        screening = store()
        if screening is None:
            return unavailable(request)
        symbol = body.symbol.strip().upper()
        if not SYMBOL.fullmatch(symbol):
            return error_response(request, 422, "invalid_symbol", "a US ticker such as AAPL")
        window = session_window(body.session_date)
        if window is None:
            return bad_date(request)
        saved = strategy(body.strategy_id, body.strategy_version)
        if saved is None:
            return error_response(request, 404, "strategy_not_found", "No such strategy")
        listing = listing_for(symbol)
        if listing is None:
            return error_response(
                request, 422, "unknown_symbol", f"{symbol} is not in the app's symbol list"
            )
        instrument_id = str(listing["instrument"]["instrument_id"])
        now = clock()
        cutoff = min(now, window.post_close_utc)
        try:
            outcome = entry_check(saved, instrument_id, cutoff, get_bars(), get_adjusted())
        except MarketDataError as exc:
            outcome = {
                "status": "error",
                "result": {"reason": str(exc)[:300]},
                "strategy": None,
                "versions": {},
                "warnings": [],
                "sources": {},
            }
        document = {
            "kind": "screening_check",
            "schema_version": "0.1",
            "check": "entry",
            "session_date": window.session_date.isoformat(),
            "symbol": symbol,
            "instrument_id": instrument_id,
            "cutoff_utc": utc_text(cutoff),
            "created_at_utc": utc_text(now),
            # A later run for an earlier time sees stored data, not what was live then.
            "after_the_fact": now > window.post_close_utc,
            **outcome,
        }
        validate_document({**document, "check_id": 0})
        return respond(screening.add_check(document))

    @application.get("/v1/screening/checks/{check_id}", response_model=None)
    def get_check(request: Request, check_id: int) -> Response:
        screening = store()
        if screening is None:
            return unavailable(request)
        try:
            return respond(screening.check(check_id))
        except CheckNotFoundError:
            return error_response(request, 404, "check_not_found", "No such check")


def saved_strategy(
    runner: Callable[[], Any],
) -> Callable[[str, int | None], dict[str, Any] | None]:
    def find(strategy_id: str, version: int | None) -> dict[str, Any] | None:
        jobs = runner()
        if jobs is None:
            return None
        try:
            return dict(jobs.store.strategy(strategy_id, version))
        except BacktestNotFoundError:
            return None

    return find
