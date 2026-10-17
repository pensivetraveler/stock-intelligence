from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from services.journal.csv_import import (
    ImportRequest,
    StatementImportError,
    classify,
    file_sha256,
    normalize_rows,
    parse_timestamp,
    read_rows,
    suggest_mapping,
    validate_broker_label,
    validate_mapping,
)

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)
SYMBOLS = {"SYNTH": "us:synth:xnas:2026", "AAPL": "us:aapl:xnas:2026"}
STATEMENTS = Path(__file__).resolve().parents[1] / "fixtures" / "statements"


def request(name: str, **changes: Any) -> ImportRequest:
    values: dict[str, Any] = {
        "file_name": name,
        "content": (STATEMENTS / name).read_text(encoding="utf-8"),
        "broker_label": "Example Broker",
        "timestamp_timezone": "America/New_York",
        "delimiter": ",",
        "mapping": None,
    }
    values.update(changes)
    return ImportRequest(**values)


def preview(
    req: ImportRequest, existing: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    columns, rows = read_rows(req)
    mapping = suggest_mapping(columns)
    validate_mapping(mapping, columns)
    normalized = normalize_rows(req, rows, mapping, SYMBOLS, file_sha256(req.content), NOW)
    return classify(normalized, existing or [])


def test_header_suggestions_map_date_time_and_ignore_unmapped_columns() -> None:
    columns, _rows = read_rows(request("with_execution_ids.csv"))

    assert suggest_mapping(columns) == {
        "executed_at": None,
        "date": "Trade Date",
        "time": "Time",
        "symbol": "Symbol",
        "side": "Action",
        "quantity": "Quantity",
        "price": "Price",
        "fees": "Commission",
        "broker_execution_id": "Exec ID",
    }


def test_statement_rows_normalize_signs_currency_and_et_times() -> None:
    rows = preview(request("with_execution_ids.csv"))

    first, sell, apple = (row["execution"] for row in rows[:3])
    assert (first["executed_at_utc"], first["time_precision"]) == ("2026-10-09T14:15:30Z", "exact")
    assert (first["price"], first["fees"]) == ("50.10", "1.00")
    assert (sell["side"], sell["quantity"], sell["fees"]) == ("sell", "4", "0.50")
    assert apple["price"] == "1250.00"
    assert "12345678" not in str(rows)


def test_duplicate_ids_in_one_file_and_unknown_symbols() -> None:
    rows = preview(request("with_execution_ids.csv"))

    assert [row["status"] for row in rows] == ["new", "new", "new", "duplicate_exact", "invalid"]
    assert rows[3]["matches"] == ["row 1"]
    assert "ZZZZ is not available" in rows[4]["errors"][0]


def test_stored_ids_are_exact_duplicates_and_changed_details_are_invalid() -> None:
    stored = [row["execution"] for row in preview(request("with_execution_ids.csv"))[:2]]
    changed = dict(stored[1], price="51.00")

    rows = preview(request("with_execution_ids.csv"), [stored[0], changed])

    assert rows[0]["status"] == "duplicate_exact"
    assert rows[1]["status"] == "invalid"
    assert "already imported with different details" in rows[1]["errors"][0]


def test_identical_fills_without_ids_are_flagged_not_dropped() -> None:
    rows = preview(request("without_ids.csv"))

    assert [row["status"] for row in rows] == ["new", "possible_duplicate", "new"]
    assert rows[1]["matches"] == ["row 1"]
    assert rows[0]["execution"]["time_precision"] == "minute"
    assert rows[2]["execution"]["executed_at_utc"] == "2026-10-09T18:30:00Z"


def test_stored_fill_without_id_flags_a_later_file_with_ids() -> None:
    stored = [preview(request("without_ids.csv"))[0]["execution"]]
    content = (
        "Executed At,Symbol,Side,Quantity,Price,Execution ID\n"
        "2026-10-09T11:00:20-04:00,SYNTH,buy,5,50,X-1\n"
    )

    [row] = preview(request("without_ids.csv", content=content), stored)

    assert row["status"] == "possible_duplicate"
    assert row["matches"] == [stored[0]["execution_id"]]


@pytest.mark.parametrize(
    ("raw", "zone", "expected"),
    [
        ("2026-10-09 09:30", "America/New_York", ("2026-10-09T13:30:00Z", "minute")),
        ("2026-02-02 09:30:05", "America/New_York", ("2026-02-02T14:30:05Z", "exact")),
        ("2026-10-09T09:30:00-04:00", "UTC", ("2026-10-09T13:30:00Z", "exact")),
        ("2026-10-09 13:30:00", "UTC", ("2026-10-09T13:30:00Z", "exact")),
        ("10/09/2026 2:30:15 PM", "America/New_York", ("2026-10-09T18:30:15Z", "exact")),
    ],
)
def test_timestamps(raw: str, zone: Any, expected: tuple[str, str]) -> None:
    assert parse_timestamp(raw, zone, NOW) == expected


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("2026-03-08 02:30", "does not exist"),
        ("2026-11-01 01:30", "ambiguous"),
        ("2026-10-09 09:30:00.250", "fractional seconds"),
        ("2026-10-13 09:30", "in the future"),
        ("2026-10-09", "no time of day"),
        ("yesterday", "no time of day"),
    ],
)
def test_bad_timestamps_are_rejected(raw: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_timestamp(raw, "America/New_York", NOW)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("", "empty"),
        ("Symbol,Symbol\nSYNTH,SYNTH\n", "exactly once"),
        ("Symbol,Side\nSYNTH,buy,extra\n", "more cells"),
        ("a\n" + "x" * 1_000_001, "larger than"),
    ],
)
def test_unreadable_files_are_rejected(content: str, message: str) -> None:
    with pytest.raises(StatementImportError, match=message):
        read_rows(request("without_ids.csv", content=content))


def test_mapping_must_cover_required_fields() -> None:
    with pytest.raises(StatementImportError, match="map a column to price"):
        validate_mapping(
            {"symbol": "Symbol", "side": "Side", "quantity": "Qty"}, ["Symbol", "Side", "Qty"]
        )
    with pytest.raises(StatementImportError, match="does not have"):
        validate_mapping({"symbol": "Ticker"}, ["Symbol"])


def test_broker_label_cannot_be_an_account_number() -> None:
    assert validate_broker_label("  Example Broker ") == "Example Broker"
    with pytest.raises(StatementImportError, match="account number"):
        validate_broker_label("Acct 12345678")
