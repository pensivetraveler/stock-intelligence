"""Screening check B — news catalysts (ADR 0052 §3): `POST /v1/screening/checks/news`.

Reads Alpaca News headlines and summaries known by the cutoff, tags them with the keyword
rules and stores one `screening_check` in screening.sqlite3. It places no order and changes
no entry condition. The session window and cutoff follow the entry check (S1).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from packages.contracts.validator import validate_document
from services.api.journal_routes import ErrorResponse
from services.market_data.calendar import NEW_YORK, UsEquityCalendar
from services.public_sources.cache import utc_text
from services.public_sources.sources import PublicSources
from services.screening.news import DEFAULT_LOOKBACK_HOURS, MAX_LOOKBACK_HOURS, news_check
from services.screening.store import ScreeningStore

SYMBOL = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")
MAX_DAYS_BACK = 10


class NewsCheckRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=10)
    session_date: date | None = None
    lookback_hours: int = Field(default=DEFAULT_LOOKBACK_HOURS, ge=1, le=MAX_LOOKBACK_HOURS)


def register_screening_news_routes(
    application: FastAPI,
    *,
    store: Callable[[], ScreeningStore | None],
    listing_for: Callable[[str], dict[str, Any] | None],
    news: Callable[[], PublicSources | None],
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

    @application.post("/v1/screening/checks/news", response_model=None)
    def run_news_check(request: Request, body: NewsCheckRequest) -> Response:
        """Check B: catalyst categories of the news created before the cutoff."""
        screening, sources = store(), news()
        if screening is None or sources is None:
            return error_response(
                request,
                503,
                "screening_storage_unconfigured",
                "Set SIW_DATA_DIR to keep screening results; nothing was stored",
            )
        symbol = body.symbol.strip().upper()
        if not SYMBOL.fullmatch(symbol):
            return error_response(request, 422, "invalid_symbol", "a US ticker such as AAPL")
        window = session_window(body.session_date)
        if window is None:
            return error_response(request, 422, "not_a_session", "that date has no US session")
        listing = listing_for(symbol)
        if listing is None:
            return error_response(
                request, 422, "unknown_symbol", f"{symbol} is not in the app's symbol list"
            )
        instrument_id = str(listing["instrument"]["instrument_id"])
        now = clock()
        cutoff = min(now, window.post_close_utc)
        outcome = news_check(sources.alpaca_news(), instrument_id, cutoff, body.lookback_hours)
        document = {
            "kind": "screening_check",
            "schema_version": "0.1",
            "check": "news",
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
        stored = screening.add_check(document)
        validate_document(stored)
        return JSONResponse(content=stored)
