"""Custom Python indicators in `SIW_DATA_DIR/indicators.sqlite3` (ADR 0023).

Versions are immutable: each changed save adds one, and charts pin `py:<id>@<version>`.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS indicators (
    indicator_id TEXT PRIMARY KEY,
    created_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS indicator_versions (
    indicator_id TEXT NOT NULL REFERENCES indicators(indicator_id),
    version INTEGER NOT NULL,
    code TEXT NOT NULL,
    code_sha256 TEXT NOT NULL,
    meta TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    PRIMARY KEY (indicator_id, version)
);
"""


class IndicatorNotFoundError(LookupError):
    pass


def _utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class CustomIndicatorStore:
    def __init__(self, path: Path, clock: Callable[[], datetime]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()
        self._clock = clock

    def save(self, code: str, meta: dict[str, Any], indicator_id: str | None) -> dict[str, Any]:
        digest = hashlib.sha256(code.encode()).hexdigest()
        now = _utc(self._clock())
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                unchanged = False
                if indicator_id is None:
                    indicator_id = f"ind-{uuid.uuid4().hex[:12]}"
                    self._db.execute("INSERT INTO indicators VALUES (?, ?)", (indicator_id, now))
                    version = 1
                else:
                    latest = self._db.execute(
                        "SELECT version, code_sha256 FROM indicator_versions "
                        "WHERE indicator_id = ? ORDER BY version DESC LIMIT 1",
                        (indicator_id,),
                    ).fetchone()
                    if latest is None:
                        raise IndicatorNotFoundError(indicator_id)
                    unchanged = latest["code_sha256"] == digest
                    version = int(latest["version"]) + (0 if unchanged else 1)
                if not unchanged:
                    self._db.execute(
                        "INSERT INTO indicator_versions VALUES (?, ?, ?, ?, ?, ?)",
                        (indicator_id, version, code, digest, json.dumps(meta), now),
                    )
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return self.indicator(indicator_id, version)

    def indicator(self, indicator_id: str, version: int | None = None) -> dict[str, Any]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM indicator_versions WHERE indicator_id = ? ORDER BY version",
                (indicator_id,),
            ).fetchall()
        if not rows:
            raise IndicatorNotFoundError(indicator_id)
        row = (
            rows[-1]
            if version is None
            else next((item for item in rows if item["version"] == version), None)
        )
        if row is None:
            raise IndicatorNotFoundError(f"{indicator_id} v{version}")
        meta = json.loads(row["meta"])
        return {
            "kind": "custom_indicator",
            "schema_version": "0.1",
            "indicator_id": indicator_id,
            "version": row["version"],
            "versions": [int(item["version"]) for item in rows],
            "type": f"py:{indicator_id}@{row['version']}",
            "name": meta["name"],
            "inputs": meta["inputs"],
            "outputs": meta["outputs"],
            "pane": meta["pane"],
            "code": row["code"],
            "code_sha256": row["code_sha256"],
            "created_at_utc": row["created_at_utc"],
        }

    def indicators(self) -> list[dict[str, Any]]:
        """Every version of every indicator, so charts can still draw a pinned one."""
        with self._lock:
            rows = self._db.execute(
                "SELECT indicator_id, version FROM indicator_versions "
                "ORDER BY indicator_id, version"
            ).fetchall()
        return [self.indicator(row["indicator_id"], int(row["version"])) for row in rows]
