"""A record of one Kiwoom condition, watched in realtime (ADR 0051).

The collector subscribes to the condition from premarket to the end of the aftermarket on
US session days and appends to `SIW_DATA_DIR/screeners/kiwoom_condition_watch.sqlite3`:
one watch per connection, the condition's list when it starts, and every enter/leave
event with the time SIW received it. Each subscription is renewed every RENEW_SECONDS:
on 2026-11-05 a subscription opened before the 18:00 KST premarket open stayed connected
but sent no event after it, while the list it watched changed completely. A renewal bounds
such a silence, and its fresh list shows what the events missed (`differs_from_previous`).
A watch that ends without its end being recorded (the process was killed or the machine
slept) is closed as `interrupted` the next time the store opens, so a gap in the record is
never mistaken for a quiet market.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from services.market_data.calendar import NEW_YORK, CalendarError, ExchangeCalendar
from services.market_data.kiwoom_conditions import (
    Condition,
    ConditionEvent,
    ConditionNotFoundError,
    ConditionTicker,
    DuplicateConditionError,
    KiwoomConditionSearch,
)
from services.market_data.provider import MarketDataError

MAX_DAYS_AHEAD = 14
MIN_RETRY_SECONDS = 5.0
MAX_RETRY_SECONDS = 300.0
# A watch that lasted this long resets the retry delay.
STEADY_SECONDS = 300.0
# A subscription is closed and opened again this often, even when nothing went wrong.
RENEW_SECONDS = 1800.0
# The first subscription of a window starts this long after premarket opens, so it is not
# opened just before Kiwoom turns the condition over to the new day.
START_DELAY_SECONDS = 60.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
    watch_id INTEGER PRIMARY KEY,
    condition_name TEXT NOT NULL,
    condition_seq TEXT NOT NULL,
    environment TEXT NOT NULL,
    started_at_utc TEXT NOT NULL,
    ended_at_utc TEXT,
    end_reason TEXT
);
CREATE TABLE IF NOT EXISTS watch_events (
    event_id INTEGER PRIMARY KEY,
    watch_id INTEGER NOT NULL REFERENCES watches(watch_id),
    received_at_utc TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('listed', 'enter', 'leave')),
    symbol TEXT NOT NULL,
    kiwoom_exchange TEXT NOT NULL,
    kiwoom_time TEXT,
    raw TEXT
);
CREATE INDEX IF NOT EXISTS watch_events_by_time ON watch_events (received_at_utc);
"""


