"""Off-exchange price structure check (ADR 0053): the dark-pool stress model v2 on Alpaca
SIP trades, stored like every screening check (ADR 0052 S1). Never places orders.

The first run for a ticker reads twenty closed sessions in the background and answers
`pending`; posting again shows the progress and, once the sessions are cached, the result.
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from packages.contracts.validator import validate_document
from services.api.journal_routes import ErrorResponse
from services.market_data.calendar import NEW_YORK, UsEquityCalendar
from services.screening.candidates import utc_text
from services.screening.store import ScreeningStore
from services.screening.structure.check import structure_check
from services.screening.structure.service import (
    DATABASE,
    StructureStore,
    daily_bars_from,
    open_structure,
)
from services.screening.structure.sessions import SessionFiller

SYMBOL = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")
MAX_DAYS_BACK = 10
#: The screen waits 60 s; the sessions keep loading after the answer.
WAIT_SECONDS = 30.0


class StructureCheckRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=10)
    session_date: date | None = None


class LazyStructure:
    """`SIW_DATA_DIR/screening_structure.sqlite3` and its session filler, opened on first use."""

    def __init__(self, trades: Callable[[], Any], clock: Callable[[], datetime]) -> None:
        self._trades = trades
        self._clock = clock
        self._lock = threading.Lock()
        self._store: StructureStore | None = None
        self._filler: SessionFiller | None = None

    def get(self) -> tuple[StructureStore, SessionFiller] | None:
        with self._lock:
            configured = os.getenv("SIW_DATA_DIR", "").strip()
            if self._store is None and configured:
                self._store = open_structure(Path(configured) / DATABASE, self._clock)

                def pages() -> Any:
                    adapter = self._trades()
                    return None if adapter is None else getattr(adapter, "trade_pages", None)

                self._filler = SessionFiller(self._store.cache, pages, self._clock)
            if self._store is None or self._filler is None:
                return None
            return self._store, self._filler


def register_screening_structure_routes(
    application: FastAPI,
    *,
    store: Callable[[], ScreeningStore | None],
    structure: Callable[[], tuple[StructureStore, SessionFiller] | None],
    trades: Callable[[], Any],
    listing_for: Callable[[str], dict[str, Any] | None],
    error_response: ErrorResponse,
    clock: Callable[[], datetime],
    wait_seconds: float = WAIT_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
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

    @application.post("/v1/screening/checks/off_exchange_structure", response_model=None)
    def run_structure_check(request: Request, body: StructureCheckRequest) -> Response:
        """Off-exchange price structure ("장외 체결 가격 구조") before the cutoff."""
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
                "The structure check reads Alpaca SIP trades: register an enabled Alpaca key",
            )
        structure_store, filler = opened
        instrument_id = str(listing["instrument"]["instrument_id"])
        now = clock()
        cutoff = min(now, window.post_close_utc)
        outcome = structure_check(
            symbol=symbol,
            instrument_id=instrument_id,
            cutoff=cutoff,
            now=now,
            publication_delay_minutes=adapter.config.publication_delay_minutes,
            calendar=calendar,
            cache=structure_store.cache,
            filler=filler,
            registry=structure_store.registry,
            parameters=structure_store.parameters(),
            trade_pages=adapter.trade_pages,
            split_actions=getattr(adapter, "split_actions", None),
            daily_bars=daily_bars_from(adapter) if hasattr(adapter, "get_bars") else None,
            wait_seconds=wait_seconds,
            sleep=sleep,
        )
        document = {
            "kind": "screening_check",
            "schema_version": "0.1",
            "check": "off_exchange_structure",
            "session_date": window.session_date.isoformat(),
            "symbol": symbol,
            "instrument_id": instrument_id,
            "cutoff_utc": utc_text(cutoff),
            "created_at_utc": utc_text(now),
            # A later run for an earlier time sees stored data, not what was live then.
            "after_the_fact": now > window.post_close_utc,
            "strategy": None,
            **outcome,
        }
        validate_document({**document, "check_id": 0})
        stored = screening.add_check(document)
        validate_document(stored)
        return JSONResponse(content=stored)
