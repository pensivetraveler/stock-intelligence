"""Candidates from the realtime condition record (ADR 0052 S1, reading ADR 0051's file).

The record is read through a read-only SQLite connection. Opening it with
`ConditionWatchStore` would write (schema, and closing an unended watch as interrupted),
which must not happen while the collector is recording. Only events received before the
cutoff count, so a screen run later for an earlier time sees what SIW had seen by then.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from services.market_data.calendar import SessionWindow


def utc_text(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class WatchedSymbol:
    symbol: str
    kiwoom_exchange: str
    first_seen_utc: str
    # Whether the symbol was in the list when the last watch before the cutoff last heard.
    in_list: bool


@dataclass
class WatchCoverage:
    """How much of the window the record covers, so a gap is not read as a quiet list."""

    watches: int = 0
    interrupted: int = 0
    first_started_utc: str | None = None
    last_heard_utc: str | None = None
    open_now: bool = False


@dataclass
class WatchRead:
    recorded: bool
    coverage: WatchCoverage = field(default_factory=WatchCoverage)
    symbols: list[WatchedSymbol] = field(default_factory=list)


def watch_path(data_dir: Path) -> Path:
    return data_dir / "screeners" / "kiwoom_condition_watch.sqlite3"


def read_watched(
    path: Path, window: SessionWindow, cutoff: datetime, condition_name: str
) -> WatchRead:
    """The symbols the condition listed or added in the session window before `cutoff`."""
    if not path.exists():
        return WatchRead(recorded=False)
    low = utc_text(window.pre_open_utc)
    high = utc_text(min(cutoff, window.post_close_utc))
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        watches = db.execute(
            "SELECT watch_id, started_at_utc, ended_at_utc, end_reason FROM watches "
            "WHERE condition_name = ? AND started_at_utc < ? "
            "AND (ended_at_utc IS NULL OR ended_at_utc >= ?) ORDER BY watch_id",
            (condition_name, high, low),
        ).fetchall()
        events = {
            int(watch["watch_id"]): db.execute(
                "SELECT received_at_utc, kind, symbol, kiwoom_exchange FROM watch_events "
                "WHERE watch_id = ? AND received_at_utc < ? ORDER BY event_id",
                (watch["watch_id"], high),
            ).fetchall()
            for watch in watches
        }
    finally:
        db.close()

    coverage = WatchCoverage(watches=len(watches))
    found: dict[str, WatchedSymbol] = {}
    members: set[str] = set()
    for watch in watches:
        coverage.first_started_utc = coverage.first_started_utc or watch["started_at_utc"]
        if watch["end_reason"] == "interrupted":
            coverage.interrupted += 1
        ended = watch["ended_at_utc"]
        coverage.open_now = ended is None or ended >= high
        heard = min(ended, high) if ended is not None else high
        coverage.last_heard_utc = max(coverage.last_heard_utc or heard, heard)
        # A new watch starts from its own list, not from where the previous one ended.
        members = set()
        for row in events[int(watch["watch_id"])]:
            symbol = str(row["symbol"])
            if row["kind"] == "leave":
                members.discard(symbol)
                continue
            members.add(symbol)
            if symbol not in found:
                found[symbol] = WatchedSymbol(
                    symbol, str(row["kiwoom_exchange"]), str(row["received_at_utc"]), True
                )
    for item in found.values():
        item.in_list = item.symbol in members
    ordered = sorted(found.values(), key=lambda item: (item.first_seen_utc, item.symbol))
    return WatchRead(recorded=True, coverage=coverage, symbols=ordered)
