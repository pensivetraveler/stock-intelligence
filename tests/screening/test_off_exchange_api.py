"""`POST /v1/screening/checks/off_exchange` (ADR 0052 §5, S2). No network: Alpaca trades
come through `httpx.MockTransport` and FINRA through a stub review."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.contracts.validator import validate_document
from services.api.main import _error_response
from services.api.screening_off_exchange_routes import register_screening_off_exchange_routes
from services.market_data.broker_common import instrument_listing
from services.public_sources.http import SourceConfigError
from services.screening.store import ScreeningStore
from services.screening.structure.service import open_structure
from services.screening.structure.sessions import SessionFiller
from tests.screening.test_off_exchange import NOW, Tape, adapter, friday_and_thursday


class Stub:
    def __init__(self) -> None:
        self.review: Any = None
        self.trades: Any = None


def shared(tmp_path: Path, trades: Any) -> Any:
    """The structure model's session cache with an inline reader, as the app shares it."""
    structure = open_structure(tmp_path / "screening_structure.sqlite3", lambda: NOW)

    def pages() -> Any:
        adapter = trades()
        return None if adapter is None else adapter.trade_pages

    filler = SessionFiller(structure.cache, pages, lambda: NOW, inline=True)
    return lambda: (structure, filler)


def listing_for(symbol: str) -> dict[str, Any] | None:
    if symbol != "STAK":
        return None
    return instrument_listing(symbol, "XNAS", "test", "test-r1", "test")


@pytest.fixture
def client(tmp_path: Path) -> Iterator[tuple[TestClient, Stub, ScreeningStore]]:
    screening = ScreeningStore(tmp_path / "screening.sqlite3", lambda: NOW)
    stub = Stub()
    stub.trades = adapter(friday_and_thursday())
    app = FastAPI()
    register_screening_off_exchange_routes(
        app,
        store=lambda: screening,
        structure=shared(tmp_path, lambda: stub.trades),
        trades=lambda: stub.trades,
        listing_for=listing_for,
        finra_review=lambda: stub.review,
        error_response=_error_response,
        clock=lambda: NOW,
    )
    with TestClient(app) as test_client:
        yield test_client, stub, screening
    screening.close()


def test_the_check_is_stored_as_a_screening_check(client: Any) -> None:
    http, _stub, screening = client
    response = http.post("/v1/screening/checks/off_exchange", json={"symbol": "stak"})
    assert response.status_code == 200, response.text
    check = response.json()
    validate_document(check)
    assert check["kind"] == "screening_check" and check["check"] == "off_exchange"
    assert check["check_id"] >= 1
    assert check["session_date"] == "2026-11-06"
    assert check["instrument_id"] == listing_for("STAK")["instrument"]["instrument_id"]  # type: ignore[index]
    assert check["cutoff_utc"] == "2026-11-06T15:30:00.000Z"
    assert check["after_the_fact"] is False
    assert check["status"] in ("ok", "incomplete")
    assert check["result"]["label"] == "장외 체결(TRF 보고)"
    # 15 minutes of publication delay: nothing after 15:15 is read.
    assert check["result"]["data_until_utc"] == "2026-11-06T15:15:00Z"
    assert check["result"]["levels"]
    assert any("FINRA 주간 ATS 맥락" in text for text in check["warnings"])
    assert screening.check(check["check_id"]) == check


def test_finra_weekly_ats_is_attached_as_context(client: Any) -> None:
    http, stub, _screening = client
    seen: list[tuple[str, datetime]] = []

    class Evidence:
        cutoff_utc = "2026-11-06T15:30:00.000Z"
        facts: list[Any] = []
        missing = ["ats_shares"]
        uncertainty = ["FINRA weekly OTC/ATS data is delayed aggregate volume."]

    def review(instrument_id: str, cutoff: datetime) -> Evidence:
        seen.append((instrument_id, cutoff))
        return Evidence()

    stub.review = review
    check = http.post("/v1/screening/checks/off_exchange", json={"symbol": "STAK"}).json()
    validate_document(check)
    assert seen and seen[0][1] == NOW
    assert check["result"]["context"]["finra_weekly_ats"]["missing"] == ["ats_shares"]
    assert "finra_weekly_ats" not in check["result"]["missing"]


