"""Check C (ADR 0052): the dilution analyzer's service, faked with httpx.MockTransport."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.contracts.validator import validate_document
from services.api.main import _error_response
from services.api.screening_routes import register_screening_routes
from services.market_data.broker_common import instrument_listing
from services.screening.dilution import DilutionService, DilutionServiceError, supply_levels
from services.screening.store import ScreeningStore

NOW = datetime(2026, 11, 9, 13, 0, tzinfo=timezone.utc)  # 08:00 New York, pre-market
JOB = "a" * 32
RESULT = "b" * 32


def value(amount: str | None, status: str, unit: str = "usd_per_share") -> dict[str, Any]:
    return {"amount": amount, "unit": unit, "status": status}


CONTRACT: dict[str, Any] = {
    "schema_version": "1.17",
    "meta": {"ticker": "LABT", "as_of": "2026-11-09", "input_hash": "c" * 64},
    "issuer": {"fpi": False, "warnings": []},
    "securities": [
        {
            "id": "S1",
            "kind": "warrant",
            "description": "Series A Warrant",
            "price": value("1.25", "confirmed"),
            "price_range": None,
            "pricing": None,
            "outstanding": value("2000000", "computed", "shares"),
            "conditions": [],
            "gates": [{"kind": "shareholder_approval", "state": "unknown"}],
        },
        {
            "id": "S2",
            "kind": "convertible_preferred",
            "description": "Series C Preferred (기준 시점 뒤 발행)",
            "price": value(None, "unverified"),
            "price_range": None,
            "pricing": {"floor": value("0.30", "inferred")},
            "outstanding": value(None, "unverified", "shares"),
            "conditions": [
                {"type": "issuance_blocked_below", "threshold": value("0.50", "confirmed")}
            ],
            "gates": [],
        },
    ],
    "capacity": [{"security_id": "S3", "nominal": value("5000000", "confirmed", "usd")}],
    "dilution": {"as_of": {"incomplete_reasons": []}},
    "shelves": {"notes": ["shelf note"], "items": []},
    "resale": None,
}


def test_supply_levels_keep_unknowns_unknown() -> None:
    levels = supply_levels(CONTRACT)
    first, second = levels["securities"]
    assert first["price"] == {
        "amount": "1.25",
        "unit": "usd_per_share",
        "status": "confirmed",
        "fact": True,
    }
    assert first["approval_unknown"] and not first["after_base_point"]
    # An unknown price stays null and is not shown as a fact.
    assert second["price"]["amount"] is None and second["price"]["fact"] is False
    assert second["after_base_point"]
    assert second["floor"]["amount"] == "0.30"
    assert second["conditions"][0]["threshold"]["amount"] == "0.50"
    assert levels["capacity"][0]["nominal"]["amount"] == "5000000"
    assert levels["incomplete_reasons"] == []


def test_the_service_must_be_on_this_computer(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.screening.dilution import service_url

    monkeypatch.setenv("SIW_DILUTION_URL", "http://example.com:8775")
    with pytest.raises(DilutionServiceError):
        service_url()


class Analyzer:
    """A fake analyzer: jobs answer `states` in turn, then the result."""

    def __init__(self, states: list[str], contract: dict[str, Any] | None = None) -> None:
        self.states = states
        self.contract = contract or CONTRACT
        self.posted: list[dict[str, Any]] = []
        self.down = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("connection refused")
        assert request.url.host == "127.0.0.1"
        if request.method == "POST" and request.url.path == "/api/v1/analyses":
            self.posted.append(json.loads(request.content))
            return httpx.Response(202, json={"job_id": JOB})
        if request.url.path == f"/api/v1/analyses/{JOB}":
            state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
            envelope: dict[str, Any] = {"state": state, "reused": False}
            if state == "done":
                envelope["result_id"] = RESULT
            return httpx.Response(200, json=envelope)
        if request.url.path == f"/api/v1/results/{RESULT}":
            return httpx.Response(200, content=json.dumps(self.contract).encode())
        return httpx.Response(404)


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[tuple[TestClient, list[Analyzer]]]:
    analyzer = [Analyzer(["done"])]
    screening = ScreeningStore(tmp_path / "screening.sqlite3", lambda: NOW)
    app = FastAPI()
    register_screening_routes(
        app,
        store=lambda: screening,
        data_dir=lambda: tmp_path,
        listing_for=lambda s: instrument_listing(s, "XNAS", "test", "test-r1", "test"),
        get_bars=lambda: lambda query: None,  # type: ignore[arg-type,return-value]
        error_response=_error_response,
        clock=lambda: NOW,
        dilution_service=lambda: DilutionService(
            "http://127.0.0.1:8775", transport=httpx.MockTransport(analyzer[0])
        ),
        dilution_wait_seconds=2,
        sleep=lambda _seconds: None,
    )
    with TestClient(app) as client:
        yield client, analyzer
    screening.close()


def test_a_rules_only_analysis_is_stored_with_its_contract(setup: Any) -> None:
    client, analyzer = setup
    response = client.post("/v1/screening/checks/dilution", json={"symbol": "labt"})
    check = response.json()
    validate_document(check)
    assert check["status"] == "ok" and check["check"] == "dilution"
    assert analyzer[0].posted == [{"ticker": "LABT", "as_of": "2026-11-09", "llm": False}]
    assert check["result"]["llm"] is False
    assert check["result"]["contract"] == CONTRACT
    assert check["result"]["securities"][0]["id"] == "S1"


def test_an_incomplete_analysis_says_so(setup: Any) -> None:
    client, analyzer = setup
    partial = {
        **CONTRACT,
        "dilution": {"as_of": {"incomplete_reasons": [{"code": "llm_disabled"}]}},
    }
    analyzer[0] = Analyzer(["done"], partial)
    check = client.post("/v1/screening/checks/dilution", json={"symbol": "LABT"}).json()
    assert check["status"] == "incomplete"
    assert check["result"]["incomplete_reasons"] == ["llm_disabled"]


def test_a_slow_analysis_waits_and_a_refresh_picks_it_up(setup: Any) -> None:
    client, analyzer = setup
    analyzer[0] = Analyzer(["queued", "running", "running", "running", "done"])
    waiting = client.post(
        "/v1/screening/checks/dilution", json={"symbol": "LABT", "llm": True}
    ).json()
    assert waiting["status"] == "pending" and waiting["result"]["job_id"] == JOB
    assert analyzer[0].posted[0]["llm"] is True
    done = client.post(f"/v1/screening/checks/{waiting['check_id']}/refresh").json()
    assert done["status"] == "ok" and done["check_id"] > waiting["check_id"]
    assert done["result"]["llm"] is True
    # Only one analysis was asked for.
    assert len(analyzer[0].posted) == 1


def test_a_stopped_analyzer_leaves_the_check_waiting(setup: Any) -> None:
    client, analyzer = setup
    analyzer[0].down = True
    check = client.post("/v1/screening/checks/dilution", json={"symbol": "LABT"}).json()
    assert check["status"] == "pending"
    assert "did not answer" in check["result"]["reason"]
    # Once it runs, a refresh asks for the analysis.
    analyzer[0].down = False
    done = client.post(f"/v1/screening/checks/{check['check_id']}/refresh").json()
    assert done["status"] == "ok"


def test_a_blocked_or_unsupported_analysis_has_no_result(setup: Any) -> None:
    client, analyzer = setup
    analyzer[0] = Analyzer(["unsupported"])
    check = client.post("/v1/screening/checks/dilution", json={"symbol": "LABT"}).json()
    assert check["status"] == "unsupported" and "contract" not in check["result"]
    analyzer[0] = Analyzer(["blocked"])
    check = client.post("/v1/screening/checks/dilution", json={"symbol": "LABT"}).json()
    assert check["status"] == "error"
