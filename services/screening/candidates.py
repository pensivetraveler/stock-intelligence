"""Candidates from the realtime condition record (ADR 0052 S1, reading ADR 0051's file).

The record is read through a read-only SQLite connection. Opening it with
`ConditionWatchStore` would write (schema, and closing an unended watch as interrupted),
which must not happen while the collector is recording. Only events received before the
cutoff count, so a screen run later for an earlier time sees what SIW had seen by then.

ADR 0055 (U0) adds what a momentum list needs from the same record: the stretches of the
window with no watch (`gaps`), whether a symbol's entry time is known or only bounded (a
symbol first seen in a watch's opening list entered at some unknown time before it), and
how often a symbol entered and how long it stayed in the list.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from services.market_data.calendar import SessionWindow

# A pause between two watches longer than this is a gap: a renewal takes well under a
# second (0.3 s on 2026-11-09).
GAP_SECONDS = 5.0
# The collector renews every watch after 30 minutes (ADR 0051). A watch with no recorded end
# that started longer ago than this is not running: its process died and the store has not
# been opened since to close it as interrupted.
STALE_SECONDS = 1800.0 + 120.0

FirstSeenHow = Literal["enter_event", "first_list", "list_after_gap", "list_on_renewal"]
GapReason = Literal["before_first_watch", "between_watches", "not_recording", "no_record"]


def utc_text(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


@dataclass
class WatchedSymbol:
    symbol: str
    kiwoom_exchange: str
    first_seen_utc: str
    # Whether the symbol was in the list when the last watch before the cutoff last heard.
    in_list: bool
    # How SIW first saw it: an enter event (the entry time is known to the second), or a
    # watch's opening list (it entered at an unknown time after `entered_after_utc`).
    first_seen_how: FirstSeenHow = "enter_event"
    entered_after_utc: str | None = None
    enter_count: int = 0
    seconds_in_list: float = 0.0


@dataclass
class WatchGap:
    """A stretch of the window before the cutoff that no watch covered."""

    from_utc: str
    to_utc: str
    reason: GapReason
    # Why the watch before the gap ended (`interrupted`, an error, `stopped`), if any.
    previous_end: str | None = None


@dataclass
class WatchCoverage:
    """How much of the window the record covers, so a gap is not read as a quiet list."""

    watches: int = 0
    interrupted: int = 0
    first_started_utc: str | None = None
    last_heard_utc: str | None = None
    open_now: bool = False
    gaps: list[WatchGap] = field(default_factory=list)


@dataclass
class WatchRead:
    recorded: bool
    coverage: WatchCoverage = field(default_factory=WatchCoverage)
    symbols: list[WatchedSymbol] = field(default_factory=list)


def watch_path(data_dir: Path) -> Path:
    return data_dir / "screeners" / "kiwoom_condition_watch.sqlite3"


def _gap(start: datetime, end: datetime) -> bool:
    return (end - start).total_seconds() > GAP_SECONDS


def read_watched(
    path: Path, window: SessionWindow, cutoff: datetime, condition_name: str
) -> WatchRead:
    """The symbols the condition listed or added in the session window before `cutoff`."""
    low_time = window.pre_open_utc
    high_time = min(cutoff, window.post_close_utc)
    if not path.exists():
        coverage = WatchCoverage()
        if _gap(low_time, high_time):
            coverage.gaps.append(WatchGap(utc_text(low_time), utc_text(high_time), "no_record"))
        return WatchRead(recorded=False, coverage=coverage)
    low, high = utc_text(low_time), utc_text(high_time)
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
    covered_until = low_time  # the end of what the watches so far heard
    previous: dict[str, str | None] | None = None
    for index, watch in enumerate(watches):
        coverage.first_started_utc = coverage.first_started_utc or watch["started_at_utc"]
        if watch["end_reason"] == "interrupted":
            coverage.interrupted += 1
        started = max(_parse(watch["started_at_utc"]), low_time)
        after_gap = _gap(covered_until, started)
        if after_gap:
            coverage.gaps.append(
                WatchGap(
                    utc_text(covered_until),
                    utc_text(started),
                    "before_first_watch" if index == 0 else "between_watches",
                    None if previous is None else previous["end_reason"],
                )
            )
        # A watch that did not end as renewed may have missed changes before it ended.
        broken = previous is not None and previous["end_reason"] != "renewed"
        if previous is None:
            how: FirstSeenHow = "first_list"
            entered_after = utc_text(low_time)
        elif after_gap or broken:
            how, entered_after = "list_after_gap", utc_text(covered_until)
        else:
            how = "list_on_renewal"
            entered_after = utc_text(max(_parse(str(previous["started_at_utc"])), low_time))
        ended = watch["ended_at_utc"]
        end_reason = watch["end_reason"]
        rows = events[int(watch["watch_id"])]
        if ended is None and (high_time - started).total_seconds() > STALE_SECONDS:
            # The best known end, as ConditionWatchStore would record it.
            ended = max([watch["started_at_utc"], *(str(r["received_at_utc"]) for r in rows)])
            end_reason = "unclosed"
            coverage.interrupted += 1
        coverage.open_now = ended is None or ended >= high
        heard = min(ended, high) if ended is not None else high
        heard_time = _parse(heard)
        coverage.last_heard_utc = max(coverage.last_heard_utc or heard, heard)
        # A new watch starts from its own list, not from where the previous one ended.
        members = set()
        since: dict[str, datetime] = {}
        for row in rows:
            symbol = str(row["symbol"])
            moment = _parse(str(row["received_at_utc"]))
            if row["kind"] == "leave":
                if symbol in since:
                    found[symbol].seconds_in_list += (moment - since.pop(symbol)).total_seconds()
                members.discard(symbol)
                continue
            if symbol not in found:
                item = WatchedSymbol(
                    symbol, str(row["kiwoom_exchange"]), str(row["received_at_utc"]), True
                )
                if row["kind"] == "listed":
                    item.first_seen_how, item.entered_after_utc = how, entered_after
                found[symbol] = item
            if row["kind"] == "enter":
                found[symbol].enter_count += 1
            members.add(symbol)
            since.setdefault(symbol, max(moment, low_time))
        for symbol, start in since.items():
            found[symbol].seconds_in_list += max((heard_time - start).total_seconds(), 0.0)
        covered_until = max(covered_until, heard_time)
        previous = {"started_at_utc": watch["started_at_utc"], "end_reason": end_reason}
    if _gap(covered_until, high_time):
        coverage.gaps.append(
            WatchGap(
                utc_text(covered_until),
                high,
                "not_recording" if watches else "no_record",
                None if previous is None else previous["end_reason"],
            )
        )
    for item in found.values():
        item.in_list = item.symbol in members
    ordered = sorted(found.values(), key=lambda item: (item.first_seen_utc, item.symbol))
    return WatchRead(recorded=True, coverage=coverage, symbols=ordered)
