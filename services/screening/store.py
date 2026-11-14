"""`SIW_DATA_DIR/screening.sqlite3` (ADR 0052): manual candidates and check results.

Check results are only added: running a check again stores a new row and the card shows
the latest one, so an earlier result stays readable. Nothing here is an order, a ledger
entry or market data.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from services.screening.candidates import utc_text

SCHEMA = """
CREATE TABLE IF NOT EXISTS manual_candidates (
    session_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    added_at_utc TEXT NOT NULL,
    PRIMARY KEY (session_date, symbol)
);
CREATE TABLE IF NOT EXISTS checks (
    check_id INTEGER PRIMARY KEY,
    session_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    check_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    cutoff_utc TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    document TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS checks_by_day ON checks (session_date, symbol, check_kind);
"""


class CheckNotFoundError(LookupError):
    pass


class ScreeningStore:
    def __init__(self, path: Path, clock: Callable[[], datetime]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.executescript(SCHEMA)
        self._clock = clock
        self._lock = threading.Lock()

    def close(self) -> None:
        self._db.close()

    def add_manual(self, session_date: date, symbol: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO manual_candidates VALUES (?, ?, ?)",
                (session_date.isoformat(), symbol, utc_text(self._clock())),
            )

    def manual(self, session_date: date) -> list[tuple[str, str]]:
        """(symbol, added_at_utc) in the order they were added."""
        with self._lock:
            rows = self._db.execute(
                "SELECT symbol, added_at_utc FROM manual_candidates WHERE session_date = ? "
                "ORDER BY added_at_utc, symbol",
                (session_date.isoformat(),),
            ).fetchall()
        return [(str(row["symbol"]), str(row["added_at_utc"])) for row in rows]

    def add_check(self, document: dict[str, Any]) -> dict[str, Any]:
        """Stores a check document and returns it with its `check_id`."""
        with self._lock:
            cursor = self._db.execute(
                "INSERT INTO checks (session_date, symbol, check_kind, status, cutoff_utc, "
                "created_at_utc, document) VALUES (?, ?, ?, ?, ?, ?, '{}')",
                (
                    document["session_date"],
                    document["symbol"],
                    document["check"],
                    document["status"],
                    document["cutoff_utc"],
                    document["created_at_utc"],
                ),
            )
            check_id = int(cursor.lastrowid or 0)
            stored = {**document, "check_id": check_id}
            self._db.execute(
                "UPDATE checks SET document = ? WHERE check_id = ?",
                (json.dumps(stored, ensure_ascii=False, sort_keys=True), check_id),
            )
        return stored

    def check(self, check_id: int) -> dict[str, Any]:
        with self._lock:
            row = self._db.execute(
                "SELECT document FROM checks WHERE check_id = ?", (check_id,)
            ).fetchone()
        if row is None:
            raise CheckNotFoundError(str(check_id))
        return dict(json.loads(row["document"]))

    def documents(self, session_date: date, check_kind: str) -> list[dict[str, Any]]:
        """Every stored check of one kind for a session date, oldest first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT document FROM checks WHERE session_date = ? AND check_kind = ? "
                "ORDER BY check_id",
                (session_date.isoformat(), check_kind),
            ).fetchall()
        return [dict(json.loads(row["document"])) for row in rows]

    def latest(self, session_date: date) -> dict[str, dict[str, dict[str, Any]]]:
        """The latest check of each kind, by symbol, for one session date."""
        with self._lock:
            rows = self._db.execute(
                "SELECT symbol, check_kind, check_id, status, created_at_utc FROM checks "
                "WHERE session_date = ? ORDER BY check_id",
                (session_date.isoformat(),),
            ).fetchall()
        found: dict[str, dict[str, dict[str, Any]]] = {}
        for row in rows:
            found.setdefault(str(row["symbol"]), {})[str(row["check_kind"])] = {
                "check_id": int(row["check_id"]),
                "status": str(row["status"]),
                "created_at_utc": str(row["created_at_utc"]),
            }
        return found
