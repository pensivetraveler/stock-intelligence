"""Check D of the daily screening (ADR 0052 §5, S2): off-exchange (TRF-reported) price
levels from Alpaca SIP trades. Stored like every screening check; never places orders.

The closed sessions come from the structure model's session cache and its background
reader (ADR 0053), shared with the structure check. The first run for a ticker may answer
`pending` with the sessions read so far; posting again shows the rest.
"""

from __future__ import annotations

import re
import time
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
from services.screening.off_exchange import OffExchangeLimits, finra_context, off_exchange_check
from services.screening.store import ScreeningStore
from services.screening.structure.service import StructureStore
from services.screening.structure.sessions import SessionFiller

SYMBOL = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")
MAX_DAYS_BACK = 10
#: FINRA weeks read for the weekly ATS context (ADR 0024).
FINRA_WEEKS = 12
#: The trades never get less than this, however long the FINRA context took.
MIN_TRADE_SECONDS = 10.0

#: FINRA weekly ATS evidence for (instrument_id, cutoff), or None when not set up.
FinraReview = Callable[[str, datetime], Any]


class OffExchangeCheckRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=10)
    session_date: date | None = None
    #: Closed sessions; the cache holds the structure model's twenty, so 5 and 10 read
    #: nothing more.
    sessions: int = Field(default=OffExchangeLimits.sessions, ge=1, le=20)


class OffExchangeChecks:
    """Check D runs, shared by the route and the stage 4 runner (ADR 0057 §4)."""

    def __init__(
        self,
        *,
        finra_review: Callable[[], FinraReview | None],
        clock: Callable[[], datetime],
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._finra_review = finra_review
        self._clock = clock
        self._monotonic = monotonic
        self._sleep = sleep

    def _finra(
        self, instrument_id: str, cutoff: datetime
    ) -> tuple[dict[str, Any] | None, str | None]:
        review = self._finra_review()
        if review is None:
            return None, "FINRA 주간 ATS 맥락: 공개 자료 저장소가 설정되지 않아 넣지 않았다."
        try:
            return finra_context(review(instrument_id, cutoff)), None
        except (SourceError, MarketDataError, ValueError) as exc:
            return None, f"FINRA 주간 ATS 맥락을 읽지 못했다: {type(exc).__name__}"

    def run(
        self,
        screening: ScreeningStore,
        opened: tuple[StructureStore, SessionFiller],
        adapter: Any,
        window: Any,
        symbol: str,
        instrument_id: str,
        sessions: int = OffExchangeLimits.sessions,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Runs check D at now (or the session's close) and stores it."""
        structure_store, filler = opened
        now = self._clock()
        cutoff = min(now, window.post_close_utc)
        started = self._monotonic()
        context, problem = self._finra(instrument_id, cutoff)
        # One time budget for the whole request (the screen waits 60 s): FINRA first.
        budget = OffExchangeLimits()
        spent = self._monotonic() - started
        limits = OffExchangeLimits(
            sessions=sessions,
            max_seconds=max(MIN_TRADE_SECONDS, budget.max_seconds - spent),
            live_max_seconds=max(MIN_TRADE_SECONDS, budget.live_max_seconds - spent),
        )
        outcome = off_exchange_check(
            adapter.trade_pages,
            symbol=symbol,
            cutoff=cutoff,
            now=now,
            publication_delay_minutes=adapter.config.publication_delay_minutes,
            cache=structure_store.cache,
            filler=filler,
            limits=limits,
            finra=context,
            monotonic=self._monotonic,
            sleep=self._sleep,
        )
        if problem is not None:
            outcome["warnings"].append(problem)
        if extra:
            outcome["result"] = {**outcome["result"], **extra}
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
        return screening.add_check(document)


def register_screening_off_exchange_routes(
    application: FastAPI,
    *,
    store: Callable[[], ScreeningStore | None],
    structure: Callable[[], tuple[StructureStore, SessionFiller] | None],
    trades: Callable[[], Any],
    listing_for: Callable[[str], dict[str, Any] | None],
    finra_review: Callable[[], FinraReview | None],
    error_response: ErrorResponse,
    clock: Callable[[], datetime],
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
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

    checks = OffExchangeChecks(
        finra_review=finra_review, clock=clock, monotonic=monotonic, sleep=sleep
    )

    @application.post("/v1/screening/checks/off_exchange", response_model=None)
    def run_off_exchange_check(request: Request, body: OffExchangeCheckRequest) -> Response:
        """Check D: off-exchange (TRF-reported) volume by price before the cutoff."""
        screening = store()
        opened = structure()
        if screening is None or opened is None:
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
        stored = checks.run(
            screening, opened, adapter, window, symbol, instrument_id, body.sessions
        )
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
