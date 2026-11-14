"""Daily candidate screening S1 (ADR 0052): candidates from the condition record and the
routes. Entry check A was retired (ADR 0057). No test reaches the network."""

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
from services.collector.condition_watch import ConditionWatchStore
from services.market_data.broker_common import instrument_listing
from services.market_data.calendar import UsEquityCalendar
from services.market_data.kiwoom_conditions import Condition, ConditionEvent, ConditionTicker
from services.screening.candidates import read_watched, watch_path
from services.screening.store import ScreeningStore
from tests.backtest.test_backtest_api import synthetic_bars

CALENDAR = UsEquityCalendar("XNAS")
DAY = date(2026, 11, 9)  # Monday
WINDOW = CALENDAR.window_for_date(DAY)
assert WINDOW is not None
CONDITION = Condition("2", "SIW_당일급등주")
# 10:30 New York time, during the regular session.
NOW = datetime(2026, 11, 9, 15, 30, tzinfo=timezone.utc)


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


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    record(tmp_path)
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
        listing_for=listing_for,
        get_bars=lambda: synthetic_bars,
        error_response=_error_response,
        clock=lambda: NOW,
    )
    with TestClient(app) as test_client:
        yield test_client
    screening.close()


def test_candidates_route_merges_the_record_and_manual_symbols(client: Any) -> None:
    http = client
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


def test_entry_check_a_is_retired(client: Any) -> None:
    http = client
    # ADR 0057 retired check A: the route is gone, unknown checks are still 404.
    gone = http.post("/v1/screening/checks/entry", json={"symbol": "AAPL", "strategy_id": "x"})
    assert gone.status_code in (404, 405)
    assert http.get("/v1/screening/checks/999").status_code == 404


def test_the_candidate_list_carries_every_check_kind(client: Any, tmp_path: Path) -> None:
    http = client
    other = ScreeningStore(tmp_path / "screening.sqlite3", lambda: NOW)
    for kind in ("news", "off_exchange", "dilution", "off_exchange_structure"):
        other.add_check(
            {
                "session_date": "2026-11-09",
                "symbol": "AAPL",
                "check": kind,
                "status": "pending",
                "cutoff_utc": "2026-11-09T13:00:00.000Z",
                "created_at_utc": "2026-11-09T13:00:00.000Z",
            }
        )
    other.close()
    document = http.get("/v1/screening/candidates").json()
    validate_document(document)
    checks = document["candidates"][0]["checks"]
    assert set(checks) == {"news", "off_exchange", "dilution", "off_exchange_structure"}
