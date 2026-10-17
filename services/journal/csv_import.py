"""Broker-statement CSV import: column mapping, normalization and duplicate review.

Only mapped columns are read. The original file is never stored: a batch keeps its
SHA-256, name and row numbers as provenance, so unmapped columns such as account
numbers never reach the journal.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, time, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from services.journal.ledger import ExchangeTimeError, exchange_local_to_utc

MAX_FILE_BYTES = 1_000_000
MAX_ROWS = 5_000
MAPPING_FIELDS = (
    "executed_at",
    "date",
    "time",
    "symbol",
    "side",
    "quantity",
    "price",
    "fees",
    "broker_execution_id",
)
Timezone = Literal["America/New_York", "UTC"]

# Header names (lower case) suggested for each field; the user can change any of them.
HEADER_HINTS: dict[str, tuple[str, ...]] = {
    "executed_at": (
        "executed at",
        "execution time",
        "trade time",
        "date/time",
        "datetime",
        "timestamp",
        "filled at",
        "fill time",
    ),
    "date": ("trade date", "date"),
    "time": ("time", "exec time"),
    "symbol": ("symbol", "ticker"),
    "side": ("side", "action", "buy/sell", "b/s"),
    "quantity": ("quantity", "qty", "shares", "filled qty"),
    "price": ("price", "fill price", "execution price", "avg price"),
    "fees": ("fees", "fee", "commission", "commissions", "comm/fee"),
    "broker_execution_id": ("execution id", "exec id", "fill id", "trade id", "transaction id"),
}
BUY_WORDS = {"buy", "b", "bot", "bought", "buy to cover", "btc", "buy to open", "bto"}
SELL_WORDS = {
    "sell",
    "s",
    "sld",
    "sold",
    "sell short",
    "ss",
    "sell to open",
    "sto",
    "sell to close",
    "stc",
}
LIKELY_ACCOUNT_NUMBER = re.compile(r"\d{6,}")


class StatementImportError(ValueError):
    """The file or request cannot be previewed at all (not a single bad row)."""


@dataclass(frozen=True)
class ImportRequest:
    file_name: str
    content: str
    broker_label: str
    timestamp_timezone: Timezone
    delimiter: str
    mapping: dict[str, str | None] | None


def file_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def batch_id_for(sha256: str) -> str:
    return f"batch-{sha256[:24]}"


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def suggest_mapping(columns: list[str]) -> dict[str, str | None]:
    lowered = {column.strip().lower(): column for column in columns}
    mapping: dict[str, str | None] = {}
    taken: set[str] = set()
    for field in MAPPING_FIELDS:
        mapping[field] = None
        for hint in HEADER_HINTS[field]:
            column = lowered.get(hint)
            if column is not None and column not in taken:
                mapping[field] = column
                taken.add(column)
                break
    if mapping["executed_at"] is not None:
        mapping["date"] = mapping["time"] = None
    return mapping


def read_rows(request: ImportRequest) -> tuple[list[str], list[dict[str, str]]]:
    if not request.content.strip():
        raise StatementImportError("the file is empty")
    if len(request.content.encode("utf-8")) > MAX_FILE_BYTES:
        raise StatementImportError(
            f"files larger than {MAX_FILE_BYTES // 1000} KB are not supported"
        )
    text = request.content.removeprefix("﻿")
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=request.delimiter)
    columns = [column for column in (reader.fieldnames or []) if column is not None]
    if not columns or len(set(columns)) != len(columns):
        raise StatementImportError("the header row must name every column exactly once")
    rows: list[dict[str, str]] = []
    for row in reader:
        if len(rows) == MAX_ROWS:
            raise StatementImportError(f"files with more than {MAX_ROWS} rows are not supported")
        if None in row:
            raise StatementImportError(f"row {len(rows) + 1} has more cells than the header")
        rows.append({key: (value or "").strip() for key, value in row.items()})
    return columns, rows


def validate_mapping(mapping: dict[str, str | None], columns: list[str]) -> None:
    unknown = [field for field in mapping if field not in MAPPING_FIELDS]
    if unknown:
        raise StatementImportError(f"unknown mapping field: {unknown[0]}")
    for field, column in mapping.items():
        if column is not None and column not in columns:
            raise StatementImportError(
                f"{field} is mapped to a column the file does not have: {column}"
            )
    for field in ("symbol", "side", "quantity", "price"):
        if mapping.get(field) is None:
            raise StatementImportError(f"map a column to {field}")
    if mapping.get("executed_at") is None and (
        mapping.get("date") is None or mapping.get("time") is None
    ):
        raise StatementImportError("map either an execution timestamp, or both a date and a time")


def validate_broker_label(label: str) -> str:
    cleaned = label.strip()
    if not 1 <= len(cleaned) <= 64:
        raise StatementImportError("the broker label must be 1-64 characters")
    if LIKELY_ACCOUNT_NUMBER.search(cleaned):
        raise StatementImportError(
            "the broker label looks like an account number; use a name instead"
        )
    return cleaned


def _decimal(raw: str, field: str) -> Decimal:
    cleaned = raw.replace("$", "").replace(",", "").strip()
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = f"-{cleaned[1:-1]}"
    try:
        value = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"{field} is not a number: {raw!r}") from exc
    if not value.is_finite():
        raise ValueError(f"{field} is not a number: {raw!r}")
    return value


def _text(value: Decimal) -> str:
    return format(value, "f")


TIME_WITH_SECONDS = re.compile(r"\d{1,2}:\d{2}:\d{2}")
NAIVE_FORMATS = (
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y %I:%M:%S %p",
    "%m/%d/%Y %I:%M %p",
)


def parse_timestamp(
    raw: str, zone: Timezone, now: datetime
) -> tuple[str, Literal["exact", "minute"]]:
    """UTC text and precision for a statement timestamp.

    An explicit offset wins. A timestamp without one is read in the chosen zone; in
    ET the skipped and repeated DST hours are rejected rather than guessed.
    """
    text = raw.strip()
    if not text:
        raise ValueError("the execution time is empty")
    if re.search(r":\d{2}\.\d", text):
        raise ValueError("fractional seconds are not supported; export whole seconds")
    if not re.search(r"\d{1,2}:\d{2}", text):
        raise ValueError(f"the timestamp has no time of day: {raw!r}")
    precision: Literal["exact", "minute"] = "exact" if TIME_WITH_SECONDS.search(text) else "minute"
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        for pattern in NAIVE_FORMATS:
            try:
                parsed = datetime.strptime(text.upper(), pattern)
                break
            except ValueError:
                continue
    if parsed is None:
        raise ValueError(f"unrecognized date/time: {raw!r}")
    if parsed.tzinfo is not None:
        utc = parsed.astimezone(timezone.utc)
    elif zone == "UTC":
        utc = parsed.replace(tzinfo=timezone.utc)
    else:
        try:
            utc = exchange_local_to_utc(
                parsed.date(), time(parsed.hour, parsed.minute, parsed.second)
            )
        except ExchangeTimeError as exc:
            raise ValueError(str(exc)) from exc
    if utc > now:
        raise ValueError("the execution time is in the future")
    return _utc_text(utc), precision


def _side(raw: str) -> Literal["buy", "sell"]:
    word = " ".join(raw.lower().split())
    if word in BUY_WORDS:
        return "buy"
    if word in SELL_WORDS:
        return "sell"
    raise ValueError(f"unrecognized side: {raw!r}")


@dataclass
class _Row:
    row_number: int
    execution: dict[str, Any] | None
    errors: list[str]


def normalize_rows(
    request: ImportRequest,
    rows: list[dict[str, str]],
    mapping: dict[str, str | None],
    symbols: dict[str, str],
    sha256: str,
    now: datetime,
) -> list[_Row]:
    """Turn each statement row into an `imported_execution`, or record why it cannot be."""
    batch_id = batch_id_for(sha256)
    results: list[_Row] = []

    def cell(row: dict[str, str], field: str) -> str:
        column = mapping.get(field)
        return row.get(column, "") if column is not None else ""

    for index, row in enumerate(rows, start=1):
        errors: list[str] = []
        execution: dict[str, Any] | None = None
        try:
            side = _side(cell(row, "side"))
            quantity = _decimal(cell(row, "quantity"), "quantity")
            if quantity < 0 and side == "buy":
                raise ValueError("a buy cannot have a negative quantity")
            quantity = abs(quantity)
            price = _decimal(cell(row, "price"), "price")
            # Statements report fees as positive charges or negative cash amounts; both are costs.
            fees = abs(_decimal(cell(row, "fees"), "fees")) if cell(row, "fees") else Decimal(0)
            if quantity <= 0 or price <= 0:
                raise ValueError("quantity and price must be positive")
            raw_time = (
                cell(row, "executed_at")
                if mapping.get("executed_at")
                else f"{cell(row, 'date')} {cell(row, 'time')}"
            )
            executed_at, precision = parse_timestamp(raw_time, request.timestamp_timezone, now)
            symbol = cell(row, "symbol").upper()
            instrument_id = symbols.get(symbol)
            if instrument_id is None:
                raise ValueError(f"symbol {symbol or '(empty)'} is not available from the provider")
            native_id = cell(row, "broker_execution_id") or None
            if native_id is not None and len(native_id) > 128:
                raise ValueError("the broker execution ID is longer than 128 characters")
            execution = {
                "kind": "imported_execution",
                "schema_version": "0.1",
                "execution_id": "imp-"
                + hashlib.sha256(f"{sha256}:{index}".encode()).hexdigest()[:32],
                "instrument_id": instrument_id,
                "side": side,
                "quantity": _text(quantity),
                "price": _text(price),
                "fees": _text(fees),
                "currency": "USD",
                "executed_at_utc": executed_at,
                "time_precision": precision,
                "broker_label": request.broker_label,
                "broker_execution_id": native_id,
                "source": {"batch_id": batch_id, "file_sha256": sha256, "row_number": index},
                "verification_status": "statement_imported",
                "duplicate_review": {"decision": "none", "related": []},
            }
        except ValueError as exc:
            errors.append(str(exc))
        results.append(_Row(index, execution, errors))
    return results


def _same_fill(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Same instrument, side, size and price at the same time, to the coarser precision."""
    if (a["instrument_id"], a["side"]) != (b["instrument_id"], b["side"]):
        return False
    if Decimal(a["quantity"]) != Decimal(b["quantity"]) or Decimal(a["price"]) != Decimal(
        b["price"]
    ):
        return False
    first = datetime.fromisoformat(a["executed_at_utc"].replace("Z", "+00:00"))
    second = datetime.fromisoformat(b["executed_at_utc"].replace("Z", "+00:00"))
    if a["time_precision"] == "minute" or b["time_precision"] == "minute":
        return first.replace(second=0) == second.replace(second=0)
    return first == second


