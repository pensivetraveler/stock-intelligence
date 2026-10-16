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
