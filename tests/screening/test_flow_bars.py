"""The flow watch's settled-head memo (mondash -187): fetch only the unsettled tail."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from services.market_data.provider import BarsQuery, BarsResult
from services.screening.flow_bars import SettledBarsMemo

START = datetime(2026, 11, 24, 9, 0, tzinfo=timezone.utc)  # 04:00 ET
IID = "us:abcd:xnas:2026"


def _z(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


class Upstream:
    """Alpaca-like answers: a bar or a gap for every minute; the last 15 are unsettled."""

    def __init__(self, revision: str = "r1", hole_at: int | None = None) -> None:
        self.now = START
        self.revision = revision
        self.hole_at = hole_at
        self.queries: list[BarsQuery] = []

    def __call__(self, query: BarsQuery) -> BarsResult:
        self.queries.append(query)
        final_before = self.now - timedelta(minutes=15)
        bars: list[dict[str, Any]] = []
        missing: list[dict[str, Any]] = []
        minute = query.start_utc
        while minute < query.end_utc:
            index = int((minute - START).total_seconds() // 60)
            end = minute + timedelta(minutes=1)
            if index == self.hole_at:
                pass  # a minute the provider does not account for
            elif index % 4 == 0:
                reason = "provider_no_bar" if end <= final_before else "not_yet_final"
                missing.append({"start_utc": _z(minute), "end_utc": _z(end), "reason": reason})
            else:
                bars.append(
                    {
                        "interval_start_utc": _z(minute),
                        "close": str(index),
                        "is_final": end <= final_before,
                    }
                )
            minute = end
        coverage = {
            "source_id": "alpaca",
            "source_feed_scope": "alpaca-sip",
            "adjustment": "raw",
            "dataset_revision": self.revision,
            "requested_start_utc": _z(query.start_utc),
            "requested_end_utc": _z(query.end_utc),
            "missing_intervals": missing,
        }
        return BarsResult(tuple(bars), coverage)


def _query(minutes: int) -> BarsQuery:
    return BarsQuery(IID, "1m", START, START + timedelta(minutes=minutes))


def _same(left: BarsResult, right: BarsResult) -> None:
    assert left.bars == right.bars
    assert left.coverage["missing_intervals"] == right.coverage["missing_intervals"]
    for key in ("source_id", "source_feed_scope", "adjustment", "dataset_revision"):
        assert left.coverage[key] == right.coverage[key]


def test_later_calls_fetch_only_the_unsettled_tail_and_match_a_full_read() -> None:
    upstream, memo = Upstream(), SettledBarsMemo()
    read = memo.bars(upstream)
    for minutes in (300, 301, 302, 310):
        upstream.now = START + timedelta(minutes=minutes, seconds=20)
        joined = read(_query(minutes))
        _same(joined, _full(upstream, minutes))
    starts = [q.start_utc for q in upstream.queries if q.end_utc != q.start_utc]
    # The first call reads the day; then each starts at the first unsettled minute.
    assert starts[0] == START
    assert all(start > START + timedelta(minutes=280) for start in starts[1:])
    assert memo.reads == {"full": 1, "tail": 3, "none": 0}


def _full(upstream: Upstream, minutes: int) -> BarsResult:
    fresh = Upstream(upstream.revision, upstream.hole_at)
    fresh.now = upstream.now
    return fresh(_query(minutes))


def test_a_settled_range_is_served_without_reading_again() -> None:
    upstream, memo = Upstream(), SettledBarsMemo()
    upstream.now = START + timedelta(hours=20)  # long after the session
    read = memo.bars(upstream)
    first = read(_query(960))
    again = read(_query(960))
    _same(first, again)
    assert len(upstream.queries) == 1
    assert memo.reads == {"full": 1, "tail": 0, "none": 1}


def test_another_revision_is_never_joined() -> None:
    upstream, memo = Upstream(), SettledBarsMemo()
    read = memo.bars(upstream)
    upstream.now = START + timedelta(minutes=100, seconds=20)
    read(_query(100))
    upstream.revision = "r2"
    upstream.now = START + timedelta(minutes=101, seconds=20)
    joined = read(_query(101))
    assert joined.coverage["dataset_revision"] == "r2"
    assert upstream.queries[-1].start_utc == START  # read whole again
    _same(joined, _full(upstream, 101))
    assert memo.reads["full"] == 2


def test_a_minute_nobody_accounted_for_ends_the_settled_head() -> None:
    upstream, memo = Upstream(hole_at=50), SettledBarsMemo()
    read = memo.bars(upstream)
    upstream.now = START + timedelta(minutes=100, seconds=20)
    read(_query(100))
    upstream.now = START + timedelta(minutes=101, seconds=20)
    read(_query(101))
    # The tail starts at the hole, not after it: an unexplained minute is not "empty".
    assert upstream.queries[-1].start_utc == START + timedelta(minutes=50)


def test_old_sessions_are_forgotten() -> None:
    upstream, memo = Upstream(), SettledBarsMemo()
    upstream.now = START + timedelta(days=10)
    for day in range(5):
        start = START + timedelta(days=day)
        memo.use_session(start)
        memo.get(upstream, BarsQuery(IID, "1m", start, start + timedelta(minutes=10)))
    assert {key[1] for key in memo._heads} == {START + timedelta(days=d) for d in (2, 3, 4)}


def test_point_in_time_reads_pass_through() -> None:
    upstream, memo = Upstream(), SettledBarsMemo()
    upstream.now = START + timedelta(minutes=100)
    query = BarsQuery(
        IID, "1m", START, START + timedelta(minutes=60), START + timedelta(minutes=70)
    )
    memo.get(upstream, query)
    memo.get(upstream, query)
    assert len(upstream.queries) == 2
    assert memo.reads == {"full": 0, "tail": 0, "none": 0}
