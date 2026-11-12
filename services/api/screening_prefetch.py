"""After the close, read the closed sessions of the day's candidates ahead of time
(ADR 0052 §5, user decision 2026-11-10).

Once a session has closed (post-close plus the publication delay), the candidates of that
session (the condition search record and the symbols added by hand, as the candidates
panel lists them) get their closed sessions queued on the shared session reader at the
back of the queue, so a check D or structure check the next morning answers from the
cache. Only those symbols: never the whole market. Read-only Alpaca trades; nothing is
ordered. The app must be running; a session missed while it was off is queued at the
next start (the cache skips what it already holds).
"""

from __future__ import annotations

import logging
import os
import re
import threading
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from services.market_data.calendar import UsEquityCalendar
from services.screening.candidates import read_watched, watch_path
from services.screening.off_exchange import OffExchangeLimits
from services.screening.store import ScreeningStore
from services.screening.structure.service import StructureStore
from services.screening.structure.sessions import SessionFiller, closed_sessions

LOGGER = logging.getLogger(__name__)
SYMBOL = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")
#: How often the prefetcher looks for a newly closed session.
INTERVAL_SECONDS = 300.0
#: Startup is left alone for a while.
FIRST_DELAY_SECONDS = 60.0


class CandidatePrefetch:
    def __init__(
        self,
        *,
        store: Callable[[], ScreeningStore | None],
        structure: Callable[[], tuple[StructureStore, SessionFiller] | None],
        data_dir: Callable[[], Path | None],
        trades: Callable[[], Any],
        clock: Callable[[], datetime],
        condition_name: str,
    ) -> None:
        self._store = store
        self._structure = structure
        self._data_dir = data_dir
        self._trades = trades
        self._clock = clock
        self._condition_name = condition_name
        self._calendar = UsEquityCalendar("XNAS")
        self._done: set[date] = set()

    def tick(self) -> dict[str, Any] | None:
        """Queues the latest closed session's candidates once; None when nothing to do."""
        screening, opened, root, adapter = (
            self._store(),
            self._structure(),
            self._data_dir(),
            self._trades(),
        )
        if screening is None or opened is None or root is None or adapter is None:
            return None
        if not hasattr(adapter, "trade_pages"):
            return None
        structure_store, filler = opened
        known_until = self._clock() - timedelta(
            minutes=int(adapter.config.publication_delay_minutes)
        )
        latest = closed_sessions(self._calendar, known_until, 1)
        if not latest or latest[0].session_date in self._done:
            return None
        window = latest[0]
        watched = read_watched(
            watch_path(root), window, window.post_close_utc, self._condition_name
        )
        symbols = {item.symbol for item in watched.symbols}
        symbols |= {symbol for symbol, _added in screening.manual(window.session_date)}
        count = max(
            OffExchangeLimits.sessions,
            int(structure_store.parameters().get("long_window_sessions", 20)),
        )
        windows = closed_sessions(self._calendar, known_until, count)
        queued = sorted(symbol for symbol in symbols if SYMBOL.fullmatch(symbol))
        for symbol in queued:
            filler.request(symbol, windows, background=True)
        self._done.add(window.session_date)
        return {"session_date": window.session_date.isoformat(), "symbols": queued}

    def start(
        self,
        interval: float = INTERVAL_SECONDS,
        first_delay: float = FIRST_DELAY_SECONDS,
    ) -> threading.Event:
        """Runs `tick` on a daemon thread until the returned event is set."""
        stop = threading.Event()

        def loop() -> None:
            if stop.wait(first_delay):
                return
            while True:
                try:
                    queued = self.tick()
                    if queued is not None:
                        LOGGER.info(
                            "prefetch %s: %d candidates",
                            queued["session_date"],
                            len(queued["symbols"]),
                        )
                except Exception:  # noqa: BLE001 - a bad tick must not stop later ones
                    LOGGER.exception("candidate prefetch failed")
                if stop.wait(interval):
                    return

        threading.Thread(target=loop, name="screening-prefetch", daemon=True).start()
        return stop


def prefetch_enabled() -> bool:
    """`SIW_SCREENING_PREFETCH=0` turns the after-close prefetch off."""
    return os.getenv("SIW_SCREENING_PREFETCH", "1").strip() != "0"
