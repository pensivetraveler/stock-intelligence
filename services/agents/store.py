"""Agent analyses in `SIW_DATA_DIR/agents.sqlite3` (ADR 0022).

An analysis's evidence is written once. Commentaries are appended; none is ever changed
or removed, so every answer stays next to the facts it was given.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from services.agents.evidence import Evidence

SCHEMA = """
CREATE TABLE IF NOT EXISTS analyses (
    analysis_id TEXT PRIMARY KEY,
    created_at_utc TEXT NOT NULL,
    document TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS commentaries (
    analysis_id TEXT NOT NULL REFERENCES analyses(analysis_id),
    position INTEGER NOT NULL,
    document TEXT NOT NULL,
    PRIMARY KEY (analysis_id, position)
);
"""


class AnalysisNotFoundError(LookupError):
    pass


def _utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class AgentStore:
    def __init__(self, path: Path, clock: Callable[[], datetime]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()
        self._clock = clock

    def save(self, evidence: Evidence) -> dict[str, Any]:
        analysis_id = f"analysis-{uuid.uuid4().hex[:12]}"
        facts = [fact.document() for fact in evidence.facts]
        document = {
            "analysis_id": analysis_id,
            "agent_id": evidence.agent_id,
            "agent_version": evidence.agent_version,
            "created_at_utc": _utc(self._clock()),
            "target": evidence.target,
            "cutoff_utc": evidence.cutoff_utc,
            "facts": facts,
            "missing": evidence.missing,
            "uncertainty": evidence.uncertainty,
            "warnings": evidence.warnings,
            "dataset_revisions": sorted(evidence.dataset_revisions),
            "indicator_versions": evidence.indicator_versions,
            "source_refs": evidence.source_refs,
        }
        with self._lock:
            self._db.execute(
                "INSERT INTO analyses VALUES (?, ?, ?)",
                (analysis_id, document["created_at_utc"], json.dumps(document)),
            )
        return self.analysis(analysis_id)

    def add_commentary(self, analysis_id: str, commentary: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if (
                self._db.execute(
                    "SELECT 1 FROM analyses WHERE analysis_id = ?", (analysis_id,)
                ).fetchone()
                is None
            ):
                raise AnalysisNotFoundError(analysis_id)
            (count,) = self._db.execute(
                "SELECT COUNT(*) FROM commentaries WHERE analysis_id = ?", (analysis_id,)
            ).fetchone()
            self._db.execute(
                "INSERT INTO commentaries VALUES (?, ?, ?)",
                (analysis_id, count, json.dumps(commentary)),
            )
        return self.analysis(analysis_id)

    def raw(self, analysis_id: str) -> dict[str, Any]:
        """The stored evidence without commentaries or the evidence summary."""
        with self._lock:
            row = self._db.execute(
                "SELECT document FROM analyses WHERE analysis_id = ?", (analysis_id,)
            ).fetchone()
        if row is None:
            raise AnalysisNotFoundError(analysis_id)
        return cast(dict[str, Any], json.loads(row[0]))

    def analysis(self, analysis_id: str) -> dict[str, Any]:
        document = self.raw(analysis_id)
        with self._lock:
            commentaries = [
                json.loads(row[0])
                for row in self._db.execute(
                    "SELECT document FROM commentaries WHERE analysis_id = ? ORDER BY position",
                    (analysis_id,),
                )
            ]
        latest = commentaries[-1] if commentaries else None
        return {
            "kind": "agent_analysis",
            "schema_version": "0.1",
            **document,
            "commentaries": commentaries,
            # The project's common evidence record (spec: AnalysisRun/Evidence).
            "evidence": {
                "kind": "agent_evidence",
                "schema_version": "0.1",
                "analysis_id": document["analysis_id"],
                "agent_version": document["agent_version"],
                "source_record_refs": sorted(set(document["source_refs"])),
                "dataset_revisions": document["dataset_revisions"],
                "cutoff_utc": document["cutoff_utc"],
                "observations": [fact["text"] for fact in document["facts"]]
                or ["No facts could be computed from the available data."],
                "inferences": latest["inferences"] if latest else [],
                "uncertainty": " ".join(document["uncertainty"])
                or "Facts only; no interpretation has been requested.",
            },
        }

    def analyses(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            ids = [
                row[0]
                for row in self._db.execute(
                    "SELECT analysis_id FROM analyses ORDER BY created_at_utc DESC, rowid DESC "
                    "LIMIT ?",
                    (limit,),
                )
            ]
        return [self.analysis(analysis_id) for analysis_id in ids]
