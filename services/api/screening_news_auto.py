"""Stage 2 news checks run by themselves (ADR 0057, 2026-11-23 user decision 184).

During a US session (pre-market open to after-hours close, New York) a daemon thread looks
at the day's candidates (the condition record and the symbols added by hand, as the
candidates panel lists them) every `INTERVAL_SECONDS` and asks check B once a session for
each candidate that has no news check of the session yet, in the order they first
appeared, one new check a tick. A symbol that already has a news check (from the panel or
from this runner) is not asked again; an `error` is not retried by itself. A check of this
runner that is still waiting is looked at again (at most `REFRESH_PER_TICK` a tick); while
the service is down nothing new is asked.

The checks are stored like the panel's, with `result.news_auto.requested_by` set to
`news_auto` (as stage 4 marks its runs). The service's NIR LLM step has a monthly cap
(news-analysis ADR 0002, about $0.01 a check, $15 a month); past it the service answers
with its rules only (`nir.state` `stopped`, reason `monthly_cap_reached`). The first such
answer of a session is logged with its time; the stored check keeps it too. Read-only:
nothing is ordered.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from services.api.screening_news_routes import NewsChecks
from services.api.screening_stage4 import session_candidates
from services.market_data.calendar import NEW_YORK, UsEquityCalendar
from services.screening.candidates import utc_text
from services.screening.store import ScreeningStore

LOGGER = logging.getLogger(__name__)
INTERVAL_SECONDS = 30.0
FIRST_DELAY_SECONDS = 45.0
REFRESH_PER_TICK = 3
#: The runner waits this long for a check before looking again on a later tick.
NEWS_WAIT_SECONDS = 10.0
REQUESTED_BY = "news_auto"


def request_note(now: datetime) -> dict[str, Any]:
    """Stored inside the check's result: who started it."""
    return {"requested_by": REQUESTED_BY, "requested_at_utc": utc_text(now)}


def by_runner(document: Mapping[str, Any]) -> bool:
    note = (document.get("result") or {}).get("news_auto")
    return isinstance(note, Mapping) and note.get("requested_by") == REQUESTED_BY


def nir_capped(document: Mapping[str, Any]) -> bool:
    """The service answered with its rules only because the monthly LLM cap was reached."""
    nir = (document.get("result") or {}).get("nir")
    if not isinstance(nir, Mapping):
        return False
    return nir.get("state") == "stopped" or nir.get("reason") == "monthly_cap_reached"


class NewsAutoRunner:
    def __init__(
        self,
        *,
        store: Callable[[], ScreeningStore | None],
        data_dir: Callable[[], Path | None],
        listing_for: Callable[[str], dict[str, Any] | None],
        news: NewsChecks,
        clock: Callable[[], datetime],
        condition_name: str,
        refresh_per_tick: int = REFRESH_PER_TICK,
    ) -> None:
        self._store = store
        self._data_dir = data_dir
        self._listing_for = listing_for
        self._news = news
        self._clock = clock
        self._condition_name = condition_name
        self._refresh_per_tick = refresh_per_tick
        self._calendar = UsEquityCalendar("XNAS")
        #: Session date -> the time the first capped answer of that session was stored.
        self.nir_cap_reached: dict[str, str] = {}

    def _note_cap(self, day: str, stored: Mapping[str, Any], report: dict[str, Any]) -> None:
        if not nir_capped(stored):
            return
        if day not in self.nir_cap_reached:
            self.nir_cap_reached[day] = str(stored.get("created_at_utc"))
            LOGGER.warning(
                "news check: the NIR monthly cap was reached at %s (%s); the service answers "
                "with its rules only",
                self.nir_cap_reached[day],
                stored.get("symbol"),
            )
        report["nir_cap_reached_at_utc"] = self.nir_cap_reached[day]

    def tick(self, stop: Callable[[], bool] = lambda: False) -> dict[str, Any] | None:
        """One pass over today's candidates; None outside a session or when unconfigured."""
        screening, root = self._store(), self._data_dir()
        if screening is None or root is None:
            return None
        now = self._clock()
        window = self._calendar.window_for_date(now.astimezone(NEW_YORK).date())
        if window is None or not window.pre_open_utc <= now < window.post_close_utc:
            return None
        day = window.session_date
        report: dict[str, Any] = {
            "session_date": day.isoformat(),
            "refreshed": [],
            "asked": None,
            "blocked": None,
            "unknown_symbol": [],
            "nir_cap_reached_at_utc": self.nir_cap_reached.get(day.isoformat()),
        }
        latest: dict[str, dict[str, Any]] = {}
        for document in screening.documents(day, "news"):
            latest[str(document["symbol"])] = document
        waiting = False
        refreshed = 0
        for symbol, document in latest.items():
            if refreshed >= self._refresh_per_tick or stop():
                break
            if document["status"] != "pending" or not by_runner(document):
                continue
            refreshed += 1
            fresh = self._news.refresh(screening, document)
            if fresh["check_id"] != document["check_id"]:
                report["refreshed"].append(symbol)
                self._note_cap(day.isoformat(), fresh, report)
            if fresh["status"] == "pending":
                waiting = True
        if waiting:
            # The service is down or slow: wait for it before asking for more.
            report["blocked"] = "a news check is still waiting"
            return report
        cutoff = min(now, window.post_close_utc)
        for symbol in session_candidates(screening, root, window, cutoff, self._condition_name):
            if symbol in latest or stop():
                continue
            listing = self._listing_for(symbol)
            if listing is None:
                report["unknown_symbol"].append(symbol)
                continue
            stored = self._news.run(
                screening,
                window,
                symbol,
                str(listing["instrument"]["instrument_id"]),
                extra={"news_auto": request_note(self._clock())},
            )
            report["asked"] = symbol
            self._note_cap(day.isoformat(), stored, report)
            break
        return report

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
                    report = self.tick(stop.is_set)
                    if report is not None and (report["asked"] or report["refreshed"]):
                        LOGGER.info("news auto %s", report)
                except Exception:  # noqa: BLE001 - a bad tick must not stop later ones
                    LOGGER.exception("news auto run failed")
                if stop.wait(interval):
                    return

        threading.Thread(target=loop, name="screening-news-auto", daemon=True).start()
        return stop


def news_auto_enabled() -> bool:
    """`SIW_SCREENING_NEWS_AUTO=0` turns the automatic news checks off."""
    return os.getenv("SIW_SCREENING_NEWS_AUTO", "1").strip() != "0"
