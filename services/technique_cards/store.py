"""Technique cards and extraction jobs (ADR 0056 L2), in `technique_cards.sqlite3`.

A card keeps what the model wrote, the script checks, and the user's resolutions apart:
- `card`: the extracted (merged) card, never edited;
- `checks`: the script flags on it;
- `resolutions`: per condition `confirm` / `edit` (with the user's condition) / `drop`, and per
  ambiguity the user's answer ("사용자 지정"). Each save makes a new version.
A card is ready for the rule builder only when every flagged condition is resolved, its
evidence is not withdrawn, and the last library recheck allowed its use.

The transcript is not stored here (it stays in the 0600 cache); only its hash.
"""

from __future__ import annotations

import builtins
import json
import sqlite3
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS technique_cards (
    card_id TEXT PRIMARY KEY,
    item_ref TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    version INTEGER NOT NULL,
    card TEXT NOT NULL,
    checks TEXT NOT NULL,
    resolutions TEXT NOT NULL,
    usage TEXT NOT NULL,
    attempts TEXT NOT NULL,
    versions TEXT NOT NULL,
    context_ref TEXT NOT NULL,
    revision_ref TEXT NOT NULL,
    transcript_sha256 TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    recheck_outcome TEXT NOT NULL,
    rechecked_at TEXT NOT NULL,
    withdrawn INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS technique_card_versions (
    card_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    resolutions TEXT NOT NULL,
    saved_at TEXT NOT NULL,
    PRIMARY KEY (card_id, version)
);
CREATE TABLE IF NOT EXISTS technique_card_drafts (
    draft_id TEXT PRIMARY KEY,
    card_id TEXT NOT NULL,
    state TEXT NOT NULL,
    model TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    result TEXT,
    strategy_id TEXT,
    strategy_version INTEGER,
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS technique_card_jobs (
    job_id TEXT PRIMARY KEY,
    item_ref TEXT NOT NULL,
    state TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    card_id TEXT,
    attempts TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    finished_at TEXT
);
"""

ACTIONS = ("confirm", "edit", "drop")


def _utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class CardNotFoundError(KeyError):
    pass


def ready(row: dict[str, Any]) -> tuple[bool, list[str]]:
    """Whether the card may go to the rule builder, and what still blocks it."""
    blocks = []
    if row["withdrawn"]:
        blocks.append("evidence withdrawn")
    if row["recheck_outcome"] not in ("still_usable", "conditional"):
        blocks.append(f"library recheck: {row['recheck_outcome']}")
    resolved = row["resolutions"].get("conditions", {})
    for entry in row["checks"]["conditions"]:
        if entry["flags"] and str(entry["index"]) not in resolved:
            blocks.append(f"condition {entry['index']} not resolved")
    return not blocks, blocks


class CardStore:
    def __init__(self, path: Path, clock: Callable[[], datetime]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()
        self._clock = clock

    # --- jobs ---------------------------------------------------------------------

    def new_job(self, item_ref: str) -> str:
        job_id = uuid.uuid4().hex
        with self._lock:
            self._db.execute(
                "INSERT INTO technique_card_jobs (job_id, item_ref, state, created_at)"
                " VALUES (?, ?, 'running', ?)",
                (job_id, item_ref, _utc(self._clock())),
            )
        return job_id

    def finish_job(
        self,
        job_id: str,
        card_id: str | None,
        error: str,
        attempts: list[dict[str, Any]],
    ) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE technique_card_jobs SET state = ?, card_id = ?, error = ?, attempts = ?,"
                " finished_at = ? WHERE job_id = ?",
                (
                    "failed" if error else "done",
                    card_id,
                    error[:500],
                    json.dumps(attempts),
                    _utc(self._clock()),
                    job_id,
                ),
            )

    def job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM technique_card_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["attempts"] = json.loads(out["attempts"])
        return out

    def running_job(self, item_ref: str) -> str | None:
        with self._lock:
            row = self._db.execute(
                "SELECT job_id FROM technique_card_jobs WHERE item_ref = ? AND state = 'running'",
                (item_ref,),
            ).fetchone()
        return None if row is None else str(row[0])

    def fail_running_jobs(self) -> None:
        """At start-up: a job left running by a closed process will never finish."""
        with self._lock:
            self._db.execute(
                "UPDATE technique_card_drafts SET state = 'failed', error = 'app closed during the"
                " draft', finished_at = ? WHERE state = 'running'",
                (_utc(self._clock()),),
            )
            self._db.execute(
                "UPDATE technique_card_jobs SET state = 'failed', error = 'app closed during the"
                " extraction', finished_at = ? WHERE state = 'running'",
                (_utc(self._clock()),),
            )

    # --- cards --------------------------------------------------------------------

    def add(self, fields: dict[str, Any]) -> str:
        card_id = uuid.uuid4().hex
        now = _utc(self._clock())
        with self._lock:
            self._db.execute(
                "INSERT INTO technique_cards (card_id, item_ref, title, url, version, card,"
                " checks, resolutions, usage, attempts, versions, context_ref, revision_ref,"
                " transcript_sha256, verification_status, recheck_outcome, rechecked_at,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
                " ?, ?, ?, ?)",
                (
                    card_id,
                    fields["item_ref"],
                    fields["title"],
                    fields["url"],
                    json.dumps(fields["card"], ensure_ascii=False),
                    json.dumps(fields["checks"], ensure_ascii=False),
                    json.dumps({"conditions": {}, "ambiguities": {}}),
                    json.dumps(fields["usage"]),
                    json.dumps(fields["attempts"]),
                    json.dumps(fields["versions"]),
                    fields["context_ref"],
                    fields["revision_ref"],
                    fields["transcript_sha256"],
                    fields["verification_status"],
                    fields["recheck_outcome"],
                    now,
                    now,
                    now,
                ),
            )
        return card_id

    def _row(self, row: sqlite3.Row) -> dict[str, Any]:
        out = dict(row)
        for key in ("card", "checks", "resolutions", "usage", "attempts", "versions"):
            out[key] = json.loads(out[key])
        out["withdrawn"] = bool(out["withdrawn"])
        out["ready"], out["blocks"] = ready(out)
        out["corrected"] = sum(
            1 for r in out["resolutions"]["conditions"].values() if r["action"] in ("edit", "drop")
        )
        return out

    def get(self, card_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM technique_cards WHERE card_id = ?", (card_id,)
            ).fetchone()
        if row is None:
            raise CardNotFoundError(card_id)
        return self._row(row)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM technique_cards ORDER BY created_at DESC, card_id"
            ).fetchall()
        return [self._row(row) for row in rows]

    def resolve(
        self,
        card_id: str,
        version: int,
        conditions: dict[str, dict[str, Any]],
        ambiguities: dict[str, str],
    ) -> dict[str, Any]:
        """Replace the user's resolutions; refuses a stale `version` (another save won)."""
        current = self.get(card_id)
        if current["version"] != version:
            raise ValueError(f"card is at version {current['version']}, not {version}")
        resolutions = {"conditions": conditions, "ambiguities": ambiguities}
        now = _utc(self._clock())
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._db.execute(
                    "UPDATE technique_cards SET resolutions = ?, version = version + 1,"
                    " updated_at = ? WHERE card_id = ?",
                    (json.dumps(resolutions, ensure_ascii=False), now, card_id),
                )
                self._db.execute(
                    "INSERT INTO technique_card_versions (card_id, version, resolutions, saved_at)"
                    " VALUES (?, ?, ?, ?)",
                    (card_id, version + 1, json.dumps(resolutions, ensure_ascii=False), now),
                )
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise
        return self.get(card_id)

    def rechecked(self, card_id: str, outcome: str) -> dict[str, Any]:
        withdrawn = outcome in ("stop_using", "unknown")
        with self._lock:
            self._db.execute(
                "UPDATE technique_cards SET recheck_outcome = ?, rechecked_at = ?,"
                " withdrawn = MAX(withdrawn, ?) WHERE card_id = ?",
                (outcome, _utc(self._clock()), int(withdrawn), card_id),
            )
        return self.get(card_id)

    # --- strategy drafts (ADR 0056 L3) -----------------------------------------------------

    def new_draft(self, card_id: str, model: str) -> str:
        draft_id = uuid.uuid4().hex
        with self._lock:
            self._db.execute(
                "INSERT INTO technique_card_drafts (draft_id, card_id, state, model, created_at)"
                " VALUES (?, ?, 'running', ?, ?)",
                (draft_id, card_id, model, _utc(self._clock())),
            )
        return draft_id

    def finish_draft(self, draft_id: str, result: dict[str, Any] | None, error: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE technique_card_drafts SET state = ?, result = ?, error = ?,"
                " finished_at = ? WHERE draft_id = ?",
                (
                    "failed" if error else "done",
                    None if result is None else json.dumps(result, ensure_ascii=False),
                    error[:500],
                    _utc(self._clock()),
                    draft_id,
                ),
            )

    def _draft_row(self, row: sqlite3.Row) -> dict[str, Any]:
        out = dict(row)
        out["result"] = None if out["result"] is None else json.loads(out["result"])
        return out

    def draft(self, draft_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM technique_card_drafts WHERE draft_id = ?", (draft_id,)
            ).fetchone()
        if row is None:
            raise CardNotFoundError(draft_id)
        return self._draft_row(row)

    def drafts(self, card_id: str) -> builtins.list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM technique_card_drafts WHERE card_id = ?"
                " ORDER BY created_at DESC, draft_id",
                (card_id,),
            ).fetchall()
        return [self._draft_row(row) for row in rows]

    def running_draft(self, card_id: str) -> str | None:
        with self._lock:
            row = self._db.execute(
                "SELECT draft_id FROM technique_card_drafts"
                " WHERE card_id = ? AND state = 'running'",
                (card_id,),
            ).fetchone()
        return None if row is None else str(row[0])

    def saved_as(self, draft_id: str, strategy_id: str, version: int) -> dict[str, Any]:
        """The strategy (and version) a draft became: the card side of the provenance link."""
        with self._lock:
            self._db.execute(
                "UPDATE technique_card_drafts SET strategy_id = ?, strategy_version = ?"
                " WHERE draft_id = ?",
                (strategy_id, version, draft_id),
            )
        return self.draft(draft_id)
