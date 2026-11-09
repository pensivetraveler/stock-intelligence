"""Daily candidate screening S1 (ADR 0052): candidates from the condition record, the
entry check on daily bars, and the routes. No test reaches the network."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.contracts.validator import validate_document
from services.api.main import _error_response
from services.api.screening_routes import register_screening_routes
from services.backtest.store import BacktestStore
from services.collector.condition_watch import ConditionWatchStore
from services.market_data.broker_common import instrument_listing
from services.market_data.calendar import UsEquityCalendar
from services.market_data.kiwoom_conditions import Condition, ConditionEvent, ConditionTicker
from services.market_data.provider import InvalidBarsQueryError
from services.screening.candidates import read_watched, watch_path
from services.screening.entry import entry_check
from services.screening.store import ScreeningStore
from tests.backtest.test_backtest_api import adjusted_bars, synthetic_bars

CALENDAR = UsEquityCalendar("XNAS")
DAY = date(2026, 11, 9)  # Monday
WINDOW = CALENDAR.window_for_date(DAY)
assert WINDOW is not None
CONDITION = Condition("2", "SIW_당일급등주")
# 10:30 New York time, during the regular session.
NOW = datetime(2026, 11, 9, 15, 30, tzinfo=timezone.utc)
RULES = {
    "type": "rules",
    "entry": {
        "all": [
            {"op": "gt", "left": {"price": "close"}, "right": {"value": 0}},
            {
                "op": "gte",
                "left": {"price": "volume"},
                "right": {"indicator": "sma", "params": {"length": 5}, "output": "value"},
            },
        ]
    },
    "exit": {"op": "lt", "left": {"price": "close"}, "right": {"value": 0}},
}


def at(minutes: int) -> datetime:
    return WINDOW.pre_open_utc + timedelta(minutes=minutes)


def record(data_dir: Path) -> None:
    store = ConditionWatchStore.in_data_dir(data_dir)
    first = store.start(
        CONDITION, "real", at(1), (ConditionTicker("CDT", "ND"), ConditionTicker("AAPL", "ND"))
    )
    store.add(first, at(5), ConditionEvent("enter", "COIN", "ND", None, {}))
    store.add(first, at(9), ConditionEvent("leave", "CDT", "ND", None, {}))
    store.end(first, at(31), "renewed")
    # The renewal's own list is the new start: COIN is gone without a leave event.
    second = store.start(CONDITION, "real", at(31), (ConditionTicker("AAPL", "ND"),))
    store.add(second, at(300), ConditionEvent("enter", "LATE", "NY", None, {}))
    store.close()


def test_candidates_are_first_seen_times_before_the_cutoff(tmp_path: Path) -> None:
    record(tmp_path)
    read = read_watched(watch_path(tmp_path), WINDOW, at(60), CONDITION.name)
    assert read.recorded
    found = {item.symbol: item for item in read.symbols}
    assert list(found) == ["AAPL", "CDT", "COIN"]  # LATE arrived after the cutoff
    assert found["COIN"].first_seen_utc.startswith("2026-11-09T09:05")
    assert {s: item.in_list for s, item in found.items()} == {
        "AAPL": True,
        "CDT": False,
        "COIN": False,
    }
    assert read.coverage.watches == 2 and read.coverage.open_now
    assert read.coverage.interrupted == 0


def test_reading_the_record_never_writes_to_it(tmp_path: Path) -> None:
    record(tmp_path)
    path = watch_path(tmp_path)
    before = sqlite3.connect(path).execute("SELECT * FROM watches").fetchall()
    read_watched(path, WINDOW, at(400), CONDITION.name)
    after = sqlite3.connect(path).execute("SELECT * FROM watches").fetchall()
    # An open watch stays open: ConditionWatchStore would have closed it as interrupted.
    assert before == after and after[-1][5] is None


def test_no_record_is_said_plainly(tmp_path: Path) -> None:
    read = read_watched(watch_path(tmp_path), WINDOW, NOW, CONDITION.name)
    assert not read.recorded and read.symbols == []


def strategy(tmp_path: Path, definition: dict[str, Any]) -> dict[str, Any]:
    store = BacktestStore(tmp_path / "backtests.sqlite3", lambda: NOW)
    return store.save_strategy("screen", definition)


def test_entry_check_reports_each_condition_on_the_last_closed_bar(tmp_path: Path) -> None:
    saved = strategy(tmp_path, RULES)
    outcome = entry_check(saved, "us:aapl:xnas:2026", NOW, synthetic_bars, adjusted_bars)
    assert outcome["status"] == "passed"
    result = outcome["result"]
    # 10:30 on Monday: Friday is the last session that has closed.
    assert result["bar"]["session_date"] == "2026-11-06"
    assert result["adjustment"] == "split_adjusted"
    assert [c["holds"] for c in result["conditions"]] == [True, True]
    assert result["conditions"][1]["left_value"] == "1000"
    assert outcome["strategy"]["version"] == 1


def test_entry_check_refuses_what_daily_bars_cannot_answer(tmp_path: Path) -> None:
    timed = {
        **RULES,
        "entry": {
            "all": [
                RULES["entry"]["all"][0],
                {"time": {"from": "09:30", "to": "10:00"}},
            ]
        },
    }
    outcome = entry_check(strategy(tmp_path, timed), "us:aapl:xnas:2026", NOW, synthetic_bars, None)
    assert outcome["status"] == "unsupported"
    assert "time-of-day" in outcome["result"]["reason"]


@pytest.fixture
def client(tmp_path: Path) -> Iterator[tuple[TestClient, dict[str, Any]]]:
    record(tmp_path)
    saved = strategy(tmp_path, RULES)
    screening = ScreeningStore(tmp_path / "screening.sqlite3", lambda: NOW)
    known = {"AAPL", "COIN", "CDT", "TSLA"}

    def listing_for(symbol: str) -> dict[str, Any] | None:
        if symbol not in known:
            return None
        return instrument_listing(symbol, "XNAS", "test", "test-r1", "test")

    app = FastAPI()
    register_screening_routes(
        app,
        store=lambda: screening,
        data_dir=lambda: tmp_path,
        strategy=lambda sid, version: saved if sid == saved["strategy_id"] else None,
        listing_for=listing_for,
        get_bars=lambda: synthetic_bars,
        get_adjusted=lambda: adjusted_bars,
        error_response=_error_response,
        clock=lambda: NOW,
    )
    with TestClient(app) as test_client:
        yield test_client, saved
    screening.close()


def test_candidates_route_merges_the_record_and_manual_symbols(client: Any) -> None:
    http, _saved = client
    document = http.get("/v1/screening/candidates").json()
    validate_document(document)
    assert document["session_date"] == "2026-11-09"
    assert [c["symbol"] for c in document["candidates"]] == ["AAPL", "CDT", "COIN", "LATE"]
    assert document["candidates"][3]["listing"] is None  # not in the app's symbol list

    added = http.post("/v1/screening/candidates", json={"symbol": "tsla"}).json()
    assert added["candidates"][-1]["symbol"] == "TSLA"
    assert added["candidates"][-1]["sources"] == ["manual"]
    again = http.post("/v1/screening/candidates", json={"symbol": "AAPL"}).json()
    assert again["candidates"][0]["sources"] == ["condition", "manual"]
    assert http.post("/v1/screening/candidates", json={"symbol": "bad one"}).status_code == 422
    assert http.get("/v1/screening/candidates?session_date=2026-11-07").status_code == 422


def test_entry_check_route_stores_a_result_the_card_shows(client: Any) -> None:
    http, saved = client
    body = {"symbol": "AAPL", "strategy_id": saved["strategy_id"]}
    first = http.post("/v1/screening/checks/entry", json=body)
    assert first.status_code == 200, first.text
    check = first.json()
    validate_document(check)
    assert check["status"] == "passed" and check["check_id"] >= 1
    assert check["after_the_fact"] is False
    assert http.get(f"/v1/screening/checks/{check['check_id']}").json() == check

    second = http.post("/v1/screening/checks/entry", json=body).json()
    listed = http.get("/v1/screening/candidates").json()["candidates"][0]
    # Running again adds a result; the card shows the latest.
    assert listed["checks"]["entry"]["check_id"] == second["check_id"] > check["check_id"]

    missing = http.post("/v1/screening/checks/entry", json={**body, "symbol": "LATE"})
    assert missing.status_code == 422
    unknown = http.post("/v1/screening/checks/entry", json={**body, "strategy_id": "nope"})
    assert unknown.status_code == 404
    assert http.get("/v1/screening/checks/999").status_code == 404


def test_without_adjusted_bars_the_check_uses_prices_as_traded(tmp_path: Path) -> None:
    def refuses(*_: Any) -> Any:
        raise InvalidBarsQueryError("this data provider does not offer adjusted daily bars")

    saved = strategy(tmp_path, RULES)
    outcome = entry_check(saved, "us:aapl:xnas:2026", NOW, synthetic_bars, refuses)
    assert outcome["status"] == "passed"
    assert outcome["result"]["adjustment"] == "raw"
    assert "raw prices used" in outcome["warnings"][0]
