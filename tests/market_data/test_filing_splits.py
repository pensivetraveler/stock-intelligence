"""Filing splits from the shared data space (ADR 0046). No test reaches the network."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from services.api.main import create_app
from services.market_data.alpaca import AlpacaMarketDataProvider
from services.market_data.fake import FakeMarketDataProvider
from services.market_data.filing_splits import (
    FilingSplit,
    FilingSplits,
    FilingSplitsUnavailable,
    ProviderSplit,
    compare_splits,
    export_disagreements,
    read_filing_splits,
    split_events,
    splits_path,
)

CIK = 1896212


def make_database(root: Path, *, version: int = 1, rows: bool = True, issuer: bool = True) -> Path:
    path = splits_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE issuers (cik INTEGER PRIMARY KEY, ticker TEXT, checked_at TEXT, as_of TEXT,
            filings_from TEXT, filings_read INTEGER, engine TEXT);
        CREATE TABLE tickers (ticker TEXT PRIMARY KEY, cik INTEGER, last_seen TEXT);
        CREATE TABLE splits (cik INTEGER, effective TEXT, numerator INTEGER, denominator INTEGER,
            status TEXT, sources TEXT, first_seen TEXT, updated_at TEXT);
        """
    )
    connection.execute(f"PRAGMA user_version = {version}")
    connection.execute("INSERT INTO tickers VALUES ('CDT', ?, 'x')", (CIK,))
    if issuer:
        connection.execute(
            "INSERT INTO issuers VALUES (?, 'CDT', 'x', '2026-11-01', '2021-11-02', 9, 'e')", (CIK,)
        )
    if rows:
        connection.executemany(
            "INSERT INTO splits VALUES (?, ?, ?, ?, ?, '[]', 'x', 'x')",
            [
                (CIK, "2025-05-19", 1, 15, "confirmed"),
                (CIK, "2025-01-24", 1, 100, "conflict"),
            ],
        )
    connection.commit()
    connection.close()
    return path


def filing(*rows: FilingSplit) -> FilingSplits:
    return FilingSplits("found", date(2021, 11, 2), date(2026, 11, 1), rows)


def confirmed(on: date, numerator: int, denominator: int) -> FilingSplit:
    return FilingSplit(on, numerator, denominator, "confirmed")


def test_not_checked_stays_apart_from_none_found(tmp_path: Path) -> None:
    path = make_database(tmp_path, rows=False, issuer=False)
    assert read_filing_splits(path, "ZZZ").status == "not_checked"
    assert read_filing_splits(path, "CDT").status == "not_checked"

    other = make_database(tmp_path / "read", rows=False)
    assert read_filing_splits(other, "cdt").status == "none_found"


def test_reads_rows_with_the_period_read(tmp_path: Path) -> None:
    found = read_filing_splits(make_database(tmp_path), "CDT")
    assert found.status == "found"
    assert found.filings_from == date(2021, 11, 2)
    assert found.as_of == date(2026, 11, 1)
    assert [(row.effective, row.status) for row in found.rows] == [
        (date(2025, 1, 24), "conflict"),
        (date(2025, 5, 19), "confirmed"),
    ]


def test_refuses_a_version_it_does_not_know(tmp_path: Path) -> None:
    path = make_database(tmp_path, version=2)
    with pytest.raises(FilingSplitsUnavailable, match="unknown version 2"):
        read_filing_splits(path, "CDT")


def test_a_missing_file_is_unavailable_and_is_not_created(tmp_path: Path) -> None:
    path = splits_path(tmp_path)
    with pytest.raises(FilingSplitsUnavailable):
        read_filing_splits(path, "CDT")
    assert not path.exists()


def test_reading_leaves_the_file_untouched(tmp_path: Path) -> None:
    path = make_database(tmp_path)
    before = path.read_bytes()
    read_filing_splits(path, "CDT")
    assert path.read_bytes() == before


def test_the_day_after_the_effective_date_is_the_same_split() -> None:
    action = ProviderSplit(date(2025, 5, 20), Fraction(1, 15))
    assert compare_splits(filing(confirmed(date(2025, 5, 19), 1, 15)), [action]) == []


def test_a_different_ratio_near_the_date_differs() -> None:
    action = ProviderSplit(date(2025, 5, 20), Fraction(1, 10))
    found = compare_splits(filing(confirmed(date(2025, 5, 19), 1, 15)), [action])
    assert [item.kind for item in found] == ["ratio_differs"]


def test_each_side_alone_differs() -> None:
    action = ProviderSplit(date(2024, 3, 1), Fraction(1, 5))
    found = compare_splits(filing(confirmed(date(2025, 5, 19), 1, 15)), [action])
    assert [item.kind for item in found] == ["provider_only", "filing_only"]


def test_an_action_matching_an_unconfirmed_reading_is_named() -> None:
    row = FilingSplit(date(2025, 1, 24), 1, 100, "conflict")
    action = ProviderSplit(date(2025, 1, 27), Fraction(1, 100))
    found = compare_splits(filing(row), [action])
    assert [(item.kind, item.filing) for item in found] == [("filing_unconfirmed", row)]


