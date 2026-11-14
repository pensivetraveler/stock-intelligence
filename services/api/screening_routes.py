"""Daily candidate screening (ADR 0052): candidates and the stage checks (ADR 0057).

Candidates are the symbols the realtime condition record (ADR 0051) listed or added in a
US session window, plus symbols the user added. Checks are stored in screening.sqlite3
and never place orders or approve plans: the user picks from the cards.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from packages.contracts.validator import validate_document
from services.api.journal_routes import ErrorResponse
from services.backtest.data import GetBars
from services.market_data.calendar import NEW_YORK, UsEquityCalendar
from services.market_data.provider import MarketDataError
from services.screening.candidates import read_watched, utc_text, watch_path
from services.screening.dilution import (
    CHECK_VERSION as DILUTION_VERSION,
)
from services.screening.dilution import (
    DilutionService,
    DilutionServiceDown,
    DilutionServiceError,
    follow,
)
from services.screening.material import stage2_by_symbol, stage2_verdict
from services.screening.momentum import momentum_check
from services.screening.stage4 import stage4_by_symbol
from services.screening.store import CheckNotFoundError, ScreeningStore

CONDITION_NAME = "SIW_당일급등주"
SYMBOL = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")
MAX_DAYS_BACK = 10
# A rules-only analysis with SEC files cached takes 4-7 s; longer ones are picked up later.
DILUTION_WAIT_SECONDS = 15.0
#: Result keys a refresh keeps from the waiting check (the stage 4 request, ADR 0057 §4).
CARRIED = ("stage4",)


class CandidateRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=10)
    session_date: date | None = None


class MomentumCheckRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=10)
    session_date: date | None = None


class DilutionCheckRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=10)
    session_date: date | None = None
    # Rules-only by default; an LLM analysis only for the few candidates the user picks.
    llm: bool = False


class DilutionChecks:
    """Check C runs, shared by the route and the stage 4 runner (ADR 0057 §4).

    `extra` goes into the stored result next to the analyzer's answer: the stage 4 request
    (`stage4`: who asked and the stage 2 verdict at that time); a refresh carries it over.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime],
        dilution_service: Callable[[], DilutionService] | None,
        wait_seconds: float = DILUTION_WAIT_SECONDS,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._clock = clock
        self._service = dilution_service
        self._wait = wait_seconds
        self._sleep = sleep
        self._calendar = UsEquityCalendar("XNAS")

    def _document(
        self,
        window: Any,
        symbol: str,
        instrument_id: str,
        llm: bool,
        status: str,
        result: dict[str, Any],
        extra: dict[str, Any] | None,
    ) -> dict[str, Any]:
        now = self._clock()
        return {
            "kind": "screening_check",
            "schema_version": "0.1",
            "check": "dilution",
            "session_date": window.session_date.isoformat(),
            "symbol": symbol,
            "instrument_id": instrument_id,
            "cutoff_utc": utc_text(min(now, window.post_close_utc)),
            "created_at_utc": utc_text(now),
            "after_the_fact": now > window.post_close_utc,
            "status": status,
            "strategy": None,
            "result": {"llm": llm, **(extra or {}), **result},
            "versions": {"check": DILUTION_VERSION},
            # The analyzer's as-of is a date: filings later that day may be included.
            "warnings": [
                "the dilution analysis is as of the New York date, not the cutoff time",
                "made from SIW's local data: for this computer's screen only",
            ],
            "sources": {"service": "stock-dilution-analyzer", "contract": "report-v1"},
        }

    def _ask(
        self, window: Any, symbol: str, llm: bool, job_id: str | None
    ) -> tuple[str, dict[str, Any]]:
        if self._service is None:
            return "pending", {"reason": "the dilution analyzer is not configured"}
        try:
            service = self._service()
            if job_id is None:
                job_id = service.submit(symbol, window.session_date, llm)
            if self._sleep is None:
                return follow(service, job_id, self._wait)
            return follow(service, job_id, self._wait, self._sleep)
        except DilutionServiceDown as exc:
            # SIW never starts the analyzer; the check waits until the user does.
            return "pending", {"job_id": job_id, "reason": str(exc)[:300]}
        except DilutionServiceError as exc:
            return "error", {"job_id": job_id, "reason": str(exc)[:300]}

    def run(
        self,
        screening: ScreeningStore,
        window: Any,
        symbol: str,
        instrument_id: str,
        llm: bool,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Asks for a new analysis and stores the check."""
        status, result = self._ask(window, symbol, llm, None)
        document = self._document(window, symbol, instrument_id, llm, status, result, extra)
        validate_document({**document, "check_id": 0})
        return screening.add_check(document)

    def refresh(self, screening: ScreeningStore, previous: dict[str, Any]) -> dict[str, Any]:
        """A waiting check looked at again: a new row only when something changed."""
        window = self._calendar.window_for_date(date.fromisoformat(previous["session_date"]))
        stored = previous["result"]
        llm = bool(stored.get("llm"))
        status, result = self._ask(window, previous["symbol"], llm, stored.get("job_id"))
        if status == "pending" and result.get("job_id") == stored.get("job_id"):
            return previous
        extra = {key: stored[key] for key in CARRIED if key in stored}
        document = self._document(
            window, previous["symbol"], previous["instrument_id"], llm, status, result, extra
        )
        validate_document({**document, "check_id": 0})
        return screening.add_check(document)


def register_screening_routes(
    application: FastAPI,
    *,
    store: Callable[[], ScreeningStore | None],
    data_dir: Callable[[], Path | None],
    listing_for: Callable[[str], dict[str, Any] | None],
    get_bars: Callable[[], GetBars],
    error_response: ErrorResponse,
    clock: Callable[[], datetime],
    dilution_service: Callable[[], DilutionService] | None = None,
    dilution_wait_seconds: float = DILUTION_WAIT_SECONDS,
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

    def bad_date(request: Request) -> JSONResponse:
        return error_response(request, 422, "not_a_session", "that date has no US session")

    def candidates_document(window: Any, as_of: datetime | None = None) -> dict[str, Any]:
        screening, root = store(), data_dir()
        assert screening is not None and root is not None
        now = clock()
        cutoff = min(now, window.post_close_utc)
        if as_of is not None:
            cutoff = min(cutoff, as_of)
        watched = read_watched(watch_path(root), window, cutoff, CONDITION_NAME)
        latest = screening.latest(window.session_date)
        # Stage 2 (ADR 0057 §2): each candidate's verdict from its news checks. A news
        # check made after an `as_of` cut is not hidden here: the verdict says its cutoff.
        verdicts = stage2_by_symbol(screening, window.session_date)
        for found in latest.values():
            if "momentum" in found:
                # The table shows the numbers themselves, not only a status.
                momentum = screening.check(found["momentum"]["check_id"]).get("result", {})
                found["momentum"]["summary"] = {
                    "price": (momentum.get("price") or {}).get("value"),
                    "change_percent": momentum.get("change_percent"),
                    "dollar_volume_estimate": momentum.get("dollar_volume_estimate"),
                    "entry_time_known": bool(momentum.get("entry_time_known", False)),
                }
        rows: dict[str, dict[str, Any]] = {}
        for item in watched.symbols:
            rows[item.symbol] = {
                "symbol": item.symbol,
                "sources": ["condition"],
                "first_seen_utc": item.first_seen_utc,
                "in_list": item.in_list,
                "kiwoom_exchange": item.kiwoom_exchange,
                "first_seen_how": item.first_seen_how,
                "entered_after_utc": item.entered_after_utc,
                "enter_count": item.enter_count,
                "seconds_in_list": round(item.seconds_in_list, 1),
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
                    "first_seen_how": "manual",
                    "entered_after_utc": None,
                    "enter_count": None,
                    "seconds_in_list": None,
                }
        coverage = watched.coverage
        # Stage 4 (ADR 0057 §4): the dilution and off-exchange facts gathered so far. Unlike
        # stage 2, a check made after an `as_of` cut is left out.
        information = stage4_by_symbol(screening, window.session_date, verdicts, rows, as_of)
        return {
            "kind": "screening_candidates",
            "schema_version": "0.1",
            "session_date": window.session_date.isoformat(),
            "cutoff_utc": utc_text(cutoff),
            # Selection mostly ends by noon in New York (ADR 0054); the panel can cut there.
            "selection_noon_utc": utc_text(
                datetime.combine(window.session_date, time(12, 0), NEW_YORK)
            ),
            "condition_name": CONDITION_NAME,
            "recorded": watched.recorded,
            "watch_coverage": {
                "watches": coverage.watches,
                "interrupted": coverage.interrupted,
                "first_started_utc": coverage.first_started_utc,
                "last_heard_utc": coverage.last_heard_utc,
                "open_now": coverage.open_now,
                "gaps": [
                    {
                        "from_utc": gap.from_utc,
                        "to_utc": gap.to_utc,
                        "reason": gap.reason,
                        "previous_end": gap.previous_end,
                    }
                    for gap in coverage.gaps
                ],
            },
            "candidates": [
                {
                    **row,
                    "listing": listing_for(row["symbol"]),
                    "checks": latest.get(row["symbol"], {}),
                    "stage2": verdicts.get(row["symbol"]) or stage2_verdict(None),
                    "stage4": information[row["symbol"]],
                }
                for row in sorted(rows.values(), key=lambda r: (r["first_seen_utc"], r["symbol"]))
            ],
        }

    def respond(document: dict[str, Any]) -> JSONResponse:
        validate_document(document)
        return JSONResponse(content=document)

    @application.get("/v1/screening/candidates", response_model=None)
    def candidates(
        request: Request, session_date: date | None = None, as_of: datetime | None = None
    ) -> Response:
        """Today's candidates (or a past session's) with the latest result of each check.
        `as_of` cuts the record earlier than now, such as at noon New York time."""
        if store() is None or data_dir() is None:
            return unavailable(request)
        if as_of is not None and as_of.utcoffset() is None:
            return error_response(
                request, 422, "invalid_as_of", "as_of needs a time zone, such as ...Z"
            )
        window = session_window(session_date)
        if window is None:
            return bad_date(request)
        return respond(candidates_document(window, as_of))

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

    @application.post("/v1/screening/checks/momentum", response_model=None)
    def run_momentum_check(request: Request, body: MomentumCheckRequest) -> Response:
        """The market before a symbol entered the condition list (ADR 0055): price, change
        from the previous close and dollar volume from the bars closed by then."""
        screening, root = store(), data_dir()
        if screening is None or root is None:
            return unavailable(request)
        symbol = body.symbol.strip().upper()
        if not SYMBOL.fullmatch(symbol):
            return error_response(request, 422, "invalid_symbol", "a US ticker such as AAPL")
        window = session_window(body.session_date)
        if window is None:
            return bad_date(request)
        listing = listing_for(symbol)
        if listing is None:
            return error_response(
                request, 422, "unknown_symbol", f"{symbol} is not in the app's symbol list"
            )
        now = clock()
        watched = read_watched(
            watch_path(root), window, min(now, window.post_close_utc), CONDITION_NAME
        )
        seen = next((item for item in watched.symbols if item.symbol == symbol), None)
        if seen is None:
            return error_response(
                request,
                422,
                "not_a_condition_candidate",
                f"{symbol} is not in the condition record of {window.session_date.isoformat()}",
            )
        instrument_id = str(listing["instrument"]["instrument_id"])
        try:
            outcome = momentum_check(instrument_id, window, seen, get_bars(), calendar)
        except MarketDataError as exc:
            outcome = {
                "status": "error",
                "result": {"reason": str(exc)[:300]},
                "versions": {},
                "warnings": [],
                "sources": {},
            }
        document = {
            "kind": "screening_check",
            "schema_version": "0.1",
            "check": "momentum",
            "session_date": window.session_date.isoformat(),
            "symbol": symbol,
            "instrument_id": instrument_id,
            # The values describe the minutes before SIW first saw the symbol.
            "cutoff_utc": seen.first_seen_utc,
            "created_at_utc": utc_text(now),
            "after_the_fact": now > window.post_close_utc,
            "strategy": None,
            **outcome,
        }
        validate_document({**document, "check_id": 0})
        return respond(screening.add_check(document))

    dilution = DilutionChecks(
        clock=clock,
        dilution_service=dilution_service,
        wait_seconds=dilution_wait_seconds,
        sleep=sleep,
    )

    @application.post("/v1/screening/checks/dilution", response_model=None)
    def run_dilution_check(request: Request, body: DilutionCheckRequest) -> Response:
        """Check C: asks the dilution analyzer and keeps its contract prices."""
        screening = store()
        if screening is None:
            return unavailable(request)
        symbol = body.symbol.strip().upper()
        if not SYMBOL.fullmatch(symbol):
            return error_response(request, 422, "invalid_symbol", "a US ticker such as AAPL")
        window = session_window(body.session_date)
        if window is None:
            return bad_date(request)
        listing = listing_for(symbol)
        if listing is None:
            return error_response(
                request, 422, "unknown_symbol", f"{symbol} is not in the app's symbol list"
            )
        instrument_id = str(listing["instrument"]["instrument_id"])
        return respond(dilution.run(screening, window, symbol, instrument_id, body.llm))

    @application.post("/v1/screening/checks/{check_id}/refresh", response_model=None)
    def refresh_check(request: Request, check_id: int) -> Response:
        """A waiting dilution check, looked at again: a new result when the job has one."""
        screening = store()
        if screening is None:
            return unavailable(request)
        try:
            previous = screening.check(check_id)
        except CheckNotFoundError:
            return error_response(request, 404, "check_not_found", "No such check")
        if previous["check"] != "dilution" or previous["status"] != "pending":
            return respond(previous)
        return respond(dilution.refresh(screening, previous))

    @application.get("/v1/screening/checks/{check_id}", response_model=None)
    def get_check(request: Request, check_id: int) -> Response:
        screening = store()
        if screening is None:
            return unavailable(request)
        try:
            return respond(screening.check(check_id))
        except CheckNotFoundError:
            return error_response(request, 404, "check_not_found", "No such check")
