"""Screening check B — news catalysts (ADR 0052 §3) from the news-analysis service.

`POST /v1/screening/checks/news` asks the service (`news-contract-v1`) for a news check at
the screening cutoff and stores one `screening_check` in screening.sqlite3.
`POST /v1/screening/checks/news/{check_id}/refresh` looks at a waiting check again. It
places no order and changes no entry condition. The session window and cutoff follow the
entry check (S1).

The request carries the cutoff market cap as `context` (news_check 1.1;
`services/screening/market_cap.py`): the share count is the dilution analyzer's from an
earlier session's stored dilution check, or else Toss's (read before the cutoff is taken,
only for a session not over yet, with a warning). Nothing sent is later than the cutoff.
What was sent is kept in `result.context_sent` and sent again when a waiting check is
submitted later. It also carries the company name from the symbol directory as
`context.company_name` (news_check 1.3), with or without a market cap, so the service can
set aside articles a feed tagged with this ticker that are about another company
(`ticker_mismatch`). Stage 2 (ADR 0057 U-b) reads the result's `conclusions` in
`services/screening/material.py`. `NewsChecks` is shared by the routes and the runner that
checks every candidate by itself (`services/api/screening_news_auto.py`, ADR 0057 184).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
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
from services.screening.market_cap import (
    DILUTION_LOOKBACK_DAYS,
    TOSS_FALLBACK_WARNING,
    ReadShares,
    Shares,
    market_cap_context,
    shares_from_dilution,
)
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
#: news_check 1.3 `context.company_name`: 1 to 120 characters.
MAX_COMPANY_NAME = 120
# Reading the feeds takes a few seconds, and the service's LLM step adds about six
# seconds an article (at most five); longer checks stay `pending` and are picked up by
# the refresh route.
NEWS_WAIT_SECONDS = 45.0
CALENDAR = UsEquityCalendar("XNAS")


class NewsCheckRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=10)
    session_date: date | None = None
    lookback_hours: int = Field(default=DEFAULT_LOOKBACK_HOURS, ge=1, le=MAX_LOOKBACK_HOURS)


class NewsChecks:
    """Check B runs, shared by the routes and the runner that checks every candidate.

    `extra` goes into the stored result next to the service's answer (`news_auto`: who
    asked, for the runner); a refresh carries it over.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime],
        news_service: Callable[[], NewsService] | None,
        wait_seconds: float = NEWS_WAIT_SECONDS,
        sleep: Callable[[float], None] | None = None,
        read_shares: ReadShares | None = None,
        get_bars: Callable[[], GetBars] | None = None,
        company_name: Callable[[str], str | None] | None = None,
    ) -> None:
        self._clock = clock
        self._company_name = company_name
        self._service = news_service
        self._wait = wait_seconds
        self._sleep = sleep
        self._read_shares = read_shares
        self._get_bars = get_bars

    def _name(self, instrument_id: str) -> str | None:
        """The company name for `context.company_name`, or None (the context is optional)."""
        if self._company_name is None:
            return None
        try:
            name = self._company_name(instrument_id)
        except Exception:  # noqa: BLE001 - the name is optional input
            return None
        name = " ".join((name or "").split())[:MAX_COMPANY_NAME].strip()
        return name or None

    def _toss_now(self, symbol: str, instrument_id: str) -> tuple[Shares | None, str | None]:
        """Toss's share count read now (before the cutoff is taken), or why not."""
        if self._read_shares is None:
            return None, "no share count source is configured"
        try:
            return self._read_shares(symbol, instrument_id), None
        except Exception as exc:  # noqa: BLE001 - the market cap is optional input
            return None, f"share count: {str(exc)[:200]}"

    def _dilution(
        self, screening: ScreeningStore, symbol: str, bound: datetime
    ) -> tuple[Shares | None, str | None]:
        """The analyzer's count from an earlier session's dilution check of the symbol."""
        day = bound.astimezone(NEW_YORK).date()
        documents: list[Mapping[str, Any]] = []
        for back in range(1, DILUTION_LOOKBACK_DAYS + 1):
            for stored in screening.documents(day - timedelta(days=back), "dilution"):
                if stored.get("symbol") == symbol:
                    documents.append(stored)
        return shares_from_dilution(documents, bound)

    def _shares(
        self, screening: ScreeningStore, window: Any, symbol: str, instrument_id: str
    ) -> tuple[Shares | None, str | None, str | None]:
        """(shares, why none, warning). Everything here is known before the cutoff."""
        now = self._clock()
        shares, why = self._dilution(screening, symbol, min(now, window.post_close_utc))
        if shares is not None:
            return shares, None, None
        # Toss can only be read now: only for a session not over yet (a past session's
        # cutoff is its close).
        if now > window.post_close_utc:
            return None, f"dilution: {why}; Toss: a past session can only be read now", None
        toss, toss_why = self._toss_now(symbol, instrument_id)
        if toss is None:
            return None, f"dilution: {why}; Toss: {toss_why or 'no value'}", None
        return toss, None, f"{TOSS_FALLBACK_WARNING} (dilution: {why})"

    def _context(
        self, instrument_id: str, cutoff: datetime, shares: Shares | None, why_not: str | None
    ) -> tuple[dict[str, Any] | None, str | None]:
        if why_not is not None:
            return None, why_not
        bars = self._get_bars() if self._get_bars is not None else None
        return market_cap_context(instrument_id, cutoff, shares, bars)

    def _ask(
        self,
        symbol: str,
        cutoff: datetime,
        lookback_hours: int,
        job_id: str | None,
        base: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        context = base.get("context_sent") or None
        if self._service is None:
            return "pending", {**base, "reason": "the news-analysis service is not configured"}
        try:
            service = self._service()
            if job_id is None:
                job_id = service.submit(symbol, cutoff, lookback_hours, context)
            if self._sleep is None:
                status, result = follow(service, job_id, self._wait)
            else:
                status, result = follow(service, job_id, self._wait, self._sleep)
            return status, {**base, **result}
        except NewsServiceDown as exc:
            # SIW never starts the service; the check waits until the user does.
            return "pending", {**base, "job_id": job_id, "reason": str(exc)[:300]}
        except NewsServiceError as exc:
            return "error", {**base, "job_id": job_id, "reason": str(exc)[:300]}

    def _document(
        self,
        window: Any,
        symbol: str,
        instrument_id: str,
        cutoff: datetime,
        status: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        now = self._clock()
        check = result.get("news_check") or {}
        versions: dict[str, str] = {"check": CHECK_VERSION, "contract": CONTRACT}
        service_versions = check.get("versions") or {}
        for key in (
            "service",
            "rules_version",
            "rules_sha256",
            "nir_prompt_version",
            "nir_model",
            "ticker_match_version",
        ):
            if service_versions.get(key):
                versions[key] = str(service_versions[key])
        for source_id, parser in (service_versions.get("parsers") or {}).items():
            versions[f"parser.{source_id}"] = str(parser)
        warnings = [str(w) for w in check.get("warnings") or ()]
        if result.get("shares_note"):
            warnings.append(str(result["shares_note"]))
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

    def _store(self, screening: ScreeningStore, made: dict[str, Any]) -> dict[str, Any]:
        validate_document({**made, "check_id": 0})
        stored = screening.add_check(made)
        validate_document(stored)
        return stored

    def run(
        self,
        screening: ScreeningStore,
        window: Any,
        symbol: str,
        instrument_id: str,
        lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Asks the service at the session's cutoff and stores the check."""
        shares, why_not, warning = self._shares(screening, window, symbol, instrument_id)
        cutoff = min(self._clock(), window.post_close_utc)
        market_cap, note = self._context(instrument_id, cutoff, shares, why_not)
        context = dict(market_cap or {})
        name = self._name(instrument_id)
        if name is not None:
            context["company_name"] = name
        base: dict[str, Any] = {
            **(extra or {}),
            "lookback_hours": lookback_hours,
            # What SIW sent as `context` (news_check 1.1); kept for a later resubmit.
            "context_sent": context or {},
            "context_note": note,
            "shares_note": warning if market_cap else None,
        }
        status, result = self._ask(symbol, cutoff, lookback_hours, None, base)
        made = self._document(window, symbol, instrument_id, cutoff, status, result)
        return self._store(screening, made)

    def refresh(self, screening: ScreeningStore, previous: dict[str, Any]) -> dict[str, Any]:
        """A waiting check looked at again: a new stored check when the job has a result."""
        if previous["check"] != "news" or previous["status"] != "pending":
            return previous
        window = CALENDAR.window_for_date(date.fromisoformat(previous["session_date"]))
        cutoff = datetime.fromisoformat(previous["cutoff_utc"].replace("Z", "+00:00"))
        old = previous["result"]
        base = {
            key: old.get(key)
            for key in (
                "news_auto",
                "lookback_hours",
                "context_sent",
                "context_note",
                "shares_note",
            )
            if key in old
        }
        base.setdefault("context_sent", {})
        lookback = int(old.get("lookback_hours") or DEFAULT_LOOKBACK_HOURS)
        status, result = self._ask(previous["symbol"], cutoff, lookback, old.get("job_id"), base)
        if status == "pending" and result.get("job_id") == old.get("job_id"):
            return previous
        made = self._document(
            window, previous["symbol"], previous["instrument_id"], cutoff, status, result
        )
        return self._store(screening, made)


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
    company_name: Callable[[str], str | None] | None = None,
) -> None:
    checks = NewsChecks(
        clock=clock,
        news_service=news_service,
        wait_seconds=wait_seconds,
        sleep=sleep,
        read_shares=read_shares,
        get_bars=get_bars,
        company_name=company_name,
    )

    def session_window(requested: date | None) -> Any:
        """The requested session, or today's (or the latest before today) in New York."""
        if requested is not None:
            return CALENDAR.window_for_date(requested)
        day = clock().astimezone(NEW_YORK).date()
        for _ in range(MAX_DAYS_BACK):
            window = CALENDAR.window_for_date(day)
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
        stored = checks.run(screening, window, symbol, instrument_id, body.lookback_hours)
        return JSONResponse(content=stored)

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
        return JSONResponse(content=checks.refresh(screening, previous))