def test_actions_outside_the_period_read_say_nothing() -> None:
    before = ProviderSplit(date(2020, 1, 2), Fraction(1, 5))
    assert compare_splits(filing(), [before]) == []


def test_export_writes_the_list_and_the_contract(tmp_path: Path) -> None:
    make_database(tmp_path)

    def actions(ticker: str, since: date, until: date) -> list[ProviderSplit]:
        assert (ticker, since, until) == ("CDT", date(2021, 11, 2), date(2026, 11, 1))
        return [ProviderSplit(date(2025, 1, 27), Fraction(1, 100))]

    output = export_disagreements(
        tmp_path,
        actions,
        "alpaca-corporate-actions",
        now=lambda: datetime(2026, 11, 2, tzinfo=timezone.utc),
    )
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["format"] == 1
    assert document["tickers_compared"] == ["CDT"]
    assert {item["kind"] for item in document["differences"]} == {
        "filing_only",
        "filing_unconfirmed",
    }
    contract = json.loads((tmp_path / "CONTRACT.json").read_text())
    assert contract["layout_version"] == 1
    assert {"dilution/", "sec/", "sec.lock"} <= contract["datasets"].keys()
    assert not list(tmp_path.rglob("*.tmp"))


class _Boats:
    def __init__(self) -> None:
        self.calls = 0

    def split_actions(self, symbol: str, since: date, until: date) -> list[ProviderSplit]:
        self.calls += 1
        return [ProviderSplit(date(2025, 5, 20), Fraction(1, 15))]


def api_client(boats: Any = None) -> TestClient:
    return TestClient(
        create_app(FakeMarketDataProvider.from_path(), overnight_source=lambda: boats)
    )


def test_api_without_a_shared_folder_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STOCK_SHARED_DATA", raising=False)
    response = api_client().get(
        "/v1/market/filing-splits", params={"instrument_id": "us:cdt:xnas:2026"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"


def test_api_compares_and_asks_alpaca_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_database(tmp_path)
    monkeypatch.setenv("STOCK_SHARED_DATA", str(tmp_path))
    boats = _Boats()
    client = api_client(boats)
    for _ in range(2):
        body = client.get(
            "/v1/market/filing-splits", params={"instrument_id": "us:cdt:xnas:2026"}
        ).json()
    assert boats.calls == 1
    assert body["status"] == "found"
    assert body["compared_with"] == "alpaca-corporate-actions"
    assert body["differences"] == []
    assert [row["status"] for row in body["rows"]] == ["conflict", "confirmed"]


def test_api_says_when_it_could_not_compare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_database(tmp_path)
    monkeypatch.setenv("STOCK_SHARED_DATA", str(tmp_path))
    body = (
        api_client(None)
        .get("/v1/market/filing-splits", params={"instrument_id": "us:cdt:xnas:2026"})
        .json()
    )
    assert body["status"] == "found"
    assert body["compared_with"] is None


def test_alpaca_split_actions_read_both_kinds() -> None:
    # The shape follows a live reply of 2026-11-02.
    payload = {
        "corporate_actions": {
            "reverse_splits": [{"ex_date": "2023-05-10", "new_rate": 1, "old_rate": 6.25}],
            "forward_splits": [{"ex_date": "2024-06-10", "new_rate": 10, "old_rate": 1}],
        },
        "next_page_token": None,
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    provider = AlpacaMarketDataProvider.__new__(AlpacaMarketDataProvider)
    provider._client = httpx.Client(transport=transport, base_url="http://test")
    found = provider.split_actions("TNXP", date(2022, 1, 1), date(2026, 1, 1))
    assert found == [
        ProviderSplit(date(2023, 5, 10), Fraction(4, 25)),
        ProviderSplit(date(2024, 6, 10), Fraction(10)),
    ]


def test_events_pair_both_sides_and_name_what_was_not_compared() -> None:
    row = confirmed(date(2025, 5, 19), 1, 15)
    same = ProviderSplit(date(2025, 5, 20), Fraction(1, 15))
    before = ProviderSplit(date(2020, 1, 2), Fraction(1, 5))
    events = split_events(filing(row), [same, before])
    assert [(event.kind, event.filing, event.provider) for event in events] == [
        ("not_compared", None, before),
        ("same", row, same),
    ]


def test_without_filing_readings_every_provider_split_is_an_event() -> None:
    action = ProviderSplit(date(2024, 6, 10), Fraction(10))
    assert [event.kind for event in split_events(None, [action])] == ["not_compared"]


def test_api_lists_alpaca_splits_without_a_shared_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STOCK_SHARED_DATA", raising=False)
    body = (
        api_client(_Boats())
        .get("/v1/market/filing-splits", params={"instrument_id": "us:nvda:xnas:2026"})
        .json()
    )
    assert body["status"] == "unavailable"
    assert body["compared_with"] == "alpaca-corporate-actions"
    assert [(event["kind"], event["provider"]["on"]) for event in body["events"]] == [
        ("not_compared", "2025-05-20")
    ]
    assert body["differences"] == []
