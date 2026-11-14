"""Stage 3 realtime flow watch over today's candidates (ADR 0057 §3, U-a).

`GET /v1/screening/flow` judges every candidate of a session at the newest closed minute
from the 1m bars of the data provider (Alpaca SIP when `SIW_MARKET_DATA_PROVIDER=alpaca`).
Nothing is stored here: every state, including the changes through the day, is rebuilt
from bars each time. Halts come from what `siw-collector watch-halts` recorded under the
data dir (`screeners/sip_status_watch.sqlite3`); without that record the halted minutes
stay in the windows and the result says so.

Stage 2 (ADR 0057 §2, U-b) comes from each candidate's latest news check of the session
(`services/screening/material.py`). A candidate excluded for the day is not watched
(status `stage2_excluded`, no bars read). Every other candidate is watched, including one
whose material is unconfirmed, and carries the verdict and its strategy (normal or
conservative) in `result.stage2_detail` for the later stages.

States only: no order is placed, changed or cancelled here.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from packages.contracts.validator import validate_document
from services.api.journal_routes import ErrorResponse
from services.backtest.data import GetBars
from services.collector.halt_watch import store_path
from services.market_data.calendar import NEW_YORK, SessionWindow, UsEquityCalendar
from services.market_data.provider import MarketDataError
from services.screening.candidates import read_watched, utc_text, watch_path
from services.screening.flow_watch import CHECK_VERSION, RULES_VERSION, flow_check
from services.screening.halts import HaltRead, recorded_halts
from services.screening.material import (
    RULES_VERSION as STAGE2_RULES_VERSION,
)
from services.screening.material import (
    flow_stage2,
    stage2_by_symbol,
    stage2_verdict,
)
from services.screening.store import ScreeningStore

CONDITION_NAME = "SIW_당일급등주"
MAX_DAYS_BACK = 10
WORKERS = 4
NO_HALT_SOURCE = "halt 기록을 쓰지 않았다(halts=no_halts)."

GetHalts = Callable[[str, SessionWindow, datetime], HaltRead]
#: Stage 2 verdicts of a session by symbol (default: from the stored news checks).
GetStage2 = Callable[[ScreeningStore, date], dict[str, dict[str, Any]]]


def no_halts(_symbol: str, _window: SessionWindow, _as_of: datetime) -> HaltRead:
    return HaltRead(available=False, note=NO_HALT_SOURCE)


def register_screening_flow_routes(
    application: FastAPI,
    *,
    store: Callable[[], ScreeningStore | None],
    data_dir: Callable[[], Path | None],
    listing_for: Callable[[str], dict[str, Any] | None],
    get_bars: Callable[[], GetBars],
    error_response: ErrorResponse,
    clock: Callable[[], datetime],
    halts: GetHalts | None = None,
    stage2: GetStage2 = stage2_by_symbol,
) -> None:
    calendar = UsEquityCalendar("XNAS")

    def session_window(requested: date | None) -> SessionWindow | None:
        if requested is not None:
            return calendar.window_for_date(requested)
        day = clock().astimezone(NEW_YORK).date()
        for _ in range(MAX_DAYS_BACK):
            window = calendar.window_for_date(day)
            if window is not None:
                return window
            day -= timedelta(days=1)
        return None

    @application.get("/v1/screening/flow", response_model=None)
    def flow(request: Request, session_date: date | None = None) -> Response:
        """Every candidate's stage 3 state at the newest closed minute, and its changes."""
        screening, root = store(), data_dir()
        if screening is None or root is None:
            return error_response(
                request,
                503,
                "screening_storage_unconfigured",
                "Set SIW_DATA_DIR to read the candidates; nothing was judged",
            )
        window = session_window(session_date)
        if window is None:
            return error_response(request, 422, "not_a_session", "that date has no US session")
        now = clock()
        cutoff = min(now, window.post_close_utc)
        watched = read_watched(watch_path(root), window, cutoff, CONDITION_NAME)
        symbols = [item.symbol for item in watched.symbols]
        symbols += [s for s, _ in screening.manual(window.session_date) if s not in symbols]
        bars = get_bars()
        # By default, what `siw-collector watch-halts` recorded under the data dir.
        read_halts = halts or recorded_halts(store_path(root))
        verdicts = stage2(screening, window.session_date)

        def judge(symbol: str) -> dict[str, Any]:
            row: dict[str, Any] = {"symbol": symbol, "instrument_id": None}
            verdict = verdicts.get(symbol) or stage2_verdict(None)
            listing = listing_for(symbol)
            if listing is None:
                return {
                    **row,
                    "status": "unsupported",
                    "result": {"reason": f"{symbol} is not in the app's symbol list"},
                    "warnings": [],
                    "sources": {},
                }
            instrument_id = str(listing["instrument"]["instrument_id"])
            row["instrument_id"] = instrument_id
            if flow_stage2(verdict) == "failed":
                # ADR 0057: excluded for the day at stage 2, so stage 3 does not watch it.
                return {
                    **row,
                    "status": "stage2_excluded",
                    "result": {
                        "stage2": "failed",
                        "stage2_detail": verdict,
                        "new_buys_allowed": False,
                        "resting_orders": "keep",
                        "reason": "excluded for the day at stage 2 (low-impact material)",
                    },
                    "warnings": [],
                    "sources": {},
                }
            try:
                outcome = flow_check(
                    instrument_id,
                    window,
                    now,
                    bars,
                    read_halts(symbol, window, now),
                    flow_stage2(verdict),
                    stage2_detail=verdict,
                )
            except MarketDataError as exc:
                outcome = {
                    "status": "error",
                    "result": {"reason": str(exc)[:300]},
                    "warnings": [],
                    "sources": {},
                }
            outcome.pop("versions", None)
            return {**row, **outcome}

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            rows = list(pool.map(judge, symbols))
        document = {
            "kind": "screening_flow_watch",
            "schema_version": "0.1",
            "session_date": window.session_date.isoformat(),
            "created_at_utc": utc_text(now),
            "after_the_fact": now > window.post_close_utc,
            "versions": {
                "check": CHECK_VERSION,
                "rules": RULES_VERSION,
                "stage2_rules": STAGE2_RULES_VERSION,
            },
            "candidates": rows,
        }
        validate_document(document)
        return JSONResponse(content=document)
