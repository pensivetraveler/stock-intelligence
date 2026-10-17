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
STATEMENTS = Path(__file__).resolve().parents[1] / "fixtures" / "statements"


@pytest.fixture
def journal_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def client(journal_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.delenv("SIW_DATA_DIR", raising=False)
    app = create_app(journal_store=JournalStore(journal_dir), clock=lambda: NOW)
    with TestClient(app) as test_client:
        yield test_client


def body(name: str, **changes: Any) -> dict[str, Any]:
    values: dict[str, Any] = {"file_name": name, "broker_label": "Example Broker"}
    values.update(changes)
    if "content" not in values:
        values["content"] = (STATEMENTS / name).read_text(encoding="utf-8")
    return values


def test_preview_reports_rows_without_storing_anything(
    client: TestClient, journal_dir: Path
) -> None:
    response = client.post("/v1/imports/trades/preview", json=body("with_execution_ids.csv"))

    assert response.status_code == 200
    preview = response.json()
    validate_document(preview)
    assert preview["summary"] == {
        "new": 3,
        "duplicate_exact": 1,
        "possible_duplicate": 0,
        "invalid": 1,
    }
    assert preview["already_imported"] is False
    assert "12345678" not in response.text
    assert client.get("/v1/trades/imported").json()["executions"] == []


def test_same_file_imports_once_and_never_stores_unmapped_columns(
    client: TestClient, journal_dir: Path
) -> None:
    first = client.post("/v1/imports/trades/commit", json=body("with_execution_ids.csv"))
    again = client.post("/v1/imports/trades/commit", json=body("with_execution_ids.csv"))
    preview = client.post("/v1/imports/trades/preview", json=body("with_execution_ids.csv")).json()

    assert first.status_code == 201
    batch = first.json()
    assert len(batch["imported_execution_ids"]) == 3
    assert batch["skipped_rows"] == [
        {"row_number": 4, "reason": "duplicate_exact"},
        {"row_number": 5, "reason": "invalid"},
    ]
    assert (again.status_code, again.json()) == (200, batch)
    assert preview["already_imported"] is True
    assert len(client.get("/v1/trades/imported").json()["executions"]) == 3
    assert b"12345678" not in (journal_dir / "journal.sqlite3").read_bytes()


def test_overlapping_statement_imports_only_new_fills(client: TestClient) -> None:
    client.post("/v1/imports/trades/commit", json=body("with_execution_ids.csv"))
    content = (
        "Executed At,Symbol,Side,Quantity,Price,Exec ID\n"
        "2026-10-09T10:15:30-04:00,SYNTH,buy,10,50.10,E-1001\n"
        "2026-10-09T15:00:00-04:00,SYNTH,sell,6,50.90,E-1005\n"
    )
    later = body("later.csv", content=content)

    preview = client.post("/v1/imports/trades/preview", json=later).json()
    batch = client.post("/v1/imports/trades/commit", json=later).json()

    assert [row["status"] for row in preview["rows"]] == ["invalid", "new"]
    assert "different details" in preview["rows"][0]["errors"][0]
    assert len(batch["imported_execution_ids"]) == 1


def test_possible_duplicates_need_a_decision_and_may_both_be_kept(client: TestClient) -> None:
    missing = client.post("/v1/imports/trades/commit", json=body("without_ids.csv"))
    kept = client.post(
        "/v1/imports/trades/commit", json=body("without_ids.csv", decisions={"2": "import"})
    )
    ledger = client.get("/v1/trades/imported").json()

    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "duplicate_review_required"
    assert kept.status_code == 201
    reviews = [item["duplicate_review"] for item in ledger["executions"]]
    assert {"decision": "imported_after_review", "related": ["row 1"]} in reviews
    [position] = ledger["positions"]
    assert (position["open_quantity"], position["realized_pnl"]) == ("5", "2.75")
    assert ledger["batches"][0]["file_name"] == "without_ids.csv"


def test_skipped_duplicate_is_recorded_as_skipped(client: TestClient) -> None:
    batch = client.post(
        "/v1/imports/trades/commit", json=body("without_ids.csv", decisions={"2": "skip"})
    ).json()

    assert batch["skipped_rows"] == [{"row_number": 2, "reason": "skipped_after_review"}]
    assert len(batch["imported_execution_ids"]) == 2


def test_imported_ledger_is_separate_from_manual_entries(client: TestClient) -> None:
    client.post("/v1/imports/trades/commit", json=body("with_execution_ids.csv"))

    assert client.get("/v1/trades/manual").json()["executions"] == []
    assert client.get("/v1/trades/imported").json()["ledger"] == "statement_imported"


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        (
            {"mapping": {"symbol": "Symbol", "side": "Action", "quantity": "Quantity"}},
            "invalid_import",
        ),
        ({"broker_label": "Account 98765432"}, "invalid_import"),
        ({"content": ""}, "invalid_import"),
        ({"delimiter": "|"}, "validation_error"),
        ({"account": "12345678"}, "validation_error"),
    ],
)
def test_bad_import_requests_are_rejected(
    client: TestClient, changes: dict[str, Any], code: str
) -> None:
    response = client.post(
        "/v1/imports/trades/preview", json=body("with_execution_ids.csv", **changes)
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == code


def test_imports_need_a_configured_data_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SIW_DATA_DIR", raising=False)
    with TestClient(create_app()) as unconfigured:
        response = unconfigured.post("/v1/imports/trades/preview", json=body("without_ids.csv"))

    assert response.status_code == 503
