"""The SIP status recorder (`siw-collector watch-halts`) and what the flow watch reads from it.
A fake connection stands in for Alpaca; no test reaches the network."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from services.collector.halt_watch import (
    LEAD_SECONDS,
    StatusWatchStore,
    listen_once,
    run_halt_watch,
    store_path,
    window_around,
)
from services.market_data.calendar import ExchangeCalendar
from services.screening.halts import recorded_halts
from tests.screening.test_screening import WINDOW

T0 = WINDOW.pre_open_utc  # 2026-11-09 09:00 UTC, 04:00 New York


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def text(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def status(symbol: str, code: str, moment: datetime, tape: str = "B") -> dict[str, Any]:
    return {
        "T": "s",
        "S": symbol,
        "sc": code,
        "sm": "Trading Halt" if code in {"2", "H"} else "Resume",
        "rc": "T1" if code in {"2", "H"} else "",
        "rm": "News pending" if code in {"2", "H"} else "",
        "t": text(moment)[:-1] + ".123456789Z",
        "z": tape,
    }


class Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now


def test_store_keeps_changes_and_counts_repeats(tmp_path: Path) -> None:
    store = StatusWatchStore(tmp_path / "s.sqlite3")
    listen = store.start("sip", at(0))
    assert store.record(listen, status("VEMB", "2", at(1)), at(1))
    for second in (5, 10, 15):
        assert not store.record(
            listen, status("VEMB", "2", at(1)), at(1) + timedelta(seconds=second)
        )
    assert store.record(listen, status("VEMB", "3", at(3)), at(3))
    assert not store.record(listen, {"T": "s", "S": "X"}, at(3))  # no code, no time
    store.end(listen, at(4), "stopped")
    document = store.export(at(-60), at(60))
    store.close()
    halt, resume = document["changes"]
    assert halt["repeats"] == 3 and halt["last_seen_utc"] == "2026-11-09T09:01:15.000Z"
    assert resume["status_code"] == "3" and "raw" not in resume
    assert document["listens"][0]["end_reason"] == "stopped"


def test_unclosed_listen_is_closed_as_interrupted_at_its_last_heartbeat(tmp_path: Path) -> None:
    store = StatusWatchStore(tmp_path / "s.sqlite3")
    listen = store.start("sip", at(0))
    store.heard(listen, at(7))
    store.close()  # killed: no end written
    reopened = StatusWatchStore(tmp_path / "s.sqlite3")
    item = reopened.export(at(-1), at(60))["listens"][0]
    reopened.close()
    assert (
        item["end_reason"] == "interrupted" and item["ended_at_utc"] == "2026-11-09T09:07:00.000Z"
    )


class FakeConnection:
    def __init__(self, frames: list[Any], stop: threading.Event, clock: Clock) -> None:
        self.frames = deque(json.dumps(frame) for frame in frames)
        self.sent: list[dict[str, Any]] = []
        self.stop, self.clock, self.closed = stop, clock, False

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def recv(self) -> str:
        self.clock.now += timedelta(seconds=5)
        if not self.frames:
            self.stop.set()
            raise asyncio.TimeoutError
        return self.frames.popleft()

    async def close(self) -> None:
        self.closed = True


HELLO = [
    [{"T": "success", "msg": "connected"}],
    [{"T": "success", "msg": "authenticated"}],
    [{"T": "subscription", "statuses": ["*"]}],
]


def test_listen_records_statuses_and_never_logs_the_secret(tmp_path: Path) -> None:
    stop, clock = threading.Event(), Clock(at(0))
    frames = HELLO + [
        [status("VEMB", "2", at(0)), status("MAGP", "H", at(0), "C")],
        [status("VEMB", "2", at(0))],
        [{"T": "t", "S": "VEMB"}, status("VEMB", "3", at(1))],
    ]
    connection = FakeConnection(frames, stop, clock)

    async def connect(_url: str) -> FakeConnection:
        return connection

    store = StatusWatchStore(tmp_path / "s.sqlite3")
    outcome = asyncio.run(
        listen_once(connect, "KEY", "SECRET", store, until=at(60), stop=stop, clock=clock)
    )
    document = store.export(at(-60), at(60))
    store.close()
    assert outcome.reason == "stopped" and outcome.error is None and outcome.changes == 3
    assert connection.closed
    assert connection.sent[0] == {"action": "auth", "key": "KEY", "secret": "SECRET"}
    assert connection.sent[1] == {"action": "subscribe", "statuses": ["*"]}
    assert [c["status_code"] for c in document["changes"]] == ["2", "H", "3"]
    raw = (tmp_path / "s.sqlite3").read_bytes()
    assert b"SECRET" not in raw


def test_refused_key_is_fatal_and_dropped_stream_is_not(tmp_path: Path) -> None:
    store = StatusWatchStore(tmp_path / "s.sqlite3")
    clock = Clock(at(0))

    def run(frames: list[Any]) -> Any:
        stop = threading.Event()

        async def connect(_url: str) -> FakeConnection:
            return FakeConnection(frames, stop, clock)

        return asyncio.run(
            listen_once(connect, "K", "S", store, until=at(60), stop=stop, clock=clock)
        )

    refused = run(HELLO[:1] + [[{"T": "error", "code": 402, "msg": "auth failed"}]])
    assert refused.fatal and "402" in str(refused.error)
    limited = run(HELLO[:1] + [[{"T": "error", "code": 406, "msg": "connection limit exceeded"}]])
    assert limited.error and not limited.fatal

    async def broken(_url: str) -> FakeConnection:
        raise OSError("network down")

    dropped = asyncio.run(
        listen_once(broken, "K", "S", store, until=at(60), stop=threading.Event(), clock=clock)
    )
    store.close()
    assert dropped.error == "OSError: network down" and not dropped.fatal


def test_window_starts_before_premarket_and_skips_holidays() -> None:
    calendar = ExchangeCalendar("XNYS")
    start, end = window_around(at(-120), calendar)
    assert start == T0 - timedelta(seconds=LEAD_SECONDS) and end == WINDOW.post_close_utc
    saturday = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    start, _ = window_around(saturday, calendar)
    assert start.date().isoformat() == "2026-11-16"


def test_run_waits_for_the_window_and_stops() -> None:
    stop = threading.Event()
    reports: list[dict[str, object]] = []

    def report(payload: dict[str, object]) -> None:
        reports.append(payload)
        stop.set()

    store_used: list[Any] = []
    code = run_halt_watch(
        "K",
        "S",
        store_used,  # type: ignore[arg-type]
        stop=stop,
        clock=lambda: at(-120),
        report=report,
    )
    assert code == 0 and reports[0]["status"] == "waiting" and reports[-1]["status"] == "stopped"


# --- What the flow watch reads -------------------------------------------------------


def recorded(tmp_path: Path) -> StatusWatchStore:
    return StatusWatchStore(store_path(tmp_path))


def test_no_record_is_not_available(tmp_path: Path) -> None:
    read = recorded_halts(store_path(tmp_path))
    assert not read("VEMB", WINDOW, at(30)).available
    recorded(tmp_path).close()  # a store with no listen in this session
    assert not read("VEMB", WINDOW, at(30)).available


def test_spans_gaps_and_no_look_ahead(tmp_path: Path) -> None:
    store = recorded(tmp_path)
    first = store.start("sip", at(-2))
    store.record(first, status("VEMB", "H", at(10), "C"), at(10))
    store.record(first, status("VEMB", "T", at(15), "C"), at(15))
    store.record(first, status("OTHER", "2", at(12)), at(12))
    store.end(first, at(20), "ConnectionClosed")
    second = store.start("sip", at(25))
    store.heard(second, at(40))
    store.close()
    read = recorded_halts(store_path(tmp_path))

    halts = read("VEMB", WINDOW, at(41))
    assert halts.available and halts.source == "sip_status_watch"
    (span,) = halts.spans
    assert (span.start_utc, span.end_utc, span.code) == (
        at(10) + timedelta(microseconds=123456),
        at(15) + timedelta(microseconds=123456),
        "H",
    )
    assert halts.gaps == [(at(20), at(25))]

    before_resume = read("VEMB", WINDOW, at(12))
    assert before_resume.spans[0].end_utc is None and before_resume.gaps == []

    # The open listen covers only 90 s past its last heartbeat: a dead process is a gap.
    later = read("VEMB", WINDOW, at(60))
    assert later.gaps[-1] == (at(40) + timedelta(seconds=90), at(60))


def test_cta_halt_not_repeated_by_the_next_listen_ends_there(tmp_path: Path) -> None:
    store = recorded(tmp_path)
    first = store.start("sip", at(-2))
    store.record(first, status("CTA", "2", at(5)), at(5))
    store.record(first, status("UTP", "H", at(5), "C"), at(5))
    store.record(first, status("LONG", "2", at(5)), at(5))
    store.end(first, at(10), "ConnectionClosed")
    second = store.start("sip", at(10))
    store.record(second, status("LONG", "2", at(5)), at(10) + timedelta(seconds=4))
    store.heard(second, at(20))
    store.close()
    read = recorded_halts(store_path(tmp_path))
    assert read("CTA", WINDOW, at(20)).spans[0].end_utc == at(10)
    assert read("UTP", WINDOW, at(20)).spans[0].end_utc is None  # UTP repeats are unmeasured
    assert read("LONG", WINDOW, at(20)).spans[0].end_utc is None
    # Too early to tell: the second listen has not run CONFIRM_SECONDS yet.
    assert read("CTA", WINDOW, at(10) + timedelta(seconds=10)).spans[0].end_utc is None


def test_store_file_is_read_only_for_the_reader(tmp_path: Path) -> None:
    store = recorded(tmp_path)
    store.start("sip", at(-2))
    store.close()
    path = store_path(tmp_path)
    recorded_halts(path)("X", WINDOW, at(5))
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT ended_at_utc FROM listens").fetchone()[0] is None
