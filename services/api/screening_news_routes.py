"""Screening check B — news catalysts (ADR 0052 §3) from the news-analysis service.

`POST /v1/screening/checks/news` asks the service (`news-contract-v1`) for a news check at
the screening cutoff and stores one `screening_check` in screening.sqlite3.
`POST /v1/screening/checks/news/{check_id}/refresh` looks at a waiting check again. It
places no order and changes no entry condition. The session window and cutoff follow the
entry check (S1).
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
from services.screening.candidates import utc_text
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
# Reading three feeds for one ticker takes a few seconds; longer ones are picked up later.
NEWS_WAIT_SECONDS = 15.0


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

    def run(
        symbol: str, cutoff: datetime, lookback_hours: int, job_id: str | None
    ) -> tuple[str, dict[str, Any]]:
        base: dict[str, Any] = {"lookback_hours": lookback_hours}
        if news_service is None:
            return "pending", {**base, "reason": "the news-analysis service is not configured"}
        try:
            service = news_service()
            if job_id is None:
                job_id = service.submit(symbol, cutoff, lookback_hours)
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
        for key in ("service", "rules_version", "rules_sha256"):
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
        cutoff = min(clock(), window.post_close_utc)
        status, result = run(symbol, cutoff, body.lookback_hours, None)
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
        )
        if status == "pending" and result.get("job_id") == old.get("job_id"):
            return JSONResponse(content=previous)
        made = document(
            window, previous["symbol"], previous["instrument_id"], cutoff, status, result
        )
        return store_document(screening, made)
