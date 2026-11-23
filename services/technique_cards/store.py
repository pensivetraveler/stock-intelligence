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

from services.technique_cards.extract import DEFAULT_CHOICES, INITIAL_DEFAULT

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
CREATE TABLE IF NOT EXISTS technique_card_extractions (
    card_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    extraction TEXT NOT NULL,
    replaced_at TEXT NOT NULL,
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
CREATE TABLE IF NOT EXISTS technique_settings (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
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
    if row.get("user_withdrawn_at"):
        blocks.append("withdrawn by the user")
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
        columns = {r[1] for r in self._db.execute("PRAGMA table_info(technique_cards)")}
        if "members" not in columns:  # 2026-11-22: a series card records every volume
            self._db.execute(
                "ALTER TABLE technique_cards ADD COLUMN members TEXT NOT NULL DEFAULT '[]'"
            )
        # 2026-11-23: the user may withdraw a card (one of two from the same source); kept
        # apart from `withdrawn`, which says the library withdrew the evidence.
        for column in ("user_withdrawn_at", "user_withdrawn_note"):
            if column not in columns:
                self._db.execute(
                    f"ALTER TABLE technique_cards ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
                )
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
                " created_at, updated_at, members) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?,"
                " ?, ?, ?, ?, ?, ?, ?)",
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
                    json.dumps(fields.get("members") or [], ensure_ascii=False),
                ),
            )
        return card_id

    def _row(self, row: sqlite3.Row) -> dict[str, Any]:
        out = dict(row)
        for key in ("card", "checks", "resolutions", "usage", "attempts", "versions", "members"):
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

    def active_card_ids(self, item_ref: str) -> builtins.list[str]:
        """The source's cards the user has not withdrawn, newest first."""
        return [
            row["card_id"]
            for row in self.list()
            if row["item_ref"] == item_ref and not row["user_withdrawn_at"]
        ]

    def withdraw(self, card_id: str, note: str) -> dict[str, Any]:
        """The user's withdrawal: the card stays for the record but is no longer used."""
        self.get(card_id)
        with self._lock:
            self._db.execute(
                "UPDATE technique_cards SET user_withdrawn_at = ?, user_withdrawn_note = ?,"
                " updated_at = ? WHERE card_id = ? AND user_withdrawn_at = ''",
                (_utc(self._clock()), note, _utc(self._clock()), card_id),
            )
        return self.get(card_id)

    def replace(self, card_id: str, fields: dict[str, Any]) -> str:
        """A new extraction as the card's next version (ADR 0056, 2026-11-23).

        The previous extraction and its resolutions are kept in `technique_card_extractions`;
        the new version starts with no resolutions, because the conditions are new."""
        current = self.get(card_id)
        if current["user_withdrawn_at"]:
            raise ValueError("the card was withdrawn by the user")
        if current["item_ref"] != fields["item_ref"]:
            raise ValueError("the card is from another source")
        kept = {
            key: current[key]
            for key in (
                "title",
                "url",
                "card",
                "checks",
                "resolutions",
                "usage",
                "attempts",
                "versions",
                "context_ref",
                "revision_ref",
                "transcript_sha256",
                "verification_status",
                "recheck_outcome",
                "rechecked_at",
                "members",
                "created_at",
            )
        }
        empty: dict[str, dict[str, Any]] = {"conditions": {}, "ambiguities": {}}
        now = _utc(self._clock())
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._db.execute(
                    "INSERT INTO technique_card_extractions (card_id, version, extraction,"
                    " replaced_at) VALUES (?, ?, ?, ?)",
                    (card_id, current["version"], json.dumps(kept, ensure_ascii=False), now),
                )
                self._db.execute(
                    "UPDATE technique_cards SET title = ?, url = ?, version = version + 1,"
                    " card = ?, checks = ?, resolutions = ?, usage = ?, attempts = ?,"
                    " versions = ?, context_ref = ?, revision_ref = ?, transcript_sha256 = ?,"
                    " verification_status = ?, recheck_outcome = ?, rechecked_at = ?,"
                    " members = ?, updated_at = ? WHERE card_id = ?",
                    (
                        fields["title"],
                        fields["url"],
                        json.dumps(fields["card"], ensure_ascii=False),
                        json.dumps(fields["checks"], ensure_ascii=False),
                        json.dumps(empty),
                        json.dumps(fields["usage"]),
                        json.dumps(fields["attempts"]),
                        json.dumps(fields["versions"]),
                        fields["context_ref"],
                        fields["revision_ref"],
                        fields["transcript_sha256"],
                        fields["verification_status"],
                        fields["recheck_outcome"],
                        now,
                        json.dumps(fields.get("members") or [], ensure_ascii=False),
                        now,
                        card_id,
                    ),
                )
                self._db.execute(
                    "INSERT INTO technique_card_versions (card_id, version, resolutions, saved_at)"
                    " VALUES (?, ?, ?, ?)",
                    (card_id, current["version"] + 1, json.dumps(empty), now),
                )
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise
        return card_id

    def extractions(self, card_id: str) -> builtins.list[dict[str, Any]]:
        """Earlier extractions of the card, oldest first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT version, extraction, replaced_at FROM technique_card_extractions"
                " WHERE card_id = ? ORDER BY version",
                (card_id,),
            ).fetchall()
        return [{"version": r[0], "replaced_at": r[2]} | json.loads(r[1]) for r in rows]

    def resolve(
        self,
        card_id: str,
        version: int,
        conditions: dict[str, dict[str, Any]],
        ambiguities: dict[str, str],
        added: builtins.list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Replace the user's resolutions; refuses a stale `version` (another save won).

        `added` are conditions the user wrote (for example when an answer to a vague point
        makes a rule the source left out); they are the user's, never cited to the source."""
        current = self.get(card_id)
        if current["version"] != version:
            raise ValueError(f"card is at version {current['version']}, not {version}")
        resolutions = {
            "conditions": conditions,
            "ambiguities": ambiguities,
            "added": list(added or []),
        }
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

    def update_checks(self, card_id: str, checks: dict[str, Any]) -> dict[str, Any]:
        """New script check results for the same card text (a fixed checker, 2026-11-16);
        the card, its version and the user's resolutions stay as they are."""
        with self._lock:
            row = self._db.execute(
                "SELECT usage FROM technique_cards WHERE card_id = ?", (card_id,)
            ).fetchone()
            if row is None:
                raise CardNotFoundError(card_id)
            usage = json.loads(row[0]) | {"flags": checks["counts"]}
            self._db.execute(
                "UPDATE technique_cards SET checks = ?, usage = ? WHERE card_id = ?",
                (json.dumps(checks), json.dumps(usage), card_id),
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

    # --- settings -------------------------------------------------------------------------

    def default_model(self) -> str:
        """The model extractions and drafts use unless the user picks another for one run."""
        with self._lock:
            row = self._db.execute(
                "SELECT value FROM technique_settings WHERE name = 'default_model'"
            ).fetchone()
        value = None if row is None else str(row[0])
        if value is None or value in DEFAULT_CHOICES:
            return value or INITIAL_DEFAULT
        # A saved default that is no longer offered (a paused model): back to the
        # initial default, and the change is recorded so the screen can say so.
        now = _utc(self._clock())
        with self._lock:
            self._db.execute(
                "INSERT INTO technique_settings (name, value, updated_at)"
                " VALUES ('default_model_reset', ?, ?)"
                " ON CONFLICT(name) DO UPDATE SET value = excluded.value,"
                " updated_at = excluded.updated_at",
                (json.dumps({"from": value, "to": INITIAL_DEFAULT, "at": now}), now),
            )
            self._db.execute(
                "UPDATE technique_settings SET value = ?, updated_at = ?"
                " WHERE name = 'default_model'",
                (INITIAL_DEFAULT, now),
            )
        return INITIAL_DEFAULT

    def default_model_reset(self) -> dict[str, str] | None:
        """The last time a saved default was put back because it is no longer offered."""
        with self._lock:
            row = self._db.execute(
                "SELECT value FROM technique_settings WHERE name = 'default_model_reset'"
            ).fetchone()
        return None if row is None else dict(json.loads(row[0]))

    def limited_until(self, model: str) -> datetime | None:
        """When a model's limit ends, as its last limit answer said (#LAG-SIW-4)."""
        with self._lock:
            row = self._db.execute(
                "SELECT value FROM technique_settings WHERE name = ?", (f"limit:{model}",)
            ).fetchone()
        if row is None:
            return None
        return datetime.fromisoformat(json.loads(row[0])["until"])

    def hold(self, model: str, until: datetime, reason: str) -> None:
        """Skip `model` until `until`; the next extraction goes straight to the next one."""
        now = _utc(self._clock())
        value = json.dumps({"until": until.isoformat(), "reason": reason[:200], "at": now})
        with self._lock:
            self._db.execute(
                "INSERT INTO technique_settings (name, value, updated_at) VALUES (?, ?, ?)"
                " ON CONFLICT(name) DO UPDATE SET value = excluded.value,"
                " updated_at = excluded.updated_at",
                (f"limit:{model}", value, now),
            )

    def limits(self) -> dict[str, str]:
        """Each held model and the end of its limit (for the screen)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT name, value FROM technique_settings WHERE name LIKE 'limit:%'"
            ).fetchall()
        return {str(name)[6:]: json.loads(value)["until"] for name, value in rows}

    def set_default_model(self, model: str) -> str:
        if model not in DEFAULT_CHOICES:
            raise ValueError(f"default model must be one of {', '.join(DEFAULT_CHOICES)}")
        with self._lock:
            self._db.execute(
                "INSERT INTO technique_settings (name, value, updated_at)"
                " VALUES ('default_model', ?, ?)"
                " ON CONFLICT(name) DO UPDATE SET value = excluded.value,"
                " updated_at = excluded.updated_at",
                (model, _utc(self._clock())),
            )
        return self.default_model()
