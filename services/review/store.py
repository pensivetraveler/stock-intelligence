"""Trade review labels in `SIW_DATA_DIR/review.sqlite3` (ADR 0042).

Journal records stay untouched: which strategy a fill belongs to, each strategy's grouping
rule, manual groups, and trade notes live here, keyed by execution refs
("<ledger>:<execution_id>") and trade ids. Notes are appended as revisions, never changed,
so when each was written stays on record.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.review.trades import DEFAULT_RULE, Rule, cast_rule

SCHEMA = """
CREATE TABLE IF NOT EXISTS strategy_assignments (
    execution_ref TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    assigned_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS strategy_rules (
    strategy_id TEXT PRIMARY KEY,
    rule TEXT NOT NULL CHECK (rule IN ('flat', 'day', 'fifo', 'manual')),
    updated_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS manual_groups (
    execution_ref TEXT PRIMARY KEY,
    group_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS manual_groups_by_group ON manual_groups (group_id);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS account_capital (
    entry_id TEXT PRIMARY KEY,
    account_ref TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    amount TEXT NOT NULL,
    currency TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS report_drafts (
    draft_id TEXT PRIMARY KEY,
    trade_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    document TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trade_reports (
    report_id TEXT PRIMARY KEY,
    trade_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    document TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS trade_reports_by_trade ON trade_reports (trade_id, created_at_utc);
CREATE TABLE IF NOT EXISTS report_images (
    report_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    kind TEXT NOT NULL,
    png BLOB NOT NULL,
    PRIMARY KEY (report_id, position)
);
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL UNIQUE,
    created_at_utc TEXT NOT NULL,
    model TEXT NOT NULL,
    system TEXT NOT NULL,
    state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversation_messages (
    conversation_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    message TEXT NOT NULL,
    PRIMARY KEY (conversation_id, position)
);
CREATE TABLE IF NOT EXISTS conversation_events (
    conversation_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    event TEXT NOT NULL,
    PRIMARY KEY (conversation_id, position)
);
CREATE TABLE IF NOT EXISTS principle_versions (
    version INTEGER PRIMARY KEY,
    created_at_utc TEXT NOT NULL,
    document TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS principle_check_results (
    trade_id TEXT PRIMARY KEY,
    basis TEXT NOT NULL,
    computed_at_utc TEXT NOT NULL,
    broken INTEGER NOT NULL,
    document TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS period_reviews (
    review_id TEXT PRIMARY KEY,
    created_at_utc TEXT NOT NULL,
    document TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS period_commentaries (
    review_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    document TEXT NOT NULL,
    PRIMARY KEY (review_id, position)
);
CREATE TABLE IF NOT EXISTS trade_annotations (
    trade_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    note TEXT NOT NULL,
    tags TEXT NOT NULL,
    reviewed INTEGER NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    PRIMARY KEY (trade_id, revision)
);
"""


class GroupNotFoundError(LookupError):
    pass


class DraftNotFoundError(LookupError):
    pass


class CapitalNotFoundError(LookupError):
    pass


class ConversationNotFoundError(LookupError):
    pass


class PeriodReviewNotFoundError(LookupError):
    pass


def _utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class ReviewStore:
    def __init__(self, path: Path, clock: Callable[[], datetime]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()
        self._clock = clock
        # Bumped by every change to how fills are grouped (in this process), so cached
        # trades know to rebuild. Notes do not change grouping.
        self.grouping_version = 0

    # --- strategies -----------------------------------------------------------------

    def assignments(self) -> dict[str, str]:
        with self._lock:
            rows = self._db.execute("SELECT execution_ref, strategy_id FROM strategy_assignments")
            return dict(rows.fetchall())

    def assign(self, refs: Iterable[str], strategy_id: str | None) -> int:
        refs = list(dict.fromkeys(refs))
        now = _utc(self._clock())
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                for ref in refs:
                    if strategy_id is None:
                        self._db.execute(
                            "DELETE FROM strategy_assignments WHERE execution_ref = ?", (ref,)
                        )
                    else:
                        self._db.execute(
                            "INSERT INTO strategy_assignments VALUES (?, ?, ?) "
                            "ON CONFLICT (execution_ref) DO UPDATE SET "
                            "strategy_id = excluded.strategy_id, "
                            "assigned_at_utc = excluded.assigned_at_utc",
                            (ref, strategy_id, now),
                        )
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self.grouping_version += 1
        return len(refs)

    def rules(self) -> dict[str, Rule]:
        with self._lock:
            rows = self._db.execute("SELECT strategy_id, rule FROM strategy_rules").fetchall()
        return {strategy_id: cast_rule(rule) for strategy_id, rule in rows}

    def rule(self, strategy_id: str) -> Rule:
        return self.rules().get(strategy_id, DEFAULT_RULE)

    def set_rule(self, strategy_id: str, rule: Rule) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO strategy_rules VALUES (?, ?, ?) ON CONFLICT (strategy_id) "
                "DO UPDATE SET rule = excluded.rule, updated_at_utc = excluded.updated_at_utc",
                (strategy_id, rule, _utc(self._clock())),
            )
            self.grouping_version += 1

    # --- manual groups --------------------------------------------------------------

    def groups(self) -> dict[str, str]:
        with self._lock:
            return dict(self._db.execute("SELECT execution_ref, group_id FROM manual_groups"))

    def make_group(self, refs: Iterable[str]) -> str:
        """Put the fills in one new group; they leave any group they were in."""
        group_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                for ref in dict.fromkeys(refs):
                    self._db.execute(
                        "INSERT INTO manual_groups VALUES (?, ?) ON CONFLICT (execution_ref) "
                        "DO UPDATE SET group_id = excluded.group_id",
                        (ref, group_id),
                    )
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self.grouping_version += 1
        return group_id

    def remove_group(self, group_id: str) -> None:
        with self._lock:
            cursor = self._db.execute("DELETE FROM manual_groups WHERE group_id = ?", (group_id,))
            self.grouping_version += 1
        if cursor.rowcount == 0:
            raise GroupNotFoundError(group_id)

    # --- notes ----------------------------------------------------------------------

    def annotations(self) -> dict[str, dict[str, Any]]:
        """The latest note of every trade."""
        with self._lock:
            rows = self._db.execute(
                "SELECT a.trade_id, a.note, a.tags, a.reviewed, a.recorded_at_utc, a.revision "
                "FROM trade_annotations a JOIN (SELECT trade_id, MAX(revision) AS revision "
                "FROM trade_annotations GROUP BY trade_id) latest "
                "ON a.trade_id = latest.trade_id AND a.revision = latest.revision"
            ).fetchall()
        return {
            trade_id: {
                "note": note,
                "tags": json.loads(tags),
                "reviewed": bool(reviewed),
                "recorded_at_utc": recorded,
                "revision": revision,
            }
            for trade_id, note, tags, reviewed, recorded, revision in rows
        }

    def annotate(self, trade_id: str, note: str, tags: list[str], reviewed: bool) -> dict[str, Any]:
        recorded = _utc(self._clock())
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                (last,) = self._db.execute(
                    "SELECT COALESCE(MAX(revision), 0) FROM trade_annotations WHERE trade_id = ?",
                    (trade_id,),
                ).fetchone()
                self._db.execute(
                    "INSERT INTO trade_annotations VALUES (?, ?, ?, ?, ?, ?)",
                    (trade_id, last + 1, note, json.dumps(tags), int(reviewed), recorded),
                )
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return {
            "note": note,
            "tags": tags,
            "reviewed": reviewed,
            "recorded_at_utc": recorded,
            "revision": last + 1,
        }

    # --- settings -------------------------------------------------------------------

    def setting(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO settings VALUES (?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # --- account capital ------------------------------------------------------------

    def capital(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT entry_id, account_ref, effective_from, amount, currency, recorded_at_utc "
                "FROM account_capital ORDER BY account_ref, effective_from"
            ).fetchall()
        keys = (
            "entry_id",
            "account_ref",
            "effective_from",
            "amount",
            "currency",
            "recorded_at_utc",
        )
        return [dict(zip(keys, row, strict=True)) for row in rows]

    def add_capital(
        self, account_ref: str, effective_from: str, amount: str, currency: str
    ) -> dict[str, Any]:
        entry = {
            "entry_id": f"capital-{uuid.uuid4().hex[:12]}",
            "account_ref": account_ref,
            "effective_from": effective_from,
            "amount": amount,
            "currency": currency,
            "recorded_at_utc": _utc(self._clock()),
        }
        with self._lock:
            self._db.execute(
                "INSERT INTO account_capital VALUES (?, ?, ?, ?, ?, ?)", tuple(entry.values())
            )
        return entry

    def remove_capital(self, entry_id: str) -> None:
        with self._lock:
            cursor = self._db.execute("DELETE FROM account_capital WHERE entry_id = ?", (entry_id,))
        if cursor.rowcount == 0:
            raise CapitalNotFoundError(entry_id)

    # --- reports --------------------------------------------------------------------

    def save_draft(self, document: dict[str, Any]) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO report_drafts VALUES (?, ?, ?, ?)",
                (
                    document["draft_id"],
                    document["trade_id"],
                    document["created_at_utc"],
                    json.dumps(document),
                ),
            )

    def draft(self, draft_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._db.execute(
                "SELECT document FROM report_drafts WHERE draft_id = ?", (draft_id,)
            ).fetchone()
        if row is None:
            raise DraftNotFoundError(draft_id)
        document: dict[str, Any] = json.loads(row[0])
        return document

    def save_report(self, document: dict[str, Any]) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO trade_reports VALUES (?, ?, ?, ?)",
                (
                    document["report_id"],
                    document["trade_id"],
                    document["created_at_utc"],
                    json.dumps(document),
                ),
            )

    def reports(self, trade_id: str) -> list[dict[str, Any]]:
        """A trade's reports, newest first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT document FROM trade_reports WHERE trade_id = ? "
                "ORDER BY created_at_utc DESC, report_id DESC",
                (trade_id,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def save_report_images(self, report_id: str, images: list[tuple[str, bytes]]) -> None:
        """The PNGs sent with a report: a follow-up conversation re-sends the same bytes."""
        with self._lock:
            self._db.executemany(
                "INSERT INTO report_images VALUES (?, ?, ?, ?)",
                [(report_id, i, kind, png) for i, (kind, png) in enumerate(images)],
            )

    def report_images(self, report_id: str) -> list[tuple[str, bytes]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT kind, png FROM report_images WHERE report_id = ? ORDER BY position",
                (report_id,),
            ).fetchall()
        return [(str(kind), bytes(png)) for kind, png in rows]

    def report(self, report_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT document FROM trade_reports WHERE report_id = ?", (report_id,)
            ).fetchone()
        return None if row is None else dict(json.loads(row[0]))

    # --- conversations (step 2b) -------------------------------------------------------
    #
    # Messages are only ever appended: the conversation is replayed to Claude as stored,
    # so earlier turns (and their thinking blocks) stay exactly as they were. The `state`
    # row (status, pending tool calls, token totals) is the one thing that is updated.

    def conversation_for(self, report_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT conversation_id, report_id, created_at_utc, model, system, state "
                "FROM conversations WHERE report_id = ?",
                (report_id,),
            ).fetchone()
        return None if row is None else self._conversation_row(row)

    def conversation(self, conversation_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._db.execute(
                "SELECT conversation_id, report_id, created_at_utc, model, system, state "
                "FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        if row is None:
            raise ConversationNotFoundError(conversation_id)
        return self._conversation_row(row)

    @staticmethod
    def _conversation_row(row: tuple[Any, ...]) -> dict[str, Any]:
        conversation_id, report_id, created, model, system, state = row
        return {
            "conversation_id": conversation_id,
            "report_id": report_id,
            "created_at_utc": created,
            "model": model,
            "system": system,
            "state": json.loads(state),
        }

    def start_conversation(
        self, report_id: str, model: str, system: str, state: dict[str, Any]
    ) -> dict[str, Any]:
        conversation_id = f"conversation-{uuid.uuid4().hex[:12]}"
        with self._lock:
            self._db.execute(
                "INSERT INTO conversations VALUES (?, ?, ?, ?, ?, ?)",
                (
                    conversation_id,
                    report_id,
                    _utc(self._clock()),
                    model,
                    system,
                    json.dumps(state),
                ),
            )
        return self.conversation(conversation_id)

    def set_conversation_state(self, conversation_id: str, state: dict[str, Any]) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE conversations SET state = ? WHERE conversation_id = ?",
                (json.dumps(state), conversation_id),
            )

    def _append(self, table: str, column: str, conversation_id: str, item: dict[str, Any]) -> None:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                (count,) = self._db.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE conversation_id = ?",  # noqa: S608
                    (conversation_id,),
                ).fetchone()
                self._db.execute(
                    f"INSERT INTO {table} (conversation_id, position, {column}) "  # noqa: S608
                    "VALUES (?, ?, ?)",
                    (conversation_id, count, json.dumps(item)),
                )
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def append_message(self, conversation_id: str, message: dict[str, Any]) -> None:
        self._append("conversation_messages", "message", conversation_id, message)

    def append_event(self, conversation_id: str, event: dict[str, Any]) -> None:
        self._append("conversation_events", "event", conversation_id, event)

    def _items(self, table: str, column: str, conversation_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                f"SELECT {column} FROM {table} WHERE conversation_id = ? "  # noqa: S608
                "ORDER BY position",
                (conversation_id,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def messages(self, conversation_id: str) -> list[dict[str, Any]]:
        return self._items("conversation_messages", "message", conversation_id)

    def events(self, conversation_id: str) -> list[dict[str, Any]]:
        return self._items("conversation_events", "event", conversation_id)

    # --- trading principles (step 3a) --------------------------------------------------
    #
    # Each save is a new, complete version; versions are never changed, so a trade can
    # always be checked against the principles that were in effect when it opened.

    def principle_versions(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT version, created_at_utc, document FROM principle_versions ORDER BY version"
            ).fetchall()
        return [
            {"version": version, "created_at_utc": created, **json.loads(document)}
            for version, created, document in rows
        ]

    def save_principles(self, items: list[dict[str, Any]], source_text: str) -> dict[str, Any]:
        created = _utc(self._clock())
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                (last,) = self._db.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM principle_versions"
                ).fetchone()
                self._db.execute(
                    "INSERT INTO principle_versions VALUES (?, ?, ?)",
                    (
                        last + 1,
                        created,
                        json.dumps({"items": items, "source_text": source_text}),
                    ),
                )
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return {
            "version": last + 1,
            "created_at_utc": created,
            "items": items,
            "source_text": source_text,
        }

    # --- stored principle checks (step 3b) ---------------------------------------------

    def save_check(self, trade_id: str, basis: str, document: dict[str, Any]) -> None:
        broken = sum(1 for r in document.get("results", []) if r["status"] == "broken")
        with self._lock:
            self._db.execute(
                "INSERT INTO principle_check_results VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (trade_id) DO UPDATE SET basis = excluded.basis, "
                "computed_at_utc = excluded.computed_at_utc, broken = excluded.broken, "
                "document = excluded.document",
                (trade_id, basis, _utc(self._clock()), broken, json.dumps(document)),
            )

    def check_summaries(self) -> dict[str, tuple[str, int]]:
        """Every stored check: trade id -> (basis, broken count)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT trade_id, basis, broken FROM principle_check_results"
            ).fetchall()
        return {trade_id: (basis, int(broken)) for trade_id, basis, broken in rows}

    def check_documents(self) -> dict[str, tuple[str, dict[str, Any]]]:
        """Every stored check: trade id -> (basis, document)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT trade_id, basis, document FROM principle_check_results"
            ).fetchall()
        return {trade_id: (basis, json.loads(document)) for trade_id, basis, document in rows}

    # --- period reviews (step 4a) ------------------------------------------------------

    def save_period_review(self, document: dict[str, Any]) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO period_reviews VALUES (?, ?, ?)",
                (document["review_id"], document["created_at_utc"], json.dumps(document)),
            )

    def period_review(self, review_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._db.execute(
                "SELECT document FROM period_reviews WHERE review_id = ?", (review_id,)
            ).fetchone()
        if row is None:
            raise PeriodReviewNotFoundError(review_id)
        document: dict[str, Any] = json.loads(row[0])
        return document

    def period_reviews(self) -> list[dict[str, Any]]:
        """Saved period reviews, newest first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT document FROM period_reviews ORDER BY created_at_utc DESC, review_id DESC"
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def add_period_commentary(self, review_id: str, document: dict[str, Any]) -> None:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                (count,) = self._db.execute(
                    "SELECT COUNT(*) FROM period_commentaries WHERE review_id = ?", (review_id,)
                ).fetchone()
                self._db.execute(
                    "INSERT INTO period_commentaries VALUES (?, ?, ?)",
                    (review_id, count, json.dumps(document)),
                )
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def period_commentaries(self, review_id: str) -> list[dict[str, Any]]:
        """A period review's commentaries, newest first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT document FROM period_commentaries WHERE review_id = ? "
                "ORDER BY position DESC",
                (review_id,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]