def _same_record(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return _same_fill(a, b) and Decimal(a["fees"]) == Decimal(b["fees"])


def classify(normalized: list[_Row], existing: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Preview rows with their duplicate status.

    - `duplicate_exact`: the broker execution ID was already imported (or appears earlier
      in this file) with the same details. It is skipped automatically.
    - `possible_duplicate`: no matching broker ID, but the same fill as a stored or
      earlier row. The user decides; identical legitimate fills can coexist.
    - A broker ID reused with different details is invalid, never merged.
    """
    stored = list(existing)
    by_native = {
        (item["broker_label"], item["broker_execution_id"]): item
        for item in stored
        if item["broker_execution_id"] is not None
    }
    seen_native: dict[tuple[str, str], _Row] = {}
    earlier: list[_Row] = []
    preview: list[dict[str, Any]] = []
    for row in normalized:
        status = "invalid"
        matches: list[str] = []
        errors = list(row.errors)
        execution = row.execution
        if execution is not None:
            native = execution["broker_execution_id"]
            key = (execution["broker_label"], native)
            status = "new"
            if native is not None and key in by_native:
                if _same_record(by_native[key], execution):
                    status, matches = "duplicate_exact", [by_native[key]["execution_id"]]
                else:
                    errors.append(
                        f"broker execution ID {native} was already imported with different details"
                    )
            elif native is not None and key in seen_native:
                first = seen_native[key]
                assert first.execution is not None
                if _same_record(first.execution, execution):
                    status, matches = "duplicate_exact", [f"row {first.row_number}"]
                else:
                    errors.append(
                        f"broker execution ID {native} appears in row "
                        f"{first.row_number} with different details"
                    )
            if errors:
                status, execution = "invalid", None
            elif status == "new":
                matches = [item["execution_id"] for item in stored if _same_fill(item, execution)]
                matches += [
                    f"row {other.row_number}"
                    for other in earlier
                    if other.execution is not None and _same_fill(other.execution, execution)
                ]
                if matches:
                    status = "possible_duplicate"
                if native is not None:
                    seen_native[key] = row
                earlier.append(row)
        preview.append(
            {
                "row_number": row.row_number,
                "status": status,
                "execution": execution,
                "errors": errors,
                "matches": matches,
            }
        )
    return preview


def summarize(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"new": 0, "duplicate_exact": 0, "possible_duplicate": 0, "invalid": 0}
    for row in rows:
        counts[row["status"]] += 1
    return counts


__all__ = [
    "StatementImportError",
    "ImportRequest",
    "MAPPING_FIELDS",
    "batch_id_for",
    "classify",
    "file_sha256",
    "normalize_rows",
    "parse_timestamp",
    "read_rows",
    "suggest_mapping",
    "summarize",
    "validate_broker_label",
    "validate_mapping",
]
