"""U0 momentum candidates (ADR 0055): gaps in the condition record, how an entry time is
known, flicker counts, the noon cut and the momentum check on 1m bars. No test reaches the
network."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.contracts.validator import validate_document
from services.api.main import _error_response
from services.api.screening_routes import register_screening_routes
from services.collector.condition_watch import ConditionWatchStore
from services.market_data.broker_common import instrument_listing
from services.market_data.kiwoom_conditions import ConditionEvent, ConditionTicker
from services.market_data.provider import BarsQuery, BarsResult, InvalidBarsQueryError
from services.screening.candidates import read_watched, watch_path
from services.screening.momentum import momentum_check
from services.screening.store import ScreeningStore
from tests.screening.test_screening import CALENDAR, CONDITION, WINDOW, at


def utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def gappy_record(data_dir: Path) -> None:
    """A morning with a crash, a restart and a flickering symbol."""
    store = ConditionWatchStore.in_data_dir(data_dir)
    first = store.start(CONDITION, "real", at(1), (ConditionTicker("OPEN", "ND"),))
    store.add(first, at(5), ConditionEvent("enter", "FLIP", "ND", "180500", {}))
    store.add(first, at(6), ConditionEvent("leave", "FLIP", "ND", "180600", {}))
    store.add(first, at(7), ConditionEvent("enter", "FLIP", "ND", "180700", {}))
    store.add(first, at(8), ConditionEvent("leave", "FLIP", "ND", "180800", {}))
    store.add(first, at(10), ConditionEvent("enter", "EVT", "NY", "181000", {}))
    store.end(first, at(31), "renewed")
    # The renewal's list shows a symbol no event reported.
    second = store.start(
        CONDITION,
        "real",
        at(31),
        (
            ConditionTicker("OPEN", "ND"),
            ConditionTicker("EVT", "NY"),
            ConditionTicker("QUIET", "ND"),
        ),
    )
    store.add(second, at(40), ConditionEvent("enter", "LAST", "ND", "184000", {}))
    store.close()
    # The process dies: the next open closes the watch as interrupted at its last event.
    store = ConditionWatchStore.in_data_dir(data_dir)
    third = store.start(
        CONDITION,
        "real",
        at(90),
        (ConditionTicker("OPEN", "ND"), ConditionTicker("AFTER", "ND")),
    )
    store.end(third, at(120), "stopped")
    store.close()


def test_gaps_entry_times_and_flicker_come_from_the_record(tmp_path: Path) -> None:
    gappy_record(tmp_path)
    read = read_watched(watch_path(tmp_path), WINDOW, at(200), CONDITION.name)
    gaps = [
        (g.reason, g.from_utc[11:16], g.to_utc[11:16], g.previous_end) for g in read.coverage.gaps
    ]
    assert gaps == [
        ("before_first_watch", "09:00", "09:01", None),
        ("between_watches", "09:40", "10:30", "interrupted"),
        ("not_recording", "11:00", "12:20", "stopped"),
    ]
    assert read.coverage.interrupted == 1 and not read.coverage.open_now
    found = {item.symbol: item for item in read.symbols}
    assert found["OPEN"].first_seen_how == "first_list"
    assert found["OPEN"].entered_after_utc == "2026-11-09T09:00:00.000Z"
    assert found["EVT"].first_seen_how == "enter_event" and found["EVT"].entered_after_utc is None
    assert found["QUIET"].first_seen_how == "list_on_renewal"
    assert found["QUIET"].entered_after_utc == "2026-11-09T09:01:00.000Z"
    assert found["AFTER"].first_seen_how == "list_after_gap"
    assert found["AFTER"].entered_after_utc == "2026-11-09T09:40:00.000Z"
    assert found["FLIP"].enter_count == 2 and not found["FLIP"].in_list
    assert found["FLIP"].seconds_in_list == pytest.approx(120.0)
    # In the list for both covered watches, not across the gap.
    assert found["OPEN"].seconds_in_list == pytest.approx((39 + 30) * 60.0)


def test_an_unclosed_old_watch_is_not_read_as_recording(tmp_path: Path) -> None:
    store = ConditionWatchStore.in_data_dir(tmp_path)
    watch = store.start(CONDITION, "real", at(1), (ConditionTicker("OPEN", "ND"),))
    store.add(watch, at(10), ConditionEvent("enter", "EVT", "ND", None, {}))
    store.close()  # killed: no end recorded, and the store not opened since
    read = read_watched(watch_path(tmp_path), WINDOW, at(120), CONDITION.name)
    assert not read.coverage.open_now and read.coverage.interrupted == 1
    assert read.coverage.gaps[-1].reason == "not_recording"
    assert read.coverage.gaps[-1].from_utc.startswith("2026-11-09T09:10")
    # A young unclosed watch may still be running.
    young = read_watched(watch_path(tmp_path), WINDOW, at(20), CONDITION.name)
    assert young.coverage.open_now and [g.reason for g in young.coverage.gaps] == [
        "before_first_watch"
    ]


def test_no_record_is_one_gap(tmp_path: Path) -> None:
    read = read_watched(watch_path(tmp_path), WINDOW, at(60), CONDITION.name)
    assert [(g.reason, g.from_utc[11:16], g.to_utc[11:16]) for g in read.coverage.gaps] == [
        ("no_record", "09:00", "10:00")
    ]


def minute_bars(closes: dict[int, str], *, missing: dict[int, str] | None = None) -> Any:
    """1m bars at the given minutes after the premarket open, plus a daily close of 4."""

    def get_bars(query: BarsQuery) -> BarsResult:
        if query.timeframe == "1d":
            day = {
                "interval_start_utc": "2026-11-06T14:30:00Z",
                "interval_end_utc": "2026-11-06T21:00:00Z",
                "close": "4",
                "adjustment": "raw",
                "source_id": "alpaca",
            }
            return BarsResult((day,), {})
        bars = []
        expected = int((query.end_utc - query.start_utc).total_seconds() // 60)
        for minute, close in sorted(closes.items()):
            start = WINDOW.pre_open_utc + timedelta(minutes=minute)
            if start + timedelta(minutes=1) > query.end_utc:
                continue
            bars.append(
                {
                    "interval_start_utc": utc(start),
                    "interval_end_utc": utc(start + timedelta(minutes=1)),
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": "1000",
                    "volume_unit": "shares",
                    "session": "pre",
                }
            )
        gaps = [
            {"start_utc": "x", "end_utc": "y", "reason": reason}
            for reason in (missing or {}).values()
        ]
        coverage = {
            "source_id": "alpaca",
            "source_feed_scope": "alpaca-sip",
            "adjustment": "raw",
            "dataset_revision": "r1",
            "expected_interval_count": expected,
            "missing_intervals": gaps,
        }
        return BarsResult(tuple(bars), coverage)

    return get_bars


def test_momentum_uses_only_bars_closed_before_the_entry(tmp_path: Path) -> None:
    gappy_record(tmp_path)
    read = read_watched(watch_path(tmp_path), WINDOW, at(200), CONDITION.name)
    evt = next(item for item in read.symbols if item.symbol == "EVT")  # entered at 04:10
    get_bars = minute_bars({2: "4.5", 9: "5", 10: "9"})  # the 04:10 bar closes after it
    outcome = momentum_check("us:evt:xnys:2026", WINDOW, evt, get_bars, CALENDAR)
    assert outcome["status"] == "ok"
    result = outcome["result"]
    assert result["price"] == {
        "value": "5",
        "bar_end_utc": "2026-11-09T09:10:00Z",
        "session": "pre",
    }
    assert result["change_percent"] == "25.00"  # from the previous close of 4
    assert result["volume"] == "2000"
    assert result["dollar_volume_estimate"] == "9500.00"
    assert result["entry_time_known"] and result["bars_used"] == 2
    assert outcome["sources"]["bars"]["source_feed_scope"] == "alpaca-sip"


def test_momentum_says_when_it_cannot_be_complete(tmp_path: Path) -> None:
    gappy_record(tmp_path)
    read = read_watched(watch_path(tmp_path), WINDOW, at(200), CONDITION.name)
    evt = next(item for item in read.symbols if item.symbol == "EVT")
    late = minute_bars({2: "4.5"}, missing={3: "outside_entitlement"})
    assert momentum_check("i", WINDOW, evt, late, CALENDAR)["status"] == "incomplete"
    empty = minute_bars({})
    assert momentum_check("i", WINDOW, evt, empty, CALENDAR)["status"] == "no_data"
    opening = next(item for item in read.symbols if item.symbol == "OPEN")  # 04:01
    known = momentum_check("i", WINDOW, opening, minute_bars({0: "4"}), CALENDAR)
    assert not known["result"]["entry_time_known"]

    def refuses(_: BarsQuery) -> BarsResult:
        raise InvalidBarsQueryError("no 1m bars here")

    assert momentum_check("i", WINDOW, evt, refuses, CALENDAR)["status"] == "error"


NOW = WINDOW.pre_open_utc + timedelta(hours=10)  # 14:00 New York time


@pytest.fixture
def http(tmp_path: Path) -> Iterator[TestClient]:
    gappy_record(tmp_path)
    store = ConditionWatchStore.in_data_dir(tmp_path)
    # An entry after noon New York time.
    late = store.start(CONDITION, "real", at(9 * 60), ())
    store.add(late, at(9 * 60 + 5), ConditionEvent("enter", "NOON", "ND", None, {}))
    store.end(late, at(9 * 60 + 10), "stopped")
    store.close()
    screening = ScreeningStore(tmp_path / "screening.sqlite3", lambda: NOW)
    app = FastAPI()
    register_screening_routes(
        app,
        store=lambda: screening,
        data_dir=lambda: tmp_path,
        strategy=lambda *_: None,
        listing_for=lambda symbol: instrument_listing(symbol, "XNAS", "test", "test-r1", "test"),
        get_bars=lambda: minute_bars({2: "4.5", 9: "5"}),
        get_adjusted=lambda: None,
        error_response=_error_response,
        clock=lambda: NOW,
    )
    with TestClient(app) as client:
        yield client
    screening.close()


def test_candidates_carry_gaps_entry_times_and_the_noon_cut(http: TestClient) -> None:
    document = http.get("/v1/screening/candidates").json()
    validate_document(document)
    assert document["selection_noon_utc"] == "2026-11-09T17:00:00.000Z"
    reasons = [gap["reason"] for gap in document["watch_coverage"]["gaps"]]
    assert reasons == [
        "before_first_watch",
        "between_watches",
        "between_watches",
        "not_recording",
    ]
    rows = {row["symbol"]: row for row in document["candidates"]}
    assert rows["FLIP"]["enter_count"] == 2 and rows["FLIP"]["first_seen_how"] == "enter_event"
    assert rows["AFTER"]["first_seen_how"] == "list_after_gap"
    assert "NOON" in rows

    noon = http.get("/v1/screening/candidates", params={"as_of": "2026-11-09T17:00:00Z"}).json()
    validate_document(noon)
    assert noon["cutoff_utc"] == "2026-11-09T17:00:00.000Z"
    assert "NOON" not in {row["symbol"] for row in noon["candidates"]}
    naive = http.get("/v1/screening/candidates", params={"as_of": "2026-11-09T12:00:00"})
    assert naive.status_code == 422


def test_momentum_route_stores_a_check_the_table_summarizes(http: TestClient) -> None:
    response = http.post("/v1/screening/checks/momentum", json={"symbol": "EVT"})
    assert response.status_code == 200, response.text
    check = response.json()
    validate_document(check)
    assert check["check"] == "momentum" and check["status"] == "ok"
    assert check["cutoff_utc"] == "2026-11-09T09:10:00.000Z"  # when SIW first saw it
    row = next(
        row
        for row in http.get("/v1/screening/candidates").json()["candidates"]
        if row["symbol"] == "EVT"
    )
    assert row["checks"]["momentum"]["summary"] == {
        "price": "5",
        "change_percent": "25.00",
        "dollar_volume_estimate": "9500.00",
        "entry_time_known": True,
    }
    stranger = http.post("/v1/screening/checks/momentum", json={"symbol": "TSLA"})
    assert stranger.status_code == 422
    assert stranger.json()["error"]["code"] == "not_a_condition_candidate"
