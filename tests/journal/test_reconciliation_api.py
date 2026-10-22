from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from packages.contracts.validator import validate_document
from services.api.main import create_app
from services.journal.store import JournalStore
from tests.journal.test_reconcile import broker, imported, manual

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path: Path) -> JournalStore:
    return JournalStore(tmp_path)


@pytest.fixture
def client(store: JournalStore, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.delenv("SIW_DATA_DIR", raising=False)
    app = create_app(journal_store=store, clock=lambda: NOW)
    with TestClient(app) as test_client:
        yield test_client


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_reconciliation_reads_all_three_ledgers_and_writes_nothing(
    client: TestClient, store: JournalStore
) -> None:
    assert store.record_broker_execution(broker("o1")) == "new"
    assert store.record_broker_execution(broker("o2", side="sell")) == "new"
    store.record_manual(manual("man-0001"), "a" * 64)
    row = imported("imp-0001", 1, quantity="7")
    store.commit_import(
        {
            "kind": "import_batch",
            "schema_version": "0.1",
            "batch_id": "batch-0001",
            "file_name": "statement.csv",
            "file_sha256": "a" * 64,
            "broker_label": "Toss",
            "committed_at_utc": "2026-10-10T00:00:00Z",
            "row_count": 1,
            "imported_execution_ids": ["imp-0001"],
            "skipped_rows": [],
        },
        [row],
    )
    before = _digest(store.database_path)

    response = client.get("/v1/trades/reconciliation")

    assert response.status_code == 200
    document = response.json()
    validate_document(document)
    assert document["view"] == "computed"
    assert document["summary"] == {
        "matched": 1,
        "conflicting": 2,
        "broker_only": 1,
        "manual_only": 0,
        "imported_only": 0,
    }
    statuses = {record["execution_id"]: record["status"] for record in document["records"]}
    assert statuses["man-0001"] == "matched"
    assert statuses["imp-0001"] == "conflicting"
    assert statuses["brk-o1"] == "conflicting"
    assert statuses["brk-o2"] == "broker_only"
    assert _digest(store.database_path) == before


def test_reconciliation_filters_by_instrument_and_since(
    client: TestClient, store: JournalStore
) -> None:
    store.record_broker_execution(broker("o1"))
    store.record_broker_execution(broker("o2", instrument_id="us:msft:xnas:2026", symbol="MSFT"))

    one = client.get("/v1/trades/reconciliation", params={"instrument_id": "us:msft:xnas:2026"})
    later = client.get("/v1/trades/reconciliation", params={"since": "2026-10-10T00:00:00Z"})
    tight = client.get("/v1/trades/reconciliation", params={"price_tolerance_bps": "2.5"})

    assert [record["execution_id"] for record in one.json()["records"]] == ["brk-o2"]
    assert one.json()["instrument_id"] == "us:msft:xnas:2026"
    assert later.json()["records"] == []
    assert later.json()["since_utc"] == "2026-10-10T00:00:00Z"
    assert tight.json()["parameters"]["price_tolerance_bps"] == "2.5"


@pytest.mark.parametrize(
    "params",
    [{"since": "2026-10-10"}, {"price_tolerance_bps": "-1"}, {"price_tolerance_bps": "99999"}],
)
def test_reconciliation_rejects_bad_parameters(client: TestClient, params: dict[str, str]) -> None:
    assert client.get("/v1/trades/reconciliation", params=params).status_code == 422


def test_reconciliation_needs_journal_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SIW_DATA_DIR", raising=False)
    with TestClient(create_app(clock=lambda: NOW)) as test_client:
        response = test_client.get("/v1/trades/reconciliation")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "journal_storage_unconfigured"


def test_limits_list_the_newest_and_still_count_everything(
    client: TestClient, store: JournalStore
) -> None:
    for minute in range(5):
        store.record_broker_execution(
            broker(f"o{minute}", executed_at_utc=f"2026-10-09T14:0{minute}:30Z")
        )
    recon = client.get("/v1/trades/reconciliation", params={"limit": 2}).json()
    validate_document(recon)
    assert [r["execution_id"] for r in recon["records"]] == ["brk-o3", "brk-o4"]
    assert recon["record_count"] == 5
    assert recon["summary"]["broker_only"] == 5

    ledger = client.get("/v1/trades/broker", params={"limit": 2}).json()
    validate_document(ledger)
    assert [e["execution_id"] for e in ledger["executions"]] == ["brk-o3", "brk-o4"]
    assert ledger["execution_count"] == 5
    # Positions always cover the whole ledger: five buys of 10.
    assert ledger["positions"][0]["open_quantity"] == "50"
    assert client.get("/v1/trades/broker", params={"limit": 0}).status_code == 422


def test_until_status_filter_ledger_counts_and_breakdown(
    client: TestClient, store: JournalStore
) -> None:
    store.record_broker_execution(broker("o1", executed_at_utc="2026-10-09T14:00:30Z"))
    store.record_broker_execution(broker("o2", executed_at_utc="2026-10-09T23:30:00Z"))
    store.record_broker_execution(
        broker(
            "o3",
            instrument_id="us:msft:xnas:2026",
            symbol="MSFT",
            executed_at_utc="2026-10-10T14:00:00Z",
        )
    )
    store.record_manual(manual("man-0001"), "a" * 64)
    before = _digest(store.database_path)

    whole = client.get("/v1/trades/reconciliation").json()
    validate_document(whole)
    assert whole["ledger_counts"] == {
        "broker_api": 3,
        "manual_actual": 1,
        "statement_imported": 0,
    }
    # Days are exchange-local: 23:30Z on 10-09 is still 10-09 in New York.
    assert [(day["date"], sum(day["counts"].values())) for day in whole["breakdown"]["by_day"]] == [
        ("2026-10-10", 1),
        ("2026-10-09", 3),
    ]
    by_instrument = {
        row["instrument_id"]: row["counts"] for row in whole["breakdown"]["by_instrument"]
    }
    assert by_instrument["us:msft:xnas:2026"]["broker_only"] == 1
    assert by_instrument["us:aapl:xnas:2026"]["matched"] == 2

    early = client.get("/v1/trades/reconciliation", params={"until": "2026-10-10T00:00:00Z"}).json()
    validate_document(early)
    assert early["until_utc"] == "2026-10-10T00:00:00Z"
    assert "brk-o3" not in {record["execution_id"] for record in early["records"]}

    only = client.get("/v1/trades/reconciliation", params={"status": "broker_only"}).json()
    validate_document(only)
    assert only["status_filter"] == "broker_only"
    assert {record["status"] for record in only["records"]} == {"broker_only"}
    assert only["record_count"] == len(only["records"])
    # The summary and breakdown still count every status.
    assert only["summary"] == whole["summary"]
    assert only["breakdown"] == whole["breakdown"]

    assert client.get("/v1/trades/reconciliation", params={"status": "merged"}).status_code == 422
    assert _digest(store.database_path) == before


def test_days_follow_each_records_exchange_calendar(
    client: TestClient, store: JournalStore
) -> None:
    # 23:30Z on 10-09 is 10-09 in New York but would be 10-10 in UTC.
    store.record_broker_execution(broker("o1", executed_at_utc="2026-10-09T23:30:00Z"))
    store.record_broker_execution(broker("o2", executed_at_utc="2026-10-10T14:00:00Z"))

    day = client.get(
        "/v1/trades/reconciliation",
        params={"from_date": "2026-10-09", "to_date": "2026-10-09"},
    ).json()
    validate_document(day)
    assert [record["execution_id"] for record in day["records"]] == ["brk-o1"]
    assert day["from_date"] == "2026-10-09"
    assert day["summary"]["broker_only"] == 1
    assert (
        client.get("/v1/trades/reconciliation", params={"from_date": "10/09/2026"}).status_code
        == 422
    )
