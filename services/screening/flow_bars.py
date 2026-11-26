"""Session 1m bars for the stage 3 flow watch, refetching only the unsettled tail.

The flow watch reads every candidate's bars from the premarket open to the newest closed
minute once a minute. While the session runs the last minutes are never final, so the
market data cache cannot serve that range and every call used to fetch, store, read back
and contract-check the whole day again (about 1.7 s of CPU per symbol late in the day,
nearly all of it schema validation; 77 symbols took 89-347 s, mondash -187).

`SettledBarsMemo` keeps, per instrument and session, the leading run of minutes that can
no longer change: final bars and gaps with a settled reason (no trades). The market data
cache relies on the same rule (`CachedMarketDataProvider._is_reusable`). Each call fetches
only from the first unsettled minute to the requested end and joins the two. When the
source, feed, adjustment or revision of the tail differs from the remembered head, the
memo is dropped and the whole range is fetched again: bars from different sources or
revisions are never joined.

Nothing new is stored: the memo is in memory, rebuilt from the provider after a restart.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from services.market_data.provider import SETTLED_MISSING_REASONS, BarsQuery, BarsResult

MINUTE = timedelta(minutes=1)
MAX_SESSIONS = 3
GetBars = Callable[[BarsQuery], BarsResult]
IDENTITY_KEYS = ("source_id", "source_feed_scope", "adjustment", "dataset_revision")


def _parse(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


@dataclass(frozen=True)
class _Head:
    """The settled minutes of `[start, until)`: final bars and settled gaps."""

    start: datetime
    until: datetime
    identity: tuple[Any, ...]
    bars: tuple[dict[str, Any], ...]
    missing: tuple[dict[str, Any], ...]
    coverage: dict[str, Any]


def _settled_head(query: BarsQuery, result: BarsResult) -> _Head:
    """The leading settled part of `result`, the answer to `query`."""
    coverage = result.coverage
    events: list[tuple[datetime, datetime, bool, dict[str, Any], bool]] = []
    for bar in result.bars:
        start = _parse(str(bar["interval_start_utc"]))
        final = bool(bar.get("is_final", False))
        events.append((start, start + MINUTE, True, bar, final))
    for gap in coverage.get("missing_intervals", []):
        settled = gap.get("reason") in SETTLED_MISSING_REASONS
        start, end = _parse(str(gap["start_utc"])), _parse(str(gap["end_utc"]))
        events.append((start, end, False, gap, settled))
    events.sort(key=lambda item: item[0])
    # Settled only while every minute is accounted for, in order, by something settled:
    # a minute that is neither a bar nor a gap ends the head (never assumed empty).
    until = query.start_utc
    bars: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for start, end, is_bar, item, settled in events:
        if start != until or not settled:
            break
        (bars if is_bar else missing).append(item)
        until = end
    return _Head(
        start=query.start_utc,
        until=until,
        identity=tuple(coverage.get(key) for key in IDENTITY_KEYS),
        bars=tuple(bars),
        missing=tuple(missing),
        coverage=coverage,
    )


class SettledBarsMemo:
    """`get_bars` for 1m session ranges that reuses the settled head of earlier reads."""

    def __init__(self) -> None:
        self._heads: dict[tuple[str, datetime], _Head] = {}
        self._sessions: list[datetime] = []
        self._lock = threading.Lock()
        #: Upstream reads by kind, for tests and the measurements in the ADR.
        self.reads = {"full": 0, "tail": 0, "none": 0}

    def _count(self, kind: str) -> None:
        with self._lock:
            self.reads[kind] += 1

    def use_session(self, start: datetime) -> None:
        """Mark the session starting at `start` as used; forget the least recently used
        sessions beyond `MAX_SESSIONS` (a past day opened in the panel stays briefly)."""
        with self._lock:
            if start in self._sessions:
                self._sessions.remove(start)
            self._sessions.append(start)
            while len(self._sessions) > MAX_SESSIONS:
                gone = self._sessions.pop(0)
                for key in [key for key in self._heads if key[1] == gone]:
                    del self._heads[key]

    def bars(self, get_bars: GetBars) -> GetBars:
        def read(query: BarsQuery) -> BarsResult:
            return self.get(get_bars, query)

        return read

    def get(self, get_bars: GetBars, query: BarsQuery) -> BarsResult:
        if query.timeframe != "1m" or query.as_of_utc is not None:
            return get_bars(query)
        key = (query.instrument_id, query.start_utc)
        with self._lock:
            head = self._heads.get(key)
        if head is None or head.until <= query.start_utc:
            return self._full(get_bars, query, key)
        if head.until >= query.end_utc:
            self._count("none")
            return self._join(query, head, None)
        tail = get_bars(replace(query, start_utc=head.until))
        self._count("tail")
        if tuple(tail.coverage.get(k) for k in IDENTITY_KEYS) != head.identity:
            # Another source or revision: never join; read the whole range again.
            return self._full(get_bars, query, key)
        joined = self._join(query, head, tail)
        self._remember(key, query, joined)
        return joined

    def _full(
        self,
        get_bars: GetBars,
        query: BarsQuery,
        key: tuple[str, datetime],
    ) -> BarsResult:
        result = get_bars(query)
        self._count("full")
        self._remember(key, query, result)
        return result

    def _remember(self, key: tuple[str, datetime], query: BarsQuery, result: BarsResult) -> None:
        head = _settled_head(query, result)
        with self._lock:
            known = self._heads.get(key)
            if known is None or head.until >= known.until or known.identity != head.identity:
                self._heads[key] = head

    @staticmethod
    def _join(query: BarsQuery, head: _Head, tail: BarsResult | None) -> BarsResult:
        bars = [b for b in head.bars if _parse(str(b["interval_start_utc"])) < query.end_utc]
        missing = [g for g in head.missing if _parse(str(g["end_utc"])) <= query.end_utc]
        coverage = dict(tail.coverage if tail is not None else head.coverage)
        if tail is not None:
            bars.extend(tail.bars)
            missing.extend(tail.coverage.get("missing_intervals", []))
        coverage["requested_start_utc"] = head.coverage.get(
            "requested_start_utc", coverage.get("requested_start_utc")
        )
        coverage["requested_end_utc"] = query.end_utc.isoformat().replace("+00:00", "Z")
        coverage["missing_intervals"] = missing
        coverage["observed_interval_count"] = len(bars)
        coverage["joined_from_settled_head"] = head.until.isoformat().replace("+00:00", "Z")
        return BarsResult(tuple(bars), coverage)
