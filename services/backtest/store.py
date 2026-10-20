"""Strategies, versions and runs in `SIW_DATA_DIR/backtests.sqlite3`.

Strategy versions and finished results are immutable: a change to a strategy adds a
version, and a run's results are written once. Backtest fills never leave this file.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.backtest.strategy import canonical, fingerprint, validate_definition

SCHEMA = """
CREATE TABLE IF NOT EXISTS strategies (
    strategy_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS strategy_versions (
    strategy_id TEXT NOT NULL REFERENCES strategies(strategy_id),
    version INTEGER NOT NULL,
    strategy_type TEXT NOT NULL,
    definition TEXT NOT NULL,
    definition_sha256 TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    PRIMARY KEY (strategy_id, version)
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    strategy_version INTEGER NOT NULL,
    strategy_sha256 TEXT NOT NULL,
    config TEXT NOT NULL,
    status TEXT NOT NULL,
    progress REAL NOT NULL DEFAULT 0,
    created_at_utc TEXT NOT NULL,
    started_at_utc TEXT,
    finished_at_utc TEXT,
    error TEXT,
    summary TEXT,
    results TEXT,
    FOREIGN KEY (strategy_id, strategy_version)
        REFERENCES strategy_versions(strategy_id, version)
);
CREATE INDEX IF NOT EXISTS runs_by_created ON runs(created_at_utc);
"""


class BacktestNotFoundError(LookupError):
    pass


def _utc_text(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class BacktestStore:
    def __init__(self, path: Path, clock: Callable[[], datetime]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()
        self._clock = clock

    def _now(self) -> str:
        return _utc_text(self._clock())

    # --- strategies -----------------------------------------------------------------

    def _strategy_document(self, row: sqlite3.Row, versions: list[int]) -> dict[str, Any]:
        return {
            "kind": "backtest_strategy",
            "schema_version": "0.1",
            "strategy_id": row["strategy_id"],
            "name": row["name"],
            "version": row["version"],
            "strategy_type": row["strategy_type"],
            "definition": json.loads(row["definition"]),
            "definition_sha256": row["definition_sha256"],
            "created_at_utc": row["created_at_utc"],
            "versions": versions,
        }

    def save_strategy(
        self, name: str, definition: object, strategy_id: str | None = None
    ) -> dict[str, Any]:
        """A new strategy, or the next version of an existing one (unchanged: no new one)."""
        clean = validate_definition(definition)
        digest = fingerprint(clean)
        now = self._now()
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                unchanged = False
                if strategy_id is None:
                    strategy_id = f"strategy-{uuid.uuid4().hex[:12]}"
                    self._db.execute(
                        "INSERT INTO strategies VALUES (?, ?, ?)", (strategy_id, name, now)
                    )
                    version = 1
                else:
                    if (
                        self._db.execute(
                            "SELECT 1 FROM strategies WHERE strategy_id = ?", (strategy_id,)
                        ).fetchone()
                        is None
                    ):
                        raise BacktestNotFoundError(strategy_id)
                    self._db.execute(
                        "UPDATE strategies SET name = ? WHERE strategy_id = ?", (name, strategy_id)
                    )
                    latest = self._db.execute(
                        "SELECT version, definition_sha256 FROM strategy_versions "
                        "WHERE strategy_id = ? ORDER BY version DESC LIMIT 1",
                        (strategy_id,),
                    ).fetchone()
                    unchanged = latest["definition_sha256"] == digest
                    version = int(latest["version"]) + (0 if unchanged else 1)
                if not unchanged:
                    self._db.execute(
                        "INSERT INTO strategy_versions VALUES (?, ?, ?, ?, ?, ?)",
                        (strategy_id, version, clean["type"], canonical(clean), digest, now),
                    )
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return self.strategy(strategy_id, version)

    def strategy(self, strategy_id: str, version: int | None = None) -> dict[str, Any]:
        with self._lock:
            rows = self._db.execute(
                "SELECT s.name, v.* FROM strategy_versions v JOIN strategies s USING (strategy_id) "
                "WHERE strategy_id = ? ORDER BY version",
                (strategy_id,),
            ).fetchall()
        if not rows:
            raise BacktestNotFoundError(strategy_id)
        versions = [int(row["version"]) for row in rows]
        chosen = (
            rows[-1]
            if version is None
            else next((row for row in rows if row["version"] == version), None)
        )
        if chosen is None:
            raise BacktestNotFoundError(f"{strategy_id} v{version}")
        return self._strategy_document(chosen, versions)

    def strategies(self) -> list[dict[str, Any]]:
        with self._lock:
            ids = [
                row["strategy_id"]
                for row in self._db.execute(
                    "SELECT strategy_id FROM strategies ORDER BY created_at_utc DESC, strategy_id"
                )
            ]
        return [self.strategy(strategy_id) for strategy_id in ids]

    # --- runs -----------------------------------------------------------------------

    def _run_document(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "kind": "backtest_run",
            "schema_version": "0.1",
            "run_id": row["run_id"],
            "status": row["status"],
            "progress": round(float(row["progress"]), 4),
            "strategy_id": row["strategy_id"],
            "strategy_version": row["strategy_version"],
            "strategy_sha256": row["strategy_sha256"],
            "config": json.loads(row["config"]),
            "created_at_utc": row["created_at_utc"],
            "started_at_utc": row["started_at_utc"],
            "finished_at_utc": row["finished_at_utc"],
            "error": row["error"],
            "summary": None if row["summary"] is None else json.loads(row["summary"]),
        }

    def create_run(self, strategy: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        with self._lock:
            self._db.execute(
                "INSERT INTO runs (run_id, strategy_id, strategy_version, strategy_sha256, "
                "config, status, created_at_utc) VALUES (?, ?, ?, ?, ?, 'queued', ?)",
                (
                    run_id,
                    strategy["strategy_id"],
                    strategy["version"],
                    strategy["definition_sha256"],
                    canonical(config),
                    self._now(),
                ),
            )
        return self.run(run_id)

    def run(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._db.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise BacktestNotFoundError(run_id)
        return self._run_document(row)

    def runs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM runs ORDER BY created_at_utc DESC, rowid DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._run_document(row) for row in rows]

    def queued_run_ids(self) -> list[str]:
        with self._lock:
            return [
                row["run_id"]
                for row in self._db.execute(
                    "SELECT run_id FROM runs WHERE status = 'queued' ORDER BY rowid"
                )
            ]

    def mark_running(self, run_id: str) -> bool:
        with self._lock:
            cursor = self._db.execute(
                "UPDATE runs SET status = 'running', started_at_utc = ? "
                "WHERE run_id = ? AND status = 'queued'",
                (self._now(), run_id),
            )
        return cursor.rowcount == 1

    def set_progress(self, run_id: str, fraction: float) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE runs SET progress = ? WHERE run_id = ? AND status = 'running'",
                (max(0.0, min(1.0, fraction)), run_id),
            )

    def finish(
        self,
        run_id: str,
        status: str,
        *,
        error: str | None = None,
        results: dict[str, Any] | None = None,
    ) -> None:
        summary = None
        if results is not None:
            metrics = results["metrics"]
            summary = canonical(
                {
                    "final_equity": results["final_equity"],
                    "net_return": metrics["net_return"],
                    "max_drawdown": metrics["max_drawdown"],
                    "trade_count": metrics["trade_count"],
                    "benchmark_return": metrics["benchmark_return"],
                    "warning_count": len(results["warnings"]),
                }
            )
        with self._lock:
            self._db.execute(
                "UPDATE runs SET status = ?, finished_at_utc = ?, error = ?, summary = ?, "
                "results = ?, progress = CASE WHEN ? = 'succeeded' THEN 1 ELSE progress END "
                "WHERE run_id = ? AND status IN ('queued', 'running') AND results IS NULL",
                (
                    status,
                    self._now(),
                    None if error is None else error[:2000],
                    summary,
                    None if results is None else json.dumps(results, separators=(",", ":")),
                    status,
                    run_id,
                ),
            )

    def request_cancel(self, run_id: str) -> dict[str, Any]:
        """A queued run is cancelled at once; a running one stops at its next step."""
        with self._lock:
            self._db.execute(
                "UPDATE runs SET status = 'cancelled', finished_at_utc = ? "
                "WHERE run_id = ? AND status = 'queued'",
                (self._now(), run_id),
            )
        return self.run(run_id)

    def results(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT results FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise BacktestNotFoundError(run_id)
        return None if row["results"] is None else json.loads(row["results"])

    def fail_interrupted(self) -> None:
        """Runs left running by a stopped process can never finish; say so."""
        with self._lock:
            self._db.execute(
                "UPDATE runs SET status = 'failed', finished_at_utc = ?, "
                "error = 'the app stopped while this run was in progress' "
                "WHERE status = 'running'",
                (self._now(),),
            )

    def close(self) -> None:
        with self._lock:
            self._db.close()
