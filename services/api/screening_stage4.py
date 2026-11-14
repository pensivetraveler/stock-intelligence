"""Stage 4 runs by themselves (ADR 0057 §4, U-c): dilution and off-exchange for every
candidate that passed stage 2, as soon as it passed.

During a US session (pre-market open to after-hours close, New York) a daemon thread
looks at the day's candidates (the condition record and the symbols added by hand, as the
candidates panel lists them) every `INTERVAL_SECONDS`. A candidate whose stage 2 verdict is
"excluded for today" is skipped; every other one ("재료 미확인" included) gets:

- check C, rules only (`llm=false`, no LLM cost): the analyzer has one worker and shares
  the SEC limit (5 requests a second on this computer) with SIW, so at most one analysis
  this runner asked for is waiting at a time. A waiting one is looked at again every tick;
  while the analyzer is down nothing new is asked.
- check D: at most `OFF_EXCHANGE_PER_TICK` a tick, one at a time, with the route's own
  budget (40 s, 80 pages). A `pending` one (closed sessions still being read by the shared
  background reader) runs again once that reader has nothing left for the symbol.

A symbol that already has a result of that check for the session (from this runner or from
the panel) is not run again; an `error` is not retried by itself. Short interest has no
source yet and is not asked for. Read-only: nothing is ordered.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from services.api.screening_off_exchange_routes import OffExchangeChecks
from services.api.screening_routes import SYMBOL, DilutionChecks
from services.market_data.calendar import NEW_YORK, UsEquityCalendar
from services.screening.candidates import read_watched, watch_path
from services.screening.material import stage2_by_symbol, stage2_verdict
from services.screening.stage4 import pick, request_note, rules_only
from services.screening.store import ScreeningStore
from services.screening.structure.service import StructureStore
from services.screening.structure.sessions import SessionFiller

LOGGER = logging.getLogger(__name__)
INTERVAL_SECONDS = 30.0
FIRST_DELAY_SECONDS = 60.0
#: The runner waits this long for an analysis before looking again on the next tick.
DILUTION_WAIT_SECONDS = 5.0
OFF_EXCHANGE_PER_TICK = 2


class Stage4Runner:
    def __init__(
        self,
        *,
        store: Callable[[], ScreeningStore | None],
        data_dir: Callable[[], Path | None],
        listing_for: Callable[[str], dict[str, Any] | None],
        dilution: DilutionChecks,
        off_exchange: OffExchangeChecks,
        structure: Callable[[], tuple[StructureStore, SessionFiller] | None],
        trades: Callable[[], Any],
        clock: Callable[[], datetime],
        condition_name: str,
        off_exchange_per_tick: int = OFF_EXCHANGE_PER_TICK,
    ) -> None:
        self._store = store
        self._data_dir = data_dir
        self._listing_for = listing_for
        self._dilution = dilution
        self._off_exchange = off_exchange
        self._structure = structure
        self._trades = trades
        self._clock = clock
        self._condition_name = condition_name
        self._per_tick = off_exchange_per_tick
        self._calendar = UsEquityCalendar("XNAS")

    def _candidates(self, screening: ScreeningStore, root: Path, window: Any) -> list[str]:
        cutoff = min(self._clock(), window.post_close_utc)
        watched = read_watched(watch_path(root), window, cutoff, self._condition_name)
        seen: dict[str, str] = {item.symbol: item.first_seen_utc for item in watched.symbols}
        for symbol, added in screening.manual(window.session_date):
            seen.setdefault(symbol, added)
        ordered = sorted(seen, key=lambda symbol: (seen[symbol], symbol))
        return [symbol for symbol in ordered if SYMBOL.fullmatch(symbol)]

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
        verdicts = stage2_by_symbol(screening, day)
        report: dict[str, Any] = {
            "session_date": day.isoformat(),
            "excluded": [],
            "unknown_symbol": [],
            "dilution_refreshed": [],
            "dilution_asked": None,
            "dilution_blocked": None,
            "off_exchange_ran": [],
            "off_exchange_skipped": None,
        }
        due: list[tuple[str, str, dict[str, Any]]] = []
        for symbol in self._candidates(screening, root, window):
            verdict = verdicts.get(symbol) or stage2_verdict(None)
            if verdict["outcome"] == "excluded_today":
                report["excluded"].append(symbol)
                continue
            listing = self._listing_for(symbol)
            if listing is None:
                report["unknown_symbol"].append(symbol)
                continue
            due.append((symbol, str(listing["instrument"]["instrument_id"]), verdict))
        if due:
            self._dilution_pass(screening, window, due, report, stop)
            self._off_exchange_pass(screening, window, due, report, stop)
        return report

    def _dilution_pass(
        self,
        screening: ScreeningStore,
        window: Any,
        due: list[tuple[str, str, dict[str, Any]]],
        report: dict[str, Any],
        stop: Callable[[], bool],
    ) -> None:
        by_symbol: dict[str, list[dict[str, Any]]] = {}
        for document in screening.documents(window.session_date, "dilution"):
            if rules_only(document):
                by_symbol.setdefault(str(document["symbol"]), []).append(document)
        waiting = False
        for symbol, _instrument_id, _verdict in due:
            chosen = pick(by_symbol.get(symbol, []))
            if chosen is None or chosen["status"] != "pending" or stop():
                continue
            fresh = self._dilution.refresh(screening, dict(chosen))
            if fresh["check_id"] != chosen["check_id"]:
                report["dilution_refreshed"].append(symbol)
            if fresh["status"] == "pending":
                waiting = True
        if waiting:
            report["dilution_blocked"] = "an analysis is still waiting"
            return
        for symbol, instrument_id, verdict in due:
            if symbol in by_symbol or stop():
                continue
            self._dilution.run(
                screening,
                window,
                symbol,
                instrument_id,
                False,
                {"stage4": request_note(verdict, self._clock())},
            )
            report["dilution_asked"] = symbol
            return

    def _off_exchange_pass(
        self,
        screening: ScreeningStore,
        window: Any,
        due: list[tuple[str, str, dict[str, Any]]],
        report: dict[str, Any],
        stop: Callable[[], bool],
    ) -> None:
        opened, adapter = self._structure(), self._trades()
        if opened is None:
            report["off_exchange_skipped"] = "storage_unconfigured"
            return
        if adapter is None or not hasattr(adapter, "trade_pages"):
            report["off_exchange_skipped"] = "alpaca_unconfigured"
            return
        _structure_store, filler = opened
        by_symbol: dict[str, list[dict[str, Any]]] = {}
        for document in screening.documents(window.session_date, "off_exchange"):
            by_symbol.setdefault(str(document["symbol"]), []).append(document)
        for symbol, instrument_id, verdict in due:
            if len(report["off_exchange_ran"]) >= self._per_tick or stop():
                return
            chosen = pick(by_symbol.get(symbol, []))
            if chosen is not None and (chosen["status"] != "pending" or filler.pending(symbol)):
                continue
            self._off_exchange.run(
                screening,
                opened,
                adapter,
                window,
                symbol,
                instrument_id,
                extra={"stage4": request_note(verdict, self._clock())},
            )
            report["off_exchange_ran"].append(symbol)

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
                    if report is not None and (
                        report["dilution_asked"]
                        or report["dilution_refreshed"]
                        or report["off_exchange_ran"]
                    ):
                        LOGGER.info("stage 4 %s", report)
                except Exception:  # noqa: BLE001 - a bad tick must not stop later ones
                    LOGGER.exception("stage 4 run failed")
                if stop.wait(interval):
                    return

        threading.Thread(target=loop, name="screening-stage4", daemon=True).start()
        return stop


def stage4_enabled() -> bool:
    """`SIW_SCREENING_STAGE4=0` turns the stage 4 runs off."""
    return os.getenv("SIW_SCREENING_STAGE4", "1").strip() != "0"
