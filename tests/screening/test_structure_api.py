"""`POST /v1/screening/checks/off_exchange_structure` (ADR 0053). No network: trades,
splits and daily bars come from fakes shaped like the Alpaca adapter's."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.contracts.validator import validate_document, validation_errors
from services.api.main import _error_response
from services.api.screening_structure_routes import register_screening_structure_routes
from services.market_data.broker_common import instrument_listing
from services.market_data.filing_splits import ProviderSplit
from services.screening.store import ScreeningStore
from services.screening.structure.service import StructureStore, open_structure
from services.screening.structure.sessions import SessionFiller
from tests.screening.structure_fixtures import NOW, FakeTrades, trade, windows

LEVELS = [
    ("1.80", 5000, 100),
    ("1.86", 30, 20),
    ("1.90", 40, 20),
    ("1.95", 50, 40),
    ("2.00", 300, 200),
    ("2.04", 30, 10),
    ("2.10", 20, 10),
    ("2.20", 25, 10),
    ("2.30", 35, 10),
    ("2.40", 6000, 900),
]


def tape(days: int = 20) -> dict[str, list[dict[str, Any]]]:
    by_day: dict[str, list[dict[str, Any]]] = {}
    for window in windows(days):
        start = window.regular_open_utc
        trades = []
        for index, (price, off, lit) in enumerate(LEVELS):
            stamp = (start + timedelta(minutes=index)).isoformat().replace("+00:00", "Z")
            trades.append(trade(stamp, price, off))
            trades.append(trade(stamp, price, lit, x="Q"))
        by_day[window.session_date.isoformat()] = trades
    # Today's pre-market trade before the cutoff: the reference price.
    by_day["2026-11-06"] = [trade("2026-11-06T13:00:00Z", "2.01", 100, x="Q")]
    return by_day


@dataclass
class Config:
    publication_delay_minutes: int = 15


@dataclass
class Bars:
    bars: tuple[dict[str, Any], ...]


class FakeAlpaca:
    def __init__(self, trades: FakeTrades, splits: list[ProviderSplit] | None = None) -> None:
        self.trade_pages = trades
        self.config = Config()
        self.splits = splits or []

    def split_actions(self, symbol: str, since: Any, until: Any) -> list[ProviderSplit]:
        return self.splits

    def get_bars(self, query: Any) -> Bars:
        bars = []
        for window in windows(25):
            bars.append(
                {
                    "interval_start_utc": window.regular_open_utc.isoformat().replace(
                        "+00:00", "Z"
                    ),
                    "open": "2",
                    "high": "2.1",
                    "low": "1.9",
                    "close": "2",
                    "is_final": True,
                    "source_id": "alpaca-sip",
                    "adjustment": "raw",
                }
            )
        return Bars(tuple(bars))


def listing_for(symbol: str) -> dict[str, Any] | None:
    if symbol != "STAK":
        return None
    return instrument_listing(symbol, "XNAS", "test", "test-r1", "test")


@dataclass
class Setup:
    http: TestClient
    screening: ScreeningStore
    structure: StructureStore
    adapter: FakeAlpaca


def build(tmp_path: Path, adapter: FakeAlpaca, inline: bool = True) -> Iterator[Setup]:
    screening = ScreeningStore(tmp_path / "screening.sqlite3", lambda: NOW)
    structure = open_structure(tmp_path / "screening_structure.sqlite3", lambda: NOW)
    filler = SessionFiller(structure.cache, lambda: adapter.trade_pages, lambda: NOW, inline=inline)
    app = FastAPI()
    register_screening_structure_routes(
        app,
        store=lambda: screening,
        structure=lambda: (structure, filler),
        trades=lambda: adapter,
        listing_for=listing_for,
        error_response=_error_response,
        clock=lambda: NOW,
        wait_seconds=0 if not inline else 1,
        sleep=lambda _s: None,
    )
    with TestClient(app) as http:
        yield Setup(http, screening, structure, adapter)
    screening.close()


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[Setup]:
    yield from build(tmp_path, FakeAlpaca(FakeTrades(tape())))


def test_structure_check_is_stored_without_research_scores(setup: Setup) -> None:
    response = setup.http.post(
        "/v1/screening/checks/off_exchange_structure", json={"symbol": "stak"}
    )
    assert response.status_code == 200, response.text
    check = response.json()
    validate_document(check)
    assert check["check"] == "off_exchange_structure"
    assert check["status"] == "ok"
    result = check["result"]
    assert result["label"] == "장외 체결 가격 구조"
    assert "다크풀" not in result["label"] and "스트레스" not in result["label"]
    assert result["data_until_utc"] == "2026-11-06T15:15:00Z"
    assert result["forecast_status"] == "NOT_ISSUED"
    assert result["stage"] == "UNKNOWN"
    assert result["scores"] is None
    assert result["deployment_stage"] == "RESEARCH_ONLY"
    assert [g["code"] for g in result["eligibility"]["hard_failures"]] == [
        "H10_PARAMETER_SET_NOT_OPERATIONALLY_APPROVED"
    ]
    assert result["reference_price"] == {
        "value": "2.01",
        "timestamp_utc": "2026-11-06T13:00:00Z",
        "session": "pre",
        "source": "alpaca-sip",
    }
    assert result["progress"]["cached_sessions"] == 20
    assert result["zones"] and {z["side"] for z in result["zones"]} <= {"above", "below", "at"}
    # No score of any kind reaches the card before approval.
    assert "stress_up" not in str(check) and "path_instability" not in str(check)
    # The registry keeps the forecast and, apart from it, the research scores.
    forecast = setup.structure.registry.forecast(result["forecast_id"])
    assert forecast is not None and forecast["forecast_sha256"] == result["forecast_sha256"]
    research = setup.structure.registry.research(result["forecast_id"])
    assert research is not None and research["scores"]["path_instability"] is not None
    assert setup.screening.check(check["check_id"]) == check


def test_split_in_window_blocks(tmp_path: Path) -> None:
    from datetime import date
    from fractions import Fraction

    adapter = FakeAlpaca(FakeTrades(tape()), [ProviderSplit(date(2026, 10, 20), Fraction(1, 10))])
    setup = next(build(tmp_path, adapter))
    check = setup.http.post(
        "/v1/screening/checks/off_exchange_structure", json={"symbol": "STAK"}
    ).json()
    assert check["status"] == "incomplete"
    codes = [g["code"] for g in check["result"]["eligibility"]["hard_failures"]]
    assert "H04_CORPORATE_ACTION_IN_WINDOW" in codes
    assert setup.structure.registry.research(check["result"]["forecast_id"]) is None


def test_first_run_is_pending_while_sessions_load(tmp_path: Path) -> None:
    gate = threading.Event()
    inner = FakeTrades(tape())

    def slow(symbol: str, start: datetime, end: datetime, sort: str = "asc") -> Any:
        gate.wait(5)
        return inner(symbol, start, end, sort)

    adapter = FakeAlpaca(inner)
    adapter.trade_pages = slow  # type: ignore[assignment]
    setup = next(build(tmp_path, adapter, inline=False))
    first = setup.http.post(
        "/v1/screening/checks/off_exchange_structure", json={"symbol": "STAK"}
    ).json()
    validate_document(first)
    assert first["status"] == "pending"
    assert "forecast_id" not in first["result"]
    gate.set()
    deadline = datetime.now(timezone.utc) + timedelta(seconds=10)
    second: dict[str, Any] = {}
    while datetime.now(timezone.utc) < deadline:
        second = setup.http.post(
            "/v1/screening/checks/off_exchange_structure", json={"symbol": "STAK"}
        ).json()
        if second["status"] != "pending":
            break
    assert second["status"] == "ok"
    assert second["result"]["progress"]["cached_sessions"] == 20


def test_contract_refuses_a_stage_without_an_issued_forecast(setup: Setup) -> None:
    check = setup.http.post(
        "/v1/screening/checks/off_exchange_structure", json={"symbol": "STAK"}
    ).json()
    leaked = {**check, "result": {**check["result"], "stage": "HIGH", "scores": {"x": "1"}}}
    assert validation_errors(leaked)


def test_unknown_symbol_and_missing_key(setup: Setup) -> None:
    response = setup.http.post(
        "/v1/screening/checks/off_exchange_structure", json={"symbol": "NOPE"}
    )
    assert response.status_code == 422
