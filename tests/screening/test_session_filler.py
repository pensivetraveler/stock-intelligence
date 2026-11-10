"""The shared background session reader (ADR 0053, ADR 0052 §5) and the after-close
candidate prefetch. Made-up trades only; no test reaches the network."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from services.api.screening_prefetch import CandidatePrefetch
from services.market_data.calendar import UsEquityCalendar
from services.screening.store import ScreeningStore
from services.screening.structure.service import open_structure
from services.screening.structure.sessions import (
    FillLimits,
    SessionCache,
    SessionFiller,
    closed_sessions,
)

CALENDAR = UsEquityCalendar("XNAS")
#: Monday 2026-11-09 21:00 ET: the Monday session has closed (post-close plus delay).
NOW = datetime(2026, 11, 10, 2, 0, tzinfo=timezone.utc)


def trades(symbol: str, start: datetime, end: datetime, sort: str = "asc") -> Iterator[Any]:
    stamp = (start + timedelta(hours=6)).isoformat().replace("+00:00", "Z")
    yield [{"t": stamp, "x": "D", "p": 2, "s": 100, "c": ["@"], "z": "C"}]


def memory_cache() -> SessionCache:
    return SessionCache(sqlite3.connect(":memory:", check_same_thread=False), threading.Lock())


def test_sessions_are_read_by_several_workers_at_once() -> None:
    active, peak = [0], [0]
    lock, release = threading.Lock(), threading.Event()

    def counted(*args: Any, **kwargs: Any) -> Iterator[Any]:
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        release.wait(5)
        yield from trades(*args, **kwargs)
        with lock:
            active[0] -= 1

    cache = memory_cache()
    filler = SessionFiller(cache, lambda: counted, lambda: NOW, FillLimits(workers=3))
    windows = closed_sessions(CALENDAR, NOW, 5)
    filler.request("STAK", windows)
    deadline = datetime.now() + timedelta(seconds=5)
    while peak[0] < 3 and datetime.now() < deadline:
        threading.Event().wait(0.01)
    release.set()
    filler.wait("STAK", 5, lambda seconds: threading.Event().wait(seconds))
    assert peak[0] == 3
    assert filler.pending("STAK") == 0
    assert all(cache.get("STAK", w.session_date) for w in windows)


def test_a_screen_request_goes_ahead_of_the_prefetch() -> None:
    order: list[tuple[str, date]] = []
    first, release = threading.Event(), threading.Event()

    def recorded(symbol: str, start: datetime, end: datetime, sort: str = "asc") -> Iterator[Any]:
        order.append((symbol, start.date()))
        first.set()
        release.wait(5)
        yield from trades(symbol, start, end, sort)

    filler = SessionFiller(memory_cache(), lambda: recorded, lambda: NOW, FillLimits(workers=1))
    one = closed_sessions(CALENDAR, NOW, 1)
    two = closed_sessions(CALENDAR, NOW, 2)
    filler.request("AAA", one, background=True)  # being read: the worker is busy
    first.wait(5)
    filler.request("BBB", two, background=True)
    filler.request("CCC", two)  # the screen: to the front, newest first
    filler.request("BBB", one)  # asked on screen while waiting behind the prefetch: moved up
    release.set()
    for symbol in ("AAA", "BBB", "CCC"):
        filler.wait(symbol, 5, lambda seconds: threading.Event().wait(seconds))
    newest, older = two[1].session_date, two[0].session_date
    assert [item[0] for item in order] == ["AAA", "BBB", "CCC", "CCC", "BBB"]
    assert order[1][1] == newest  # BBB's newest session, moved up
    assert [day for _symbol, day in order[2:4]] == [newest, older]
    assert order[4][1] == older


def test_prefetch_queues_the_closed_sessions_candidates_once(tmp_path: Path) -> None:
    screening = ScreeningStore(tmp_path / "screening.sqlite3", lambda: NOW)
    structure = open_structure(tmp_path / "screening_structure.sqlite3", lambda: NOW)
    seen: list[str] = []

    def recorded(symbol: str, start: datetime, end: datetime, sort: str = "asc") -> Iterator[Any]:
        seen.append(symbol)
        yield from trades(symbol, start, end, sort)

    class Adapter:
        trade_pages = staticmethod(recorded)

        class config:  # noqa: N801 - the adapter's attribute name
            publication_delay_minutes = 15

    filler = SessionFiller(structure.cache, lambda: recorded, lambda: NOW, inline=True)
    monday = date(2026, 11, 9)
    screening.add_manual(monday, "STAK")
    screening.add_manual(monday - timedelta(days=3), "OLD")  # another session's candidate
    prefetch = CandidatePrefetch(
        store=lambda: screening,
        structure=lambda: (structure, filler),
        data_dir=lambda: tmp_path,  # no condition record here: the manual candidates only
        trades=lambda: Adapter(),
        clock=lambda: NOW,
        condition_name="SIW_당일급등주",
    )
    queued = prefetch.tick()
    assert queued == {"session_date": "2026-11-09", "symbols": ["STAK"]}
    assert set(seen) == {"STAK"} and len(seen) == 20
    windows = closed_sessions(CALENDAR, NOW - timedelta(minutes=15), 20)
    assert windows[-1].session_date == monday
    assert all(structure.cache.get("STAK", w.session_date) for w in windows)
    assert prefetch.tick() is None  # once per closed session
    screening.close()


def test_prefetch_waits_for_the_session_to_close(tmp_path: Path) -> None:
    screening = ScreeningStore(tmp_path / "screening.sqlite3", lambda: NOW)
    structure = open_structure(tmp_path / "screening_structure.sqlite3", lambda: NOW)
    filler = SessionFiller(structure.cache, lambda: trades, lambda: NOW, inline=True)

    class Adapter:
        trade_pages = staticmethod(trades)

        class config:  # noqa: N801
            publication_delay_minutes = 15

    # 20:10 ET Monday: closed at 20:00, but not past the 15-minute publication delay.
    early = datetime(2026, 11, 10, 1, 10, tzinfo=timezone.utc)
    prefetch = CandidatePrefetch(
        store=lambda: screening,
        structure=lambda: (structure, filler),
        data_dir=lambda: tmp_path,
        trades=lambda: Adapter(),
        clock=lambda: early,
        condition_name="SIW_당일급등주",
    )
    queued = prefetch.tick()
    assert queued is not None and queued["session_date"] == "2026-11-06"
    assert prefetch.tick() is None
    screening.close()