def test_a_finra_failure_is_a_warning_not_an_error(client: Any) -> None:
    http, stub, _screening = client

    def review(instrument_id: str, cutoff: datetime) -> Any:
        raise SourceConfigError("finra_unconfigured", "no FINRA credential")

    stub.review = review
    check = http.post("/v1/screening/checks/off_exchange", json={"symbol": "STAK"}).json()
    validate_document(check)
    assert check["status"] in ("ok", "incomplete")
    assert any("SourceConfigError" in text for text in check["warnings"])


def test_refusals(client: Any) -> None:
    http, stub, _screening = client
    route = "/v1/screening/checks/off_exchange"
    assert http.post(route, json={"symbol": "bad one"}).status_code == 422
    assert http.post(route, json={"symbol": "NOPE"}).status_code == 422
    weekend = {"symbol": "STAK", "session_date": "2026-11-07"}
    assert http.post(route, json=weekend).status_code == 422
    stub.trades = None
    assert http.post(route, json={"symbol": "STAK"}).json()["error"]["code"] == (
        "alpaca_unconfigured"
    )


def test_a_past_session_is_after_the_fact(tmp_path: Path) -> None:
    screening = ScreeningStore(tmp_path / "screening.sqlite3", lambda: NOW)
    app = FastAPI()
    register_screening_off_exchange_routes(
        app,
        store=lambda: screening,
        structure=shared(tmp_path, lambda: adapter(Tape({}))),
        trades=lambda: adapter(Tape({})),
        listing_for=listing_for,
        finra_review=lambda: None,
        error_response=_error_response,
        clock=lambda: NOW,
    )
    with TestClient(app) as http:
        body = {"symbol": "STAK", "session_date": "2026-11-05"}
        check = http.post("/v1/screening/checks/off_exchange", json=body).json()
    screening.close()
    validate_document(check)
    assert check["after_the_fact"] is True
    assert check["cutoff_utc"] == "2026-11-06T01:00:00.000Z"
    assert check["status"] == "no_data"


def test_finra_time_comes_out_of_the_trade_budget(tmp_path: Path) -> None:
    clock = {"now": 0.0}

    def review(instrument_id: str, cutoff: datetime) -> Any:
        clock["now"] += 35.0  # a slow FINRA context
        raise SourceConfigError("finra_unconfigured", "no FINRA credential")

    screening = ScreeningStore(tmp_path / "screening.sqlite3", lambda: NOW)
    app = FastAPI()
    trades = adapter(friday_and_thursday())
    register_screening_off_exchange_routes(
        app,
        store=lambda: screening,
        structure=shared(tmp_path, lambda: trades),
        trades=lambda: trades,
        listing_for=listing_for,
        finra_review=lambda: review,
        error_response=_error_response,
        clock=lambda: NOW,
        monotonic=lambda: clock["now"],
        sleep=lambda _seconds: None,
    )
    with TestClient(app) as http:
        check = http.post("/v1/screening/checks/off_exchange", json={"symbol": "STAK"}).json()
    screening.close()
    validate_document(check)
    # 40 s in all, 35 s spent on FINRA: the trades get the 10 s floor, not 5 s.
    assert check["result"]["limits"]["max_seconds"] == 10.0
    assert check["result"]["limits"]["live_max_seconds"] == 10.0
    assert check["result"]["limits"]["max_pages"] == 80


def test_fewer_sessions_can_be_asked(client: Any) -> None:
    http, _stub, _screening = client
    route = "/v1/screening/checks/off_exchange"
    check = http.post(route, json={"symbol": "STAK", "sessions": 5}).json()
    validate_document(check)
    assert check["result"]["sessions_requested"] == 5
    assert check["result"]["progress"]["total_sessions"] == 5
    assert http.post(route, json={"symbol": "STAK", "sessions": 21}).status_code == 422
