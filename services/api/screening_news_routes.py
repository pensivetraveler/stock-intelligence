"""Screening check B — news catalysts (ADR 0052 §3) from the news-analysis service.

`POST /v1/screening/checks/news` asks the service (`news-contract-v1`) for a news check at
the screening cutoff and stores one `screening_check` in screening.sqlite3.
`POST /v1/screening/checks/news/{check_id}/refresh` looks at a waiting check again. It
places no order and changes no entry condition. The session window and cutoff follow the
entry check (S1).

For a session not over yet, the request carries the cutoff market cap as `context`
(news_check 1.1; `services/screening/market_cap.py`): the share count is read before the
cutoff is taken, so nothing sent is later than the cutoff. What was sent is kept in
`result.context_sent` and sent again when a waiting check is submitted later. Stage 2
(ADR 0057 U-b) reads the result's `conclusions` in `services/screening/material.py`.
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
from services.backtest.data import GetBars
from services.market_data.calendar import NEW_YORK, UsEquityCalendar
from services.screening.candidates import utc_text
from services.screening.market_cap import ReadShares, market_cap_context
from services.screening.news_service import (
    CHECK_VERSION,
    CONTRACT,
    SERVICE_NAME,
    NewsService,
    NewsServiceDown,
    NewsServiceError,
    follow,
)
from services.screening.store import CheckNotFoundError, ScreeningStore

SYMBOL = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")
MAX_DAYS_BACK = 10
DEFAULT_LOOKBACK_HOURS = 72
MAX_LOOKBACK_HOURS = 24 * 30
# Reading the feeds takes a few seconds, and the service's LLM step adds about six
# seconds an article (at most five); longer checks stay `pending` and are picked up by
# the refresh route.
NEWS_WAIT_SECONDS = 45.0


class NewsCheckRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=10)
    session_date: date | None = None
    lookback_hours: int = Field(default=DEFAULT_LOOKBACK_HOURS, ge=1, le=MAX_LOOKBACK_HOURS)


def register_screening_news_routes(
    application: FastAPI,
    *,
    store: Callable[[], ScreeningStore | None],
    listing_for: Callable[[str], dict[str, Any] | None],
    news_service: Callable[[], NewsService] | None,
    error_response: ErrorResponse,
    clock: Callable[[], datetime],
    wait_seconds: float = NEWS_WAIT_SECONDS,
    sleep: Callable[[float], None] | None = None,
    read_shares: ReadShares | None = None,
    get_bars: Callable[[], GetBars] | None = None,
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

    def shares_now(symbol: str, instrument_id: str) -> tuple[Any, str | None]:
        """Toss's share count read now (before the cutoff is taken), or why not."""
        if read_shares is None:
            return None, "no share count source is configured"
        try:
            return read_shares(symbol, instrument_id), None
        except Exception as exc:  # noqa: BLE001 - the market cap is optional input
            return None, f"share count: {str(exc)[:200]}"

    def context_for(
        instrument_id: str, cutoff: datetime, shares: Any, why_not: str | None
    ) -> tuple[dict[str, Any] | None, str | None]:
        if why_not is not None:
            return None, why_not
        bars = get_bars() if get_bars is not None else None
        return market_cap_context(instrument_id, cutoff, shares, bars)

    def run(
        symbol: str,
        cutoff: datetime,
        lookback_hours: int,
        job_id: str | None,
        context: dict[str, Any] | None = None,
        context_note: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        base: dict[str, Any] = {
            "lookback_hours": lookback_hours,
            # What SIW sent as `context` (news_check 1.1); kept for a later resubmit.
            "context_sent": context or {},
            "context_note": context_note,
        }
        if news_service is None:
            return "pending", {**base, "reason": "the news-analysis service is not configured"}
        try:
            service = news_service()
            if job_id is None:
                job_id = service.submit(symbol, cutoff, lookback_hours, context)
            if sleep is None:
                status, result = follow(service, job_id, wait_seconds)
            else:
                status, result = follow(service, job_id, wait_seconds, sleep)
            return status, {**base, **result}
        except NewsServiceDown as exc:
            # SIW never starts the service; the check waits until the user does.
            return "pending", {**base, "job_id": job_id, "reason": str(exc)[:300]}
        except NewsServiceError as exc:
            return "error", {**base, "job_id": job_id, "reason": str(exc)[:300]}

    def document(
        window: Any,
        symbol: str,
        instrument_id: str,
        cutoff: datetime,
        status: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        now = clock()
        check = result.get("news_check") or {}
        versions: dict[str, str] = {"check": CHECK_VERSION, "contract": CONTRACT}
        service_versions = check.get("versions") or {}
        for key in ("service", "rules_version", "rules_sha256", "nir_prompt_version", "nir_model"):
            if service_versions.get(key):
                versions[key] = str(service_versions[key])
        for source_id, parser in (service_versions.get("parsers") or {}).items():
            versions[f"parser.{source_id}"] = str(parser)
        warnings = [str(w) for w in check.get("warnings") or ()]
        warnings.append(
            "news text follows each article's terms (Alpaca and Finnhub: no redistribution): "
            "for this computer's screen only"
        )
        return {
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
            "status": status,
            "strategy": None,
            "result": result,
            "versions": versions,
            "warnings": warnings[:200],
            "sources": {
                "service": SERVICE_NAME,
                "contract": CONTRACT,
                "result_id": result.get("result_id"),
            },
        }

    def store_document(screening: ScreeningStore, made: dict[str, Any]) -> JSONResponse:
        validate_document({**made, "check_id": 0})
        stored = screening.add_check(made)
        validate_document(stored)
        return JSONResponse(content=stored)

    @application.post("/v1/screening/checks/news", response_model=None)
    def run_news_check(request: Request, body: NewsCheckRequest) -> Response:
        """Check B: the news-analysis service's catalyst categories before the cutoff."""
        screening = store()
        if screening is None:
            return unavailable(request)
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
        # Toss can only be read now: read it first so it is known by the cutoff, and only
        # for a session not over yet (a past session's cutoff is its close).
        if clock() <= window.post_close_utc:
            shares, why_not = shares_now(symbol, instrument_id)
        else:
            shares, why_not = None, "a past session: the share count can only be read now"
        cutoff = min(clock(), window.post_close_utc)
        context, note = context_for(instrument_id, cutoff, shares, why_not)
        status, result = run(symbol, cutoff, body.lookback_hours, None, context, note)
        made = document(window, symbol, instrument_id, cutoff, status, result)
        return store_document(screening, made)

    @application.post("/v1/screening/checks/news/{check_id}/refresh", response_model=None)
    def refresh_news_check(request: Request, check_id: int) -> Response:
        """A waiting news check, looked at again: a new result when the job has one."""
        screening = store()
        if screening is None:
            return unavailable(request)
        try:
            previous = screening.check(check_id)
        except CheckNotFoundError:
            return error_response(request, 404, "check_not_found", "No such check")
        if previous["check"] != "news" or previous["status"] != "pending":
            return JSONResponse(content=previous)
        window = calendar.window_for_date(date.fromisoformat(previous["session_date"]))
        cutoff = datetime.fromisoformat(previous["cutoff_utc"].replace("Z", "+00:00"))
        old = previous["result"]
        status, result = run(
            previous["symbol"],
            cutoff,
            int(old.get("lookback_hours") or DEFAULT_LOOKBACK_HOURS),
            old.get("job_id"),
            old.get("context_sent") or None,
            old.get("context_note"),
        )
        if status == "pending" and result.get("job_id") == old.get("job_id"):
            return JSONResponse(content=previous)
        made = document(
            window, previous["symbol"], previous["instrument_id"], cutoff, status, result
        )
        return store_document(screening, made)
