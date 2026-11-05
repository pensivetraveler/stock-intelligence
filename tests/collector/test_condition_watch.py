"""Realtime Kiwoom condition watch over a fake WebSocket, and its record (no network, ADR 0051).

Messages follow the usa20290 spec example: the subscription answers `jmcode`/`stex_tp`
pairs, then REAL messages carry S2 items whose values hold 843 "I" (enter) or "D" (leave).
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from services.collector.cli import main
from services.collector.condition_watch import (
    ConditionWatchStore,
    run_watch,
    watch_once,
    window_around,
)
from services.market_data import kiwoom_conditions
from services.market_data.calendar import ExchangeCalendar
from services.market_data.kiwoom_conditions import (
    Condition,
    ConditionEvent,
    ConditionNotFoundError,
    ConditionTicker,
    KiwoomConditionSearch,
)
from services.market_data.provider import ProviderDataError, ProviderUnavailableError

CONDITIONS = [["1", "SIW_전일신고거래량"], ["2", "SIW_당일급등주"], ["3", "SIW_전일급등주"]]
CALENDAR = ExchangeCalendar("XNYS")


def s2(symbol: str, kind: str, seq: str = "2") -> dict[str, Any]:
    return {
        "values": {"20": "230156", "841": seq, "843": kind, "907": "2", "9001": symbol},
        "type": "S2",
        "name": "조건검색",
        "item": symbol,
        "stexTp": "ND",
    }


class Server:
    def __init__(self, realtime: list[Any] | None = None) -> None:
        self.realtime = realtime or []
        self.sent: list[dict[str, Any]] = []
        self.closed = 0

    def connect(self, _url: str) -> Connection:
        return Connection(self)


class Connection:
    def __init__(self, server: Server) -> None:
        self.server = server
        self.outbox: list[str] = []
        self.streaming = False

    def send(self, message: str) -> None:
        body = {"trnm": "PING"} if message == "PING" else json.loads(message)
        self.server.sent.append(body)
        name = body["trnm"]
        if name == "LOGIN":
            reply: dict[str, Any] = {"trnm": "LOGIN", "return_code": 0}
        elif name == "GCNSRLST":
            reply = {"trnm": "GCNSRLST", "return_code": 0, "data": CONDITIONS}
        elif name == "GCNSRREQ":
            reply = {
                "trnm": "GCNSRREQ",
                "seq": body["seq"],
                "return_code": 0,
                "data": [{"stex_tp": "ND", "jmcode": "adv"}, {"stex_tp": "NA", "jmcode": "GPUS"}],
            }
            self.streaming = True
        elif name == "GCNSRCLR":
            # The spec example answers a clear with trnm GCNSRREQ.
            reply = {"trnm": "GCNSRREQ", "seq": body["seq"], "return_code": 0}
        else:
            return
        self.outbox.append(json.dumps(reply, ensure_ascii=False))

    def recv(self, timeout: float | None = None) -> str:
        if self.outbox:
            return self.outbox.pop(0)
        if self.streaming and self.server.realtime:
            item = self.server.realtime.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
        raise TimeoutError

    def close(self) -> None:
        self.server.closed += 1


def searcher(server: Server) -> KiwoomConditionSearch:
    return KiwoomConditionSearch(lambda _refresh: "token", connect=server.connect)


def real(*items: dict[str, Any]) -> dict[str, Any]:
    return {"trnm": "REAL", "data": list(items)}


def collect(server: Server, polls: int) -> tuple[list[Any], list[ConditionEvent]]:
    listed: list[Any] = []
    events: list[ConditionEvent] = []
    left = [polls]

    def until() -> bool:
        left[0] -= 1
        return left[0] < 0

    searcher(server).watch(
        "SIW_당일급등주",
        on_list=lambda condition, tickers: listed.append((condition, tickers)),
        on_event=events.append,
        until=until,
    )
    return listed, events


def test_a_watch_subscribes_by_name_and_reports_enter_and_leave_events() -> None:
    server = Server(
        [
            "PING",
            real(s2("COIN", "I"), s2("SOFI", "D")),
            real(s2("OTHER", "I", seq="3"), {"type": "0B", "values": {}}),
            {"trnm": "PING"},
        ]
    )
    listed, events = collect(server, polls=6)
    condition, tickers = listed[0]
    assert condition == Condition("2", "SIW_당일급등주")
    assert tickers == (ConditionTicker("ADV", "ND"), ConditionTicker("GPUS", "NA"))
    assert [(item.kind, item.symbol, item.kiwoom_time) for item in events] == [
        ("enter", "COIN", "230156"),
        ("leave", "SOFI", "230156"),
    ]
    assert events[0].values["907"] == "2" and events[0].kiwoom_exchange == "ND"
    names = [body["trnm"] for body in server.sent if body["trnm"] != "PING"]
    assert names == ["LOGIN", "GCNSRLST", "GCNSRREQ", "GCNSRCLR"]
    subscribe = next(body for body in server.sent if body["trnm"] == "GCNSRREQ")
    assert subscribe == {"trnm": "GCNSRREQ", "seq": "2", "search_type": "1"}
    assert {"trnm": "PING"} in server.sent
    assert server.closed == 1


def test_an_event_of_unknown_kind_is_an_error_not_a_guess() -> None:
    with pytest.raises(ProviderDataError):
        collect(Server([real(s2("COIN", "X"))]), polls=3)


def test_a_silent_or_closed_connection_ends_the_watch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kiwoom_conditions, "SILENCE_SECONDS", -1.0)
    server = Server()
    with pytest.raises(ProviderUnavailableError, match="silent"):
        collect(server, polls=100)
    assert server.closed == 1
    monkeypatch.setattr(kiwoom_conditions, "SILENCE_SECONDS", 60.0)
    with pytest.raises(ProviderUnavailableError, match="closed"):
        collect(Server([ConnectionResetError()]), polls=100)


def test_only_search_types_0_and_1_and_clear_are_sendable() -> None:
    socket = kiwoom_conditions._Socket(Server().connect("x"))
    with pytest.raises(ProviderDataError):
        socket._send({"trnm": "GCNSRREQ", "seq": "2", "search_type": "2"})
    with pytest.raises(ProviderDataError):
        socket._send({"trnm": "REG", "type": "0B"})


def utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=timezone.utc)


def test_the_window_runs_from_premarket_to_aftermarket_close_on_session_days() -> None:
    # Thursday 2026-11-05, New York on EST: 04:00-20:00 ET is 09:00-01:00Z.
    assert window_around(utc(2026, 11, 5, 6), CALENDAR) == (
        utc(2026, 11, 5, 9),
        utc(2026, 11, 6, 1),
    )
    assert window_around(utc(2026, 11, 5, 12), CALENDAR)[0] == utc(2026, 11, 5, 9)
    # After Friday's close the next window is Monday's.
    assert window_around(utc(2026, 11, 7, 2), CALENDAR)[0] == utc(2026, 11, 9, 9)
    # Thanksgiving is skipped; the day after closes early, so its aftermarket ends 17:00 ET.
    assert window_around(utc(2026, 11, 26, 12), CALENDAR) == (
        utc(2026, 11, 27, 9),
        utc(2026, 11, 27, 22),
    )


def test_the_store_records_watches_and_closes_an_interrupted_one(tmp_path: Path) -> None:
    store = ConditionWatchStore.in_data_dir(tmp_path)
    condition = Condition("2", "SIW_당일급등주")
    first = store.start(condition, "real", utc(2026, 11, 5, 9), (ConditionTicker("ADV", "ND"),))
    event = ConditionEvent("enter", "COIN", "ND", "040501", {"843": "I", "9001": "COIN"})
    store.add(first, utc(2026, 11, 5, 9, 5, 1), event)
    store.end(first, utc(2026, 11, 5, 10), "ProviderUnavailableError: gone")
    second = store.start(condition, "real", utc(2026, 11, 5, 10, 0, 5), ())
    store.add(second, utc(2026, 11, 5, 11), event)
    store.close()  # the second watch never ends: the process was killed

    reopened = ConditionWatchStore.in_data_dir(tmp_path)
    document = reopened.export(utc(2026, 11, 5, 5), utc(2026, 11, 6, 5))
    reopened.close()
    one, two = document["watches"]
    assert one["end_reason"] == "ProviderUnavailableError: gone"
    assert [(item["kind"], item["symbol"]) for item in one["events"]] == [
        ("listed", "ADV"),
        ("enter", "COIN"),
    ]
    assert one["events"][1] == {
        "received_at_utc": "2026-11-05T09:05:01.000Z",
        "kind": "enter",
        "symbol": "COIN",
        "kiwoom_exchange": "ND",
        "kiwoom_time": "040501",
        "raw": {"843": "I", "9001": "COIN"},
    }
    # The gap after its last event is on record, not hidden.
    assert two["end_reason"] == "interrupted"
    assert two["ended_at_utc"] == "2026-11-05T11:00:00.000Z"


class Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


def test_watch_once_records_a_session_and_why_it_ended(tmp_path: Path) -> None:
    store = ConditionWatchStore.in_data_dir(tmp_path)
    clock = Clock(utc(2026, 11, 5, 9))
    outcome = watch_once(
        searcher(Server([real(s2("COIN", "I")), ConnectionResetError()])),
        store,
        "SIW_당일급등주",
        "real",
        until=utc(2026, 11, 6, 1),
        stop=threading.Event(),
        clock=clock,
    )
    assert outcome.reason == "error" and not outcome.fatal
    watch = store.export(utc(2026, 11, 5), utc(2026, 11, 6))["watches"][0]
    assert watch["end_reason"].startswith("ProviderUnavailableError")
    assert [item["kind"] for item in watch["events"]] == ["listed", "listed", "enter"]
    # The window's end closes the watch cleanly.
    outcome = watch_once(
        searcher(Server()),
        store,
        "SIW_당일급등주",
        "real",
        until=clock.now,
        stop=threading.Event(),
        clock=clock,
    )
    assert outcome.reason == "window_closed"
    store.close()


class Stop(threading.Event):
    """Records each wait and stops the loop after `waits` of them."""

    def __init__(self, waits: int) -> None:
        super().__init__()
        self.waits: list[float] = []
        self.limit = waits

    def wait(self, timeout: float | None = None) -> bool:
        self.waits.append(timeout or 0.0)
        if len(self.waits) >= self.limit:
            self.set()
        return self.is_set()


class FlakySearch:
    def __init__(self, errors: list[Exception]) -> None:
        self.errors = errors

    def watch(self, name: str, **_kwargs: Any) -> None:
        raise self.errors.pop(0)


def test_run_watch_waits_for_the_window_and_backs_off_after_errors(tmp_path: Path) -> None:
    store = ConditionWatchStore.in_data_dir(tmp_path)
    reports: list[dict[str, object]] = []
    stop = Stop(waits=3)
    clock = Clock(utc(2026, 11, 5, 12))  # inside Thursday's window
    code = run_watch(
        FlakySearch([ProviderUnavailableError("a"), ProviderUnavailableError("b")] * 2),  # type: ignore[arg-type]
        store,
        "SIW_당일급등주",
        "real",
        stop=stop,
        clock=clock,
        report=reports.append,
        calendar=CALENDAR,
    )
    assert code == 0
    assert stop.waits == [5.0, 10.0, 20.0]
    assert [item["status"] for item in reports][-1] == "stopped"

    before = Stop(waits=1)
    run_watch(
        FlakySearch([]),  # type: ignore[arg-type]
        store,
        "SIW_당일급등주",
        "real",
        stop=before,
        clock=Clock(utc(2026, 11, 5, 8)),
        report=reports.append,
        calendar=CALENDAR,
    )
    assert 3500 < before.waits[0] <= 3600  # until 09:00Z

    missing = run_watch(
        FlakySearch([ConditionNotFoundError("no SIW_당일급등주")]),  # type: ignore[arg-type]
        store,
        "SIW_당일급등주",
        "real",
        stop=Stop(waits=5),
        clock=Clock(utc(2026, 11, 5, 12)),
        report=reports.append,
        calendar=CALENDAR,
    )
    assert missing == 2
    store.close()


def test_the_cli_refuses_an_unconnected_name_and_exports_a_date(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["watch-condition", "--name", "정규장거래량", "--data-dir", str(tmp_path)]) == 2
    store = ConditionWatchStore.in_data_dir(tmp_path)
    watch = store.start(
        Condition("2", "SIW_당일급등주"),
        "real",
        utc(2026, 11, 5, 9),
        (ConditionTicker("ADV", "ND"),),
    )
    store.end(watch, utc(2026, 11, 6, 1), "window_closed")
    store.close()
    capsys.readouterr()
    assert main(["export-condition", "--date", "2026-11-05", "--data-dir", str(tmp_path)]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["from_utc"] == "2026-11-05T05:00:00.000Z"
    assert [item["end_reason"] for item in document["watches"]] == ["window_closed"]
