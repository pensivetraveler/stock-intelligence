"""Check D of the daily screening (ADR 0052 §5, S2): off-exchange (TRF-reported) price
levels from Alpaca SIP trades. Stored like every screening check; never places orders.
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
from services.market_data.provider import MarketDataError
from services.public_sources.http import SourceError
from services.screening.candidates import utc_text
from services.screening.off_exchange import finra_context, off_exchange_check
from services.screening.store import ScreeningStore

SYMBOL = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")
MAX_DAYS_BACK = 10
#: FINRA weeks read for the weekly ATS context (ADR 0024).
FINRA_WEEKS = 12

#: FINRA weekly ATS evidence for (instrument_id, cutoff), or None when not set up.
FinraReview = Callable[[str, datetime], Any]


class OffExchangeCheckRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=10)
    session_date: date | None = None


def register_screening_off_exchange_routes(
    application: FastAPI,
    *,
    store: Callable[[], ScreeningStore | None],
    trades: Callable[[], Any],
    listing_for: Callable[[str], dict[str, Any] | None],
    finra_review: Callable[[], FinraReview | None],
    error_response: ErrorResponse,
    clock: Callable[[], datetime],
) -> None:
    """`trades()` returns an Alpaca adapter (anything with `trade_pages` and `config`)."""
    calendar = UsEquityCalendar("XNAS")

    def session_window(requested: date | None) -> Any:
        if requested is not None:
            return calendar.window_for_date(requested)
        day = clock().astimezone(NEW_YORK).date()
        for _ in range(MAX_DAYS_BACK):
            window = calendar.window_for_date(day)
            if window is not None:
                return window
            day -= timedelta(days=1)
        return None

    def finra(instrument_id: str, cutoff: datetime) -> tuple[dict[str, Any] | None, str | None]:
        review = finra_review()
        if review is None:
            return None, "FINRA 주간 ATS 맥락: 공개 자료 저장소가 설정되지 않아 넣지 않았다."
        try:
            return finra_context(review(instrument_id, cutoff)), None
        except (SourceError, MarketDataError, ValueError) as exc:
            return None, f"FINRA 주간 ATS 맥락을 읽지 못했다: {type(exc).__name__}"

    @application.post("/v1/screening/checks/off_exchange", response_model=None)
    def run_off_exchange_check(request: Request, body: OffExchangeCheckRequest) -> Response:
        """Check D: off-exchange (TRF-reported) volume by price before the cutoff."""
        screening = store()
        if screening is None:
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
        adapter = trades()
        if adapter is None or not hasattr(adapter, "trade_pages"):
            return error_response(
                request,
                503,
                "alpaca_unconfigured",
                "Check D reads Alpaca SIP trades: register an enabled Alpaca key",
            )
        instrument_id = str(listing["instrument"]["instrument_id"])
        now = clock()
        cutoff = min(now, window.post_close_utc)
        context, problem = finra(instrument_id, cutoff)
        outcome = off_exchange_check(
            adapter.trade_pages,
            symbol=symbol,
            cutoff=cutoff,
            now=now,
            publication_delay_minutes=adapter.config.publication_delay_minutes,
            finra=context,
        )
        if problem is not None:
            outcome["warnings"].append(problem)
        document = {
            "kind": "screening_check",
            "schema_version": "0.1",
            "check": "off_exchange",
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


def finra_review_from(
    public_sources: Callable[[], Any], get_bars: Callable[[], Any]
) -> Callable[[], FinraReview | None]:
    """The Dark Pool Agent's FINRA weekly review (ADR 0024), when public sources exist."""
    from services.agents.dark_pool import dark_pool_review

    def build() -> FinraReview | None:
        sources = public_sources()
        if sources is None:
            return None

        def review(instrument_id: str, cutoff: datetime) -> Any:
            return dark_pool_review(sources.finra(), get_bars(), instrument_id, cutoff, FINRA_WEEKS)

        return review

    return build
