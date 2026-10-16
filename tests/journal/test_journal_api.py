from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from packages.contracts.validator import validate_document
from services.api.main import create_app
from services.journal.store import JournalStore

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)
SYNTH = "us:synth:xnas:2026"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.delenv("SIW_DATA_DIR", raising=False)
    app = create_app(journal_store=JournalStore(tmp_path), clock=lambda: NOW)
    with TestClient(app) as test_client:
        yield test_client


def manual(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "execution_id": "exec-0001",
        "instrument_id": SYNTH,
        "side": "buy",
        "quantity": "10",
        "price": "50.25",
        "fees": "1.00",
        "time_precision": "minute",
        "executed_date_et": "2026-10-09",
        "executed_time_et": "10:15",
        "selected_interval_start_utc": "2026-10-09T14:15:00Z",
        "selected_interval_end_utc": "2026-10-09T14:20:00Z",
        "memo": "",
    }
    body.update(changes)
    return body


def scenario(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "scenario_id": "scen-0001",
        "instrument_id": SYNTH,
        "direction": "long",
        "quantity": "10",
        "entry_price": "50.00",
        "exit_price": "51.00",
        "fee_assumption": "1.00",
        "slippage_assumption": "0.01",
        "entry_interval_start_utc": "2026-10-09T14:00:00Z",
        "exit_date_et": "2026-10-09",
        "exit_time_et": "11:00",
    }
    body.update(changes)
    return body


def test_manual_execution_is_recorded_once_with_server_assertion_time(client: TestClient) -> None:
    created = client.post("/v1/trades/manual", json=manual())
    replay = client.post("/v1/trades/manual", json=manual())
    conflict = client.post("/v1/trades/manual", json=manual(price="60"))

    assert created.status_code == 201
    record = created.json()
    validate_document(record)
    assert record["executed_at_utc"] == "2026-10-09T14:15:00Z"
    assert record["asserted_at_utc"] == "2026-10-12T00:00:00Z"
    assert record["evidence_status"] == "unverified"
    assert (replay.status_code, replay.json()) == (200, record)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "journal_conflict"


@pytest.mark.parametrize(
    ("changes", "fragment"),
    [
        ({"time_precision": "unknown"}, "leave the execution time empty"),
        ({"executed_time_et": None}, "enter the execution date and time"),
        ({"executed_time_et": "10:15:30"}, "must not include seconds"),
        ({"executed_date_et": "2026-11-01", "executed_time_et": "01:30"}, "ambiguous"),
        ({"executed_date_et": "2026-10-13"}, "after it was recorded"),
        ({"quantity": "0"}, "must be positive"),
        ({"instrument_id": "us:missing:xnas:2026"}, "not available"),
    ],
)
def test_invalid_manual_executions_are_rejected(
    client: TestClient, changes: dict[str, Any], fragment: str
) -> None:
    response = client.post("/v1/trades/manual", json=manual(**changes))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_journal_entry"
    assert fragment in response.json()["error"]["message"]


def test_request_shape_errors_use_the_validation_envelope(client: TestClient) -> None:
    response = client.post("/v1/trades/manual", json=manual(account_number="12345678"))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_ledgers_stay_separate_and_report_their_own_pnl(client: TestClient) -> None:
    client.post("/v1/trades/manual", json=manual())
    client.post(
        "/v1/trades/manual",
        json=manual(execution_id="exec-0002", side="sell", price="51.25", executed_time_et="11:00"),
    )
    client.post("/v1/scenarios", json=scenario())

    manual_ledger = client.get("/v1/trades/manual", params={"instrument_id": SYNTH}).json()
    hypothetical = client.get("/v1/scenarios").json()

    assert manual_ledger["ledger"] == "manual_actual"
    assert [item["execution_id"] for item in manual_ledger["executions"]] == [
        "exec-0001",
        "exec-0002",
    ]
    assert manual_ledger["positions"][0]["realized_pnl"] == "8.00"
    assert hypothetical["ledger"] == "hypothetical"
    [item] = hypothetical["scenarios"]
    assert item["scenario"]["exit_interval_start_utc"] == "2026-10-09T15:00:00Z"
    assert (item["gross_pnl"], item["costs"], item["net_pnl"]) == ("10.00", "1.20", "8.80")
    assert "scen-0001" not in str(manual_ledger)


def test_scenario_exit_must_follow_entry(client: TestClient) -> None:
    response = client.post("/v1/scenarios", json=scenario(exit_time_et="09:30"))

    assert response.status_code == 422
    assert "exit must come after its entry" in response.json()["error"]["message"]


def test_journal_requires_a_configured_data_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SIW_DATA_DIR", raising=False)
    with TestClient(create_app()) as unconfigured:
        response = unconfigured.post("/v1/trades/manual", json=manual())
        listing = unconfigured.get("/v1/scenarios")

    assert response.status_code == listing.status_code == 503
    assert response.json()["error"]["code"] == "journal_storage_unconfigured"


def test_browser_may_post_journal_entries_from_the_local_shell(client: TestClient) -> None:
    preflight = client.options(
        "/v1/trades/manual",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )

    assert preflight.status_code == 200
    assert "POST" in preflight.headers["access-control-allow-methods"]


def test_journal_is_a_separate_database(tmp_path: Path) -> None:
    store = JournalStore(tmp_path)

    assert store.database_path.name == "journal.sqlite3"
    assert not (tmp_path / "metadata.sqlite3").exists()