def _utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class ConditionWatchStore:
    """Append-only: watches are opened and closed, events are only added."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()
        self._close_interrupted()

    @classmethod
    def in_data_dir(cls, data_dir: Path) -> ConditionWatchStore:
        return cls(data_dir / "screeners" / "kiwoom_condition_watch.sqlite3")

    def close(self) -> None:
        self._db.close()

    def _close_interrupted(self) -> None:
        # The last thing heard from an unclosed watch is the best known end.
        with self._lock:
            self._db.execute(
                "UPDATE watches SET end_reason = 'interrupted', ended_at_utc = COALESCE("
                "(SELECT MAX(received_at_utc) FROM watch_events e "
                "WHERE e.watch_id = watches.watch_id), started_at_utc) "
                "WHERE ended_at_utc IS NULL"
            )

    def start(
        self,
        condition: Condition,
        environment: str,
        at: datetime,
        tickers: tuple[ConditionTicker, ...],
    ) -> int:
        received = _utc(at)
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                cursor = self._db.execute(
                    "INSERT INTO watches (condition_name, condition_seq, environment, "
                    "started_at_utc) VALUES (?, ?, ?, ?)",
                    (condition.name, condition.seq, environment, received),
                )
                watch_id = int(cursor.lastrowid or 0)
                self._db.executemany(
                    "INSERT INTO watch_events (watch_id, received_at_utc, kind, symbol, "
                    "kiwoom_exchange) VALUES (?, ?, 'listed', ?, ?)",
                    [(watch_id, received, item.symbol, item.kiwoom_exchange) for item in tickers],
                )
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return watch_id

    def add(self, watch_id: int, at: datetime, event: ConditionEvent) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO watch_events (watch_id, received_at_utc, kind, symbol, "
                "kiwoom_exchange, kiwoom_time, raw) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    watch_id,
                    _utc(at),
                    event.kind,
                    event.symbol,
                    event.kiwoom_exchange,
                    event.kiwoom_time,
                    json.dumps(event.values, ensure_ascii=False, sort_keys=True),
                ),
            )

    def end(self, watch_id: int, at: datetime, reason: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE watches SET ended_at_utc = ?, end_reason = ? "
                "WHERE watch_id = ? AND ended_at_utc IS NULL",
                (_utc(at), reason[:300], watch_id),
            )

    def export(self, start: datetime, end: datetime) -> dict[str, Any]:
        """Every watch that overlaps [start, end) with its events, oldest first."""
        low, high = _utc(start), _utc(end)
        with self._lock:
            watches = self._db.execute(
                "SELECT * FROM watches WHERE started_at_utc < ? "
                "AND (ended_at_utc IS NULL OR ended_at_utc >= ?) ORDER BY watch_id",
                (high, low),
            ).fetchall()
            events = {
                int(item["watch_id"]): self._db.execute(
                    "SELECT * FROM watch_events WHERE watch_id = ? ORDER BY event_id",
                    (item["watch_id"],),
                ).fetchall()
                for item in watches
            }
        return {
            "kind": "kiwoom_condition_watch_export",
            "from_utc": low,
            "to_utc": high,
            "watches": [
                {
                    "watch_id": int(item["watch_id"]),
                    "condition": {"seq": item["condition_seq"], "name": item["condition_name"]},
                    "environment": item["environment"],
                    "started_at_utc": item["started_at_utc"],
                    "ended_at_utc": item["ended_at_utc"],
                    "end_reason": item["end_reason"],
                    "differs_from_previous": self._differs(item),
                    "events": [
                        {
                            "received_at_utc": row["received_at_utc"],
                            "kind": row["kind"],
                            "symbol": row["symbol"],
                            "kiwoom_exchange": row["kiwoom_exchange"],
                            "kiwoom_time": row["kiwoom_time"],
                            "raw": json.loads(row["raw"]) if row["raw"] else None,
                        }
                        for row in events[int(item["watch_id"])]
                        if low <= row["received_at_utc"] < high
                    ],
                }
                for item in watches
            ],
        }

    def _differs(self, watch: sqlite3.Row) -> dict[str, list[str]] | None:
        """How this watch's list differs from where the previous watch of the same condition
        left off (its list plus its events): changes no event reported. None for the first
        watch. Called with the lock held."""
        previous = self._db.execute(
            "SELECT watch_id FROM watches WHERE watch_id < ? AND condition_name = ? "
            "AND environment = ? ORDER BY watch_id DESC LIMIT 1",
            (watch["watch_id"], watch["condition_name"], watch["environment"]),
        ).fetchone()
        if previous is None:
            return None
        expected: set[str] = set()
        for row in self._db.execute(
            "SELECT kind, symbol FROM watch_events WHERE watch_id = ? ORDER BY event_id",
            (previous["watch_id"],),
        ):
            if row["kind"] == "leave":
                expected.discard(row["symbol"])
            else:
                expected.add(row["symbol"])
        listed = {
            row["symbol"]
            for row in self._db.execute(
                "SELECT symbol FROM watch_events WHERE watch_id = ? AND kind = 'listed'",
                (watch["watch_id"],),
            )
        }
        return {"entered": sorted(listed - expected), "left": sorted(expected - listed)}


def window_around(moment: datetime, calendar: ExchangeCalendar) -> tuple[datetime, datetime]:
    """The watch window containing `moment`, or the next one: premarket open to aftermarket
    close of a US session (04:00-20:00 New York time, 17:00 on an early close). Days without
    a session, such as exchange holidays, are skipped."""
    day = moment.astimezone(NEW_YORK).date()
    for _ in range(MAX_DAYS_AHEAD):
        window = calendar.window_for_date(day)
        if window is not None and moment < window.post_close_utc:
            return window.pre_open_utc, window.post_close_utc
        day += timedelta(days=1)
    raise CalendarError(f"no US session within {MAX_DAYS_AHEAD} days of {_utc(moment)}")


@dataclass(frozen=True)
class WatchOutcome:
    reason: str
    error: str | None = None
    fatal: bool = False  # retrying cannot help: the name is not saved, or saved twice


def watch_once(
    search: KiwoomConditionSearch,
    store: ConditionWatchStore,
    name: str,
    environment: str,
    *,
    until: datetime,
    stop: threading.Event,
    clock: Callable[[], datetime],
    renew_at: datetime | None = None,
) -> WatchOutcome:
    """One connection: subscribe, record, and close the watch with why it ended. It ends at
    `until` (the window's close) or, earlier, at `renew_at` as `renewed`."""
    deadline = until if renew_at is None else min(until, renew_at)
    watch: list[int] = []

    def on_list(condition: Condition, tickers: tuple[ConditionTicker, ...]) -> None:
        watch.append(store.start(condition, environment, clock(), tickers))

    def on_event(event: ConditionEvent) -> None:
        store.add(watch[0], clock(), event)

    try:
        search.watch(
            name,
            on_list=on_list,
            on_event=on_event,
            until=lambda: stop.is_set() or clock() >= deadline,
        )
    except MarketDataError as exc:
        error = f"{type(exc).__name__}: {exc}"
        if watch:
            store.end(watch[0], clock(), error)
        fatal = isinstance(exc, (ConditionNotFoundError, DuplicateConditionError))
        return WatchOutcome("error", error, fatal)
    reason = "window_closed" if clock() >= until else "renewed"
    if stop.is_set():
        reason = "stopped"
    if watch:
        store.end(watch[0], clock(), reason)
    return WatchOutcome(reason)


def run_watch(
    search: KiwoomConditionSearch,
    store: ConditionWatchStore,
    name: str,
    environment: str,
    *,
    stop: threading.Event,
    clock: Callable[[], datetime],
    report: Callable[[dict[str, object]], None],
    calendar: ExchangeCalendar | None = None,
) -> int:
    """Watch `name` in every window until `stop` is set. Returns 2 for a condition that is
    not saved (or saved twice), which retrying cannot fix; otherwise 0 when stopped."""
    calendar = calendar or ExchangeCalendar("XNYS")
    delay = MIN_RETRY_SECONDS
    while not stop.is_set():
        start, end = window_around(clock(), calendar)
        start += timedelta(seconds=START_DELAY_SECONDS)
        now = clock()
        if now < start:
            report({"status": "waiting", "window_start_utc": _utc(start)})
            stop.wait((start - now).total_seconds())
            continue
        report({"status": "watching", "condition": name, "window_end_utc": _utc(end)})
        began = clock()
        outcome = watch_once(
            search,
            store,
            name,
            environment,
            until=end,
            stop=stop,
            clock=clock,
            renew_at=began + timedelta(seconds=RENEW_SECONDS),
        )
        if outcome.error is None:
            delay = MIN_RETRY_SECONDS
            continue
        if outcome.fatal:
            report({"status": "error", "error": outcome.error})
            return 2
        if (clock() - began).total_seconds() >= STEADY_SECONDS:
            delay = MIN_RETRY_SECONDS
        report({"status": "retrying", "error": outcome.error, "retry_seconds": delay})
        stop.wait(delay)
        delay = min(delay * 2, MAX_RETRY_SECONDS)
    report({"status": "stopped"})
    return 0
