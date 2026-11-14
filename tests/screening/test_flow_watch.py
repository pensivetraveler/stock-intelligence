"""Stage 3 realtime flow watch (ADR 0057 §3, U-a) on fixture minutes: empty minutes, halts,
the $1 tick boundary, window edges, re-entry and no look-ahead. No test reaches the network."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from services.market_data.provider import BarsQuery, BarsResult
from services.screening.flow_watch import (
    Minute,
    evaluate,
    flow_check,
    flow_document,
    last_closed_minute,
    minutes_from_bars,
    tick_size,
    timeline,
)
from services.screening.halts import HaltRead, HaltSpan, spans_from_statuses
from tests.screening.test_screening import WINDOW

START = datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc)


def t(minute: int, second: int = 0) -> datetime:
    return START + timedelta(minutes=minute, seconds=second)


def bar(minute: int, open_: str, close: str, volume: str = "10000", final: bool = True) -> Minute:
    o, c = Decimal(open_), Decimal(close)
    return Minute(t(minute), "bar", o, max(o, c), min(o, c), c, Decimal(volume), final=final)


def empty(minute: int, reason: str = "provider_no_bar") -> Minute:
    return Minute(t(minute), "empty", final=reason != "not_yet_final", reason=reason)


# A liquid minute that does not move: $2.00 x 10,000 shares = $20,000, no change.
def flat(minute: int) -> Minute:
    return bar(minute, "2.00", "2.00")


# A liquid minute that moves 2% (4 ticks at $2).
def mover(minute: int) -> Minute:
    return bar(minute, "2.00", "2.04")


def session(pattern: str, first: int = 0) -> list[Minute]:
    """`f` flat liquid, `m` liquid mover, `e` empty, `t` thin (liquid in dollars only)."""
    made = {
        "f": flat,
        "m": mover,
        "e": empty,
        "t": lambda i: bar(i, "10.00", "10.00", "999"),  # $9,990 but 999 shares
    }
    return [made[ch](first + i) for i, ch in enumerate(pattern)]


# Twenty minutes with three movers, all liquid: eligible at the last one.
BASE = "m" + "f" * 8 + "m" + "f" * 8 + "m" + "f"


def test_tick_size_switches_at_one_dollar() -> None:
    assert tick_size(Decimal("1.00")) == Decimal("0.01")
    assert tick_size(Decimal("0.9999")) == Decimal("0.0001")


@pytest.mark.parametrize(
    ("open_", "close", "volatile"),
    [
        ("1.00", "1.01", False),  # 1% but one tick at $1
        ("1.00", "0.98", True),  # -2%, two ticks: direction does not matter
        ("1.00", "1.02", True),
        ("0.99", "0.9999", True),  # 1% and 99 ticks of $0.0001
        ("0.0150", "0.0152", True),  # 1.33%, two ticks of $0.0001
        ("0.0150", "0.0151", False),  # 0.67%: under 1%
        ("5.00", "5.04", False),  # four ticks but 0.8%
        ("5.00", "5.05", True),
    ],
)
def test_volatility_needs_one_percent_and_two_ticks(open_: str, close: str, volatile: bool) -> None:
    assert bar(0, open_, close).volatile is volatile


def test_liquidity_needs_dollars_and_shares() -> None:
    assert flat(0).liquid
    assert not bar(0, "10.00", "10.00", "999").liquid  # $9,990 but 999 shares
    assert not bar(0, "0.40", "0.40", "12000").liquid  # 12,000 shares but $4,800
    assert bar(0, "0.50", "0.50", "10000").liquid  # exactly $5,000 and 10,000 shares


def test_four_of_five_liquid_and_three_of_twenty_moving_is_eligible() -> None:
    minutes = session(BASE[:-1] + "t")  # the newest minute is thin
    result = evaluate(minutes, len(minutes) - 1)
    assert (result.liquidity_passing, result.volatility_passing) == (4, 3)
    assert result.state == "eligible"
    minutes = session(BASE[:-2] + "tt")
    assert evaluate(minutes, len(minutes) - 1).state == "excluded"


def test_an_empty_minute_fails_both_conditions() -> None:
    minutes = session(BASE[:-2] + "ee")
    result = evaluate(minutes, len(minutes) - 1)
    assert result.liquidity_passing == 3 and result.state == "excluded"


def test_windows_count_back_from_the_evaluated_minute() -> None:
    # The first mover is the 21st minute back at the end: it no longer counts.
    minutes = session(BASE + "f")
    result = evaluate(minutes, len(minutes) - 1)
    assert result.volatility_passing == 2 and result.state == "excluded"
    assert result.volatility_minutes[0].start_utc == t(1)
    assert [m.start_utc for m in result.liquidity_minutes] == [t(i) for i in range(16, 21)]


def test_fewer_than_twenty_minutes_is_warming_up() -> None:
    minutes = session("mmmmm")
    assert evaluate(minutes, 4).state == "warming_up"


def test_halted_empty_minutes_leave_the_window_and_older_minutes_fill_it() -> None:
    # Five liquid minutes, then a five-minute halt with no trade, then evaluation.
    minutes = session(BASE + "eeeee")
    halt = HaltSpan(t(20), t(25), "P", "LULD pause")
    without = evaluate(minutes, len(minutes) - 1)
    assert without.state == "excluded"  # no halt record: the empty minutes count
    marked = minutes_from_bars(_bars(minutes), _missing(minutes), [halt], t(25))
    assert [m.halted for m in marked[-5:]] == [True] * 5
    result = evaluate(marked, len(marked) - 1, [halt])
    assert result.state == "eligible"
    assert result.liquidity_minutes[-1].start_utc == t(19)
    assert result.volatility_passing == 3


def test_a_halt_still_open_is_its_own_state() -> None:
    minutes = session(BASE + "ee")
    halt = HaltSpan(t(20, 30), None, "2")
    marked = minutes_from_bars(_bars(minutes), _missing(minutes), [halt], t(22))
    result = evaluate(marked, len(marked) - 1, [halt])
    assert result.halted_now and result.state == "halted"


def test_a_traded_minute_during_a_halt_still_counts() -> None:
    minutes = session(BASE)
    halt = HaltSpan(t(19, 30), t(25), "H")
    marked = minutes_from_bars(_bars(minutes), _missing(minutes), [halt], t(25))
    assert marked[-1].halted and not marked[-1].left_out


def test_unknown_minutes_cannot_be_judged() -> None:
    minutes = session(BASE)
    minutes[10] = Minute(t(10), "unknown", reason="outside_entitlement")
    assert evaluate(minutes, len(minutes) - 1).state == "unknown"


def test_excluded_symbols_come_back_and_changes_are_kept() -> None:
    minutes = session(BASE + "ee" + "mffm")
    states = [e.state for e in timeline(minutes)]
    assert states[18] == "warming_up" and states[19] == "eligible"
    assert states[21] == "excluded"  # two empty minutes: 3 of 5
    assert states[-1] == "eligible"  # back before the session ends
    document = flow_document(timeline(minutes), HaltRead(False), "not_implemented")
    assert [c["state"] for c in document["changes"]] == [
        "warming_up",
        "eligible",
        "excluded",
        "eligible",
    ]
    assert document["new_buys_allowed"] and document["resting_orders"] == "keep"


def test_excluded_blocks_new_buys_only() -> None:
    minutes = session(BASE + "ee")
    document = flow_document(timeline(minutes), HaltRead(False), "not_implemented")
    assert document["state"] == "excluded"
    assert document["new_buys_allowed"] is False and document["resting_orders"] == "keep"


def test_no_minute_after_the_evaluated_one_is_read() -> None:
    minutes = session(BASE + "eeeefffmmm")
    full = timeline(minutes)
    for index in range(len(minutes)):
        assert evaluate(minutes[: index + 1], index) == full[index]


def test_not_yet_final_empty_minutes_count_but_mark_the_result_provisional() -> None:
    minutes = session(BASE[:-1]) + [empty(19, "not_yet_final")]
    result = evaluate(minutes, len(minutes) - 1)
    assert result.provisional and result.liquidity_passing == 4


def test_cta_and_utp_status_messages_become_halt_spans() -> None:
    messages: list[dict[str, Any]] = [
        # CTA repeats a halt every five seconds while it lasts (measured 2026-11-12).
        {"T": "s", "S": "X", "sc": "2", "t": "2026-11-12T15:20:00.001770155Z", "z": "B"},
        {"T": "s", "S": "X", "sc": "2", "t": "2026-11-12T15:20:05.002Z", "z": "B"},
        {"T": "s", "S": "X", "sc": "3", "t": "2026-11-12T15:25:00Z", "z": "B"},
        {"T": "s", "S": "X", "sc": "3", "t": "2026-11-12T15:25:05Z", "z": "B"},
        # UTP: a volatility pause, quotes resume first, trading after.
        {"T": "s", "S": "X", "sc": "P", "rm": "LULD", "t": "2026-11-12T16:00:00Z", "z": "C"},
        {"T": "s", "S": "X", "sc": "Q", "t": "2026-11-12T16:04:00Z", "z": "C"},
        {"T": "s", "S": "X", "sc": "T", "t": "2026-11-12T16:05:00Z", "z": "C"},
        {"T": "s", "S": "X", "sc": "H", "t": "2026-11-12T17:00:00Z", "z": "C"},
    ]
    spans = spans_from_statuses(messages)
    assert [(s.start_utc.strftime("%H:%M:%S"), s.code) for s in spans] == [
        ("15:20:00", "2"),
        ("16:00:00", "P"),
        ("17:00:00", "H"),
    ]
    assert spans[0].end_utc == t(25) and spans[1].end_utc == t(65)
    assert spans[1].reason == "LULD" and spans[2].end_utc is None
    # Known at 15:22, the first halt had not ended yet.
    assert spans[0].overlaps(t(22), t(23), t(22, 30))
    assert not spans[0].overlaps(t(26), t(27), t(30))


def test_last_closed_minute_waits_for_the_settle_margin() -> None:
    assert last_closed_minute(t(5, 10)) == t(4)
    assert last_closed_minute(t(5, 25)) == t(5)


def _bars(minutes: list[Minute]) -> list[dict[str, Any]]:
    return [
        {
            "interval_start_utc": m.start_utc.isoformat().replace("+00:00", "Z"),
            "open": str(m.open),
            "high": str(m.high),
            "low": str(m.low),
            "close": str(m.close),
            "volume": str(m.volume),
            "volume_unit": "shares",
            "is_final": m.final,
        }
        for m in minutes
        if m.kind == "bar"
    ]


def _missing(minutes: list[Minute]) -> list[dict[str, Any]]:
    return [
        {
            "start_utc": m.start_utc.isoformat().replace("+00:00", "Z"),
            "end_utc": m.end_utc.isoformat().replace("+00:00", "Z"),
            "reason": m.reason or "provider_no_bar",
        }
        for m in minutes
        if m.kind != "bar"
    ]


def test_flow_check_reads_closed_minutes_from_the_premarket_open() -> None:
    asked: list[BarsQuery] = []
    open_ = WINDOW.pre_open_utc

    def get_bars(query: BarsQuery) -> BarsResult:
        asked.append(query)
        count = int((query.end_utc - query.start_utc).total_seconds() // 60)
        minutes = [(mover if i % 6 == 0 else flat)(0) for i in range(count)]
        bars = []
        for i, m in enumerate(minutes):
            start = open_ + timedelta(minutes=i)
            bars.append(
                {
                    "interval_start_utc": start.isoformat().replace("+00:00", "Z"),
                    "open": str(m.open),
                    "high": str(m.high),
                    "low": str(m.low),
                    "close": str(m.close),
                    "volume": str(m.volume),
                    "volume_unit": "shares",
                    "is_final": True,
                    "session": "pre",
                }
            )
        return BarsResult(tuple(bars), {"source_id": "fixture", "missing_intervals": []})

    now = open_ + timedelta(minutes=30, seconds=10)
    outcome = flow_check("us.xnas.test", WINDOW, now, get_bars, HaltRead(False), "not_implemented")
    assert asked[0].start_utc == open_ and asked[0].end_utc == open_ + timedelta(minutes=29)
    result = outcome["result"]
    assert outcome["status"] == "ok" and result["state"] == "eligible"
    assert result["evaluated_minutes"] == 29 and result["stage2"] == "not_implemented"
    assert any("halt" in w for w in outcome["warnings"])
    early = flow_check("us.xnas.test", WINDOW, open_, get_bars, HaltRead(False), "not_implemented")
    assert early["result"]["state"] == "warming_up" and len(asked) == 1


def test_flow_route_judges_every_candidate_and_validates(
    tmp_path: Any, record_halts: bool = False
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from packages.contracts.validator import validate_document
    from services.api.main import _error_response
    from services.api.screening_flow_routes import register_screening_flow_routes
    from services.market_data.broker_common import instrument_listing
    from services.screening.store import ScreeningStore
    from tests.screening.test_momentum import gappy_record

    gappy_record(tmp_path)
    now = WINDOW.pre_open_utc + timedelta(minutes=45, seconds=30)
    screening = ScreeningStore(tmp_path / "screening.sqlite3", lambda: now)
    screening.add_manual(WINDOW.session_date, "MINE")

    def get_bars(query: BarsQuery) -> BarsResult:
        count = int((query.end_utc - query.start_utc).total_seconds() // 60)
        made = session(("mfffff" * 10)[:count])
        bars = _bars(made)
        for minute, item in enumerate(bars):
            start = query.start_utc + timedelta(minutes=minute)
            item["interval_start_utc"] = start.isoformat().replace("+00:00", "Z")
        return BarsResult(tuple(bars), {"source_id": "fixture", "missing_intervals": []})

    if record_halts:
        from services.collector.halt_watch import StatusWatchStore

        recorder = StatusWatchStore.in_data_dir(tmp_path)
        listen = recorder.start("sip", WINDOW.pre_open_utc - timedelta(minutes=2))
        halt_at = WINDOW.pre_open_utc + timedelta(minutes=44)
        recorder.record(
            listen,
            {
                "T": "s",
                "S": "FLIP",
                "sc": "H",
                "rc": "LUDP",
                "z": "C",
                "t": halt_at.isoformat().replace("+00:00", "Z"),
            },
            halt_at,
        )
        recorder.heard(listen, now)
        recorder.close()
    app = FastAPI()
    register_screening_flow_routes(
        app,
        store=lambda: screening,
        data_dir=lambda: tmp_path,
        listing_for=lambda symbol: (
            None
            if symbol == "OPEN"
            else instrument_listing(symbol, "XNAS", "test", "test-r1", "test")
        ),
        get_bars=lambda: get_bars,
        error_response=_error_response,
        clock=lambda: now,
    )
    with TestClient(app) as client:
        response = client.get("/v1/screening/flow")
    screening.close()
    assert response.status_code == 200, response.text
    document = response.json()
    validate_document(document)
    rows = {row["symbol"]: row for row in document["candidates"]}
    assert "MINE" in rows and rows["OPEN"]["status"] == "unsupported"
    flip = rows["FLIP"]["result"]
    # No news check stored: stage 2 cannot judge, so it passes as unconfirmed (ADR 0057).
    assert flip["stage2"] == "passed"
    assert flip["stage2_detail"]["material"] == "unconfirmed"
    assert flip["stage2_detail"]["reason"] == "no_news_check"
    assert flip["stage2_detail"]["strategy"] == "conservative"
    assert flip["evaluated_until_utc"] == "2026-11-09T09:45:00Z"
    if not record_halts:
        assert flip["state"] == "eligible"
        assert flip["new_buys_allowed"] and not flip["halts"]["available"]
        return
    assert flip["halts"]["available"] and flip["halts"]["source"] == "sip_status_watch"
    assert flip["halts"]["gaps"] == [] and flip["state"] == "halted"
    assert flip["halts"]["spans"][0]["code"] == "H" and not flip["new_buys_allowed"]
    assert not any("halt" in w for w in rows["FLIP"]["warnings"])


def test_flow_route_reads_the_recorded_halts(tmp_path: Any) -> None:
    test_flow_route_judges_every_candidate_and_validates(tmp_path, record_halts=True)
