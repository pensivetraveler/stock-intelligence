"""Append-only local store for manual executions and hypothetical scenarios."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, cast

from packages.contracts.validator import validate_document
from services.market_data.storage import MigrationRunner

MIGRATIONS_DIR = Path(__file__).with_name("migrations")


class JournalError(RuntimeError):
    """Base error for the local trade journal."""


class JournalConflictError(JournalError):
    """An ID was reused with different content; the stored record is never replaced."""


def _canonical(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def request_hash(request: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(request).encode()).hexdigest()


class JournalStore:
    """Separate SQLite file from market data: journal records are user data."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database_path = self.root / "journal.sqlite3"
        MigrationRunner(self.database_path, MIGRATIONS_DIR).apply()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 5000")
            with connection:
                yield connection

    def record_manual(
        self, document: dict[str, Any], request_sha256: str
    ) -> tuple[dict[str, Any], bool]:
        """Store a manual execution; a retry with the same request returns the original.

        Returns the stored record and whether this call created it.
        """
        validate_document(document)
        return self._insert_once(
            "manual_executions",
            "execution_id",
            document["execution_id"],
            {
                "execution_id": document["execution_id"],
                "instrument_id": document["instrument_id"],
                "executed_at_utc": document["executed_at_utc"],
                "asserted_at_utc": document["asserted_at_utc"],
                "request_sha256": request_sha256,
                "document_json": _canonical(document),
            },
        )

    def record_scenario(
        self, document: dict[str, Any], request_sha256: str
    ) -> tuple[dict[str, Any], bool]:
        validate_document(document)
        return self._insert_once(
            "scenarios",
            "scenario_id",
            document["scenario_id"],
            {
                "scenario_id": document["scenario_id"],
                "version": document["version"],
                "instrument_id": document["instrument_id"],
                "created_at_utc": document["created_at_utc"],
                "request_sha256": request_sha256,
                "document_json": _canonical(document),
            },
        )

    def _insert_once(
        self, table: str, id_column: str, record_id: object, row: dict[str, object]
    ) -> tuple[dict[str, Any], bool]:
        columns = ", ".join(row)
        placeholders = ", ".join("?" for _ in row)
        try:
            with self._connect() as connection:
                connection.execute(
                    f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
                    tuple(row.values()),
                )
            return cast(dict[str, Any], json.loads(cast(str, row["document_json"]))), True
        except sqlite3.IntegrityError:
            pass  # The ID exists (perhaps from a concurrent retry); compare with it.
        with self._connect() as connection:
            existing = connection.execute(
                f"SELECT request_sha256, document_json FROM {table} WHERE {id_column} = ?",
                (record_id,),
            ).fetchone()
        if existing is None:
            raise JournalError("record could not be stored")
        if existing["request_sha256"] != row["request_sha256"]:
            raise JournalConflictError(f"{id_column} was already used for a different record")
        return cast(dict[str, Any], json.loads(cast(str, existing["document_json"]))), False

    def commit_import(
        self, batch: dict[str, Any], executions: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], bool]:
        """Store a statement batch and its rows atomically, once per file.

        Returns the stored batch and whether this call created it. A file already
        committed returns its original batch and imports nothing.
        """
        validate_document(batch)
        for execution in executions:
            validate_document(execution)
        existing = self.batch_for_file(batch["file_sha256"])
        if existing is not None:
            return existing, False
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO import_batches (batch_id, file_sha256, committed_at_utc, "
                    "document_json) VALUES (?, ?, ?, ?)",
                    (
                        batch["batch_id"],
                        batch["file_sha256"],
                        batch["committed_at_utc"],
                        _canonical(batch),
                    ),
                )
                connection.executemany(
                    "INSERT INTO imported_executions (execution_id, batch_id, instrument_id, "
                    "broker_label, broker_execution_id, executed_at_utc, document_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            item["execution_id"],
                            batch["batch_id"],
                            item["instrument_id"],
                            item["broker_label"],
                            item["broker_execution_id"],
                            item["executed_at_utc"],
                            _canonical(item),
                        )
                        for item in executions
                    ],
                )
        except sqlite3.IntegrityError as exc:
            # A concurrent commit of the same file won; anything else is a real conflict.
            existing = self.batch_for_file(batch["file_sha256"])
            if existing is not None:
                return existing, False
            raise JournalConflictError(
                "a broker execution ID in this file is already stored"
            ) from exc
        return batch, True

    def record_broker_execution(self, document: dict[str, Any]) -> str:
        """Store an order execution reported by a broker API, once.

        Returns "new" when stored now, "same" when the identical report is already stored
        and "conflict" when the stored report for that order differs (it is kept as is).
        When it was synced does not count as a difference.
        """
        validate_document(document)
        content = {key: value for key, value in document.items() if key != "synced_at_utc"}
        digest = request_hash(content)
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO broker_order_executions (execution_id, broker_id, "
                    "broker_account_ref, broker_order_id, instrument_id, executed_at_utc, "
                    "synced_at_utc, document_sha256, document_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        document["execution_id"],
                        document["broker_id"],
                        document["broker_account_ref"],
                        document["broker_order_id"],
                        document["instrument_id"],
                        document["executed_at_utc"],
                        document["synced_at_utc"],
                        digest,
                        _canonical(document),
                    ),
                )
            return "new"
        except sqlite3.IntegrityError:
            pass
        with self._connect() as connection:
            row = connection.execute(
                "SELECT document_sha256 FROM broker_order_executions WHERE broker_id = ? "
                "AND broker_account_ref = ? AND broker_order_id = ?",
                (
                    document["broker_id"],
                    document["broker_account_ref"],
                    document["broker_order_id"],
                ),
            ).fetchone()
        if row is None:
            raise JournalConflictError("execution_id was already used for a different order")
        return "same" if row["document_sha256"] == digest else "conflict"

    def record_broker_fee_line(self, document: dict[str, Any]) -> str:
        """Store a settlement line's fees once: "new", "same" or "conflict"."""
        validate_document(document)
        content = {key: value for key, value in document.items() if key != "synced_at_utc"}
        digest = request_hash(content)
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO broker_fee_lines (line_id, broker_id, broker_account_ref, "
                    "instrument_id, settlement_date, synced_at_utc, document_sha256, "
                    "document_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        document["line_id"],
                        document["broker_id"],
                        document["broker_account_ref"],
                        document["instrument_id"],
                        document["settlement_date"],
                        document["synced_at_utc"],
                        digest,
                        _canonical(document),
                    ),
                )
            return "new"
        except sqlite3.IntegrityError:
            pass
        with self._connect() as connection:
            row = connection.execute(
                "SELECT document_sha256 FROM broker_fee_lines WHERE line_id = ?",
                (document["line_id"],),
            ).fetchone()
        return "same" if row is not None and row["document_sha256"] == digest else "conflict"

    def broker_fee_lines(self, instrument_id: str | None = None) -> list[dict[str, Any]]:
        return self._documents("broker_fee_lines", "settlement_date, line_id", instrument_id)

    def broker_fee_amounts(self, instrument_id: str | None = None) -> list[dict[str, Any]]:
        """Only the fields positions need from each fee line (`account_positions`), read
        without parsing whole documents. Amounts stay decimal text."""
        statement = (
            "SELECT broker_account_ref, instrument_id, "
            "json_extract(document_json, '$.commission'), json_extract(document_json, '$.tax') "
            "FROM broker_fee_lines"
        )
        parameters: tuple[object, ...] = ()
        if instrument_id is not None:
            statement += " WHERE instrument_id = ?"
            parameters = (instrument_id,)
        with self._connect() as connection:
            rows = connection.execute(statement, parameters).fetchall()
        return [
            {
                "broker_account_ref": row[0],
                "instrument_id": row[1],
                "commission": row[2],
                "tax": row[3],
            }
            for row in rows
        ]

    def broker_executions(self, instrument_id: str | None = None) -> list[dict[str, Any]]:
        return self._documents(
            "broker_order_executions", "executed_at_utc, execution_id", instrument_id
        )

    def broker_position_fields(self, instrument_id: str | None = None) -> list[dict[str, Any]]:
        """Only the fields broker positions need (`account_positions`), in ledger order,
        read without parsing whole documents. Amounts stay decimal text."""
        statement = (
            "SELECT execution_id, broker_account_ref, instrument_id, executed_at_utc, "
            "json_extract(document_json, '$.side'), json_extract(document_json, '$.quantity'), "
            "json_extract(document_json, '$.price'), json_extract(document_json, '$.fees') "
            "FROM broker_order_executions"
        )
        parameters: tuple[object, ...] = ()
        if instrument_id is not None:
            statement += " WHERE instrument_id = ?"
            parameters = (instrument_id,)
        with self._connect() as connection:
            rows = connection.execute(
                f"{statement} ORDER BY executed_at_utc, execution_id", parameters
            ).fetchall()
        keys = (
            "execution_id",
            "broker_account_ref",
            "instrument_id",
            "executed_at_utc",
            "side",
            "quantity",
            "price",
            "fees",
        )
        return [dict(zip(keys, row, strict=True)) for row in rows]

    def latest_broker_executions(
        self, limit: int, instrument_id: str | None = None
    ) -> tuple[list[dict[str, Any]], int]:
        """The newest `limit` executions in ledger order, and how many there are."""
        where, parameters = "", cast(tuple[object, ...], ())
        if instrument_id is not None:
            where, parameters = " WHERE instrument_id = ?", (instrument_id,)
        with self._connect() as connection:
            total = connection.execute(
                f"SELECT count(*) FROM broker_order_executions{where}", parameters
            ).fetchone()[0]
            rows = connection.execute(
                f"SELECT document_json FROM broker_order_executions{where} "
                "ORDER BY executed_at_utc DESC, execution_id DESC LIMIT ?",
                (*parameters, limit),
            ).fetchall()
        documents = [cast(dict[str, Any], json.loads(cast(str, row[0]))) for row in rows]
        return documents[::-1], cast(int, total)

    def batch_for_file(self, sha256: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT document_json FROM import_batches WHERE file_sha256 = ?", (sha256,)
            ).fetchone()
        return None if row is None else cast(dict[str, Any], json.loads(cast(str, row[0])))

    def import_batches(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT document_json FROM import_batches ORDER BY committed_at_utc, batch_id"
            ).fetchall()
        return [cast(dict[str, Any], json.loads(cast(str, row[0]))) for row in rows]

    def imported_executions(self, instrument_id: str | None = None) -> list[dict[str, Any]]:
        return self._documents(
            "imported_executions", "executed_at_utc, batch_id, execution_id", instrument_id
        )

    def manual_executions(self, instrument_id: str | None = None) -> list[dict[str, Any]]:
        return self._documents("manual_executions", "asserted_at_utc, execution_id", instrument_id)

    def scenarios(self, instrument_id: str | None = None) -> list[dict[str, Any]]:
        return self._documents("scenarios", "created_at_utc, scenario_id, version", instrument_id)

    def _documents(self, table: str, order: str, instrument_id: str | None) -> list[dict[str, Any]]:
        statement = f"SELECT document_json FROM {table}"
        parameters: tuple[object, ...] = ()
        if instrument_id is not None:
            statement += " WHERE instrument_id = ?"
            parameters = (instrument_id,)
        with self._connect() as connection:
            rows = connection.execute(f"{statement} ORDER BY {order}", parameters).fetchall()
        return [cast(dict[str, Any], json.loads(cast(str, row[0]))) for row in rows]
