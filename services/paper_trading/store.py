"""Paper-order ledger and kill switch in `SIW_DATA_DIR/paper_orders.sqlite3` (ADR 0037).

The kill switch stops new orders only: it never cancels resting paper orders. Releasing
it does not resume trading; the runner must be armed again with an approval.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from packages.contracts.validator import validate_document
from services.market_data.storage import MigrationRunner
from services.paper_trading.limits import LimitResult

MIGRATIONS_DIR = Path(__file__).with_name("migrations")
DATABASE_NAME = "paper_orders.sqlite3"
OPEN_SUBMISSION_STATUSES = ("pending_response", "unknown")


class PaperStoreError(RuntimeError):
    pass


def _utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def approval_id(content: dict[str, Any]) -> str:
    """Approvals are identified by their content, so a changed one is a new approval."""
    return hashlib.sha256(_canonical(content).encode()).hexdigest()


class PaperOrderStore:
    def __init__(self, root: Path, *, now: Callable[[], datetime] | None = None) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database_path = self.root / DATABASE_NAME
        self._now = now or (lambda: datetime.now(timezone.utc))
        MigrationRunner(self.database_path, MIGRATIONS_DIR).apply()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.database_path, timeout=10)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                yield connection

    # Approvals and intents

    def record_approval(self, content: dict[str, Any]) -> str:
        identifier = approval_id(content)
        with self._connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO approvals VALUES (?, ?, ?)",
                (identifier, _canonical(content), _utc(self._now())),
            )
        return identifier

    def record_intent(self, document: dict[str, Any], result: LimitResult) -> bool:
        """Store an intent once. False if the same intent was already recorded.

        An intent that failed its limit check is stored as blocked, with its reasons.
        """
        validate_document(document)
        now = _utc(self._now())
        with self._connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO order_intents VALUES (?, ?, ?, ?, ?, ?)",
                (
                    document["intent_id"],
                    document["approval_id"],
                    document["broker_id"],
                    document["instrument_id"],
                    _canonical(document),
                    document["created_at_utc"],
                ),
            )
            if cursor.rowcount == 0:
                return False
            if not result.passed:
                db.execute(
                    "INSERT INTO limit_events (intent_id, reasons_json, at_utc) VALUES (?, ?, ?)",
                    (document["intent_id"], json.dumps(list(result.reasons)), now),
                )
                db.execute(
                    "INSERT INTO intent_outcomes VALUES (?, 'blocked', ?, ?)",
                    (document["intent_id"], ",".join(result.reasons), now),
                )
        return True

    def intent(self, intent_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT document_json FROM order_intents WHERE intent_id = ?", (intent_id,)
            ).fetchone()
        return None if row is None else dict(json.loads(row["document_json"]))

    def outcome(self, intent_id: str) -> tuple[str, str | None] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT status, reason FROM intent_outcomes WHERE intent_id = ?", (intent_id,)
            ).fetchone()
        return None if row is None else (str(row["status"]), row["reason"])

    def set_outcome(self, intent_id: str, status: str, reason: str | None = None) -> None:
        with self._connect() as db:
            try:
                db.execute(
                    "INSERT INTO intent_outcomes VALUES (?, ?, ?, ?)",
                    (intent_id, status, reason, _utc(self._now())),
                )
            except sqlite3.IntegrityError as exc:
                raise PaperStoreError(f"intent {intent_id} already has an outcome") from exc

    # Submissions

    def record_submission(self, document: dict[str, Any]) -> None:
        """Insert or update one submission; its intent may be submitted only once."""
        validate_document(document)
        now = _utc(self._now())
        with self._connect() as db:
            existing = db.execute(
                "SELECT intent_id FROM order_submissions WHERE submission_id = ?",
                (document["submission_id"],),
            ).fetchone()
            if existing is None:
                try:
                    db.execute(
                        "INSERT INTO order_submissions VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            document["submission_id"],
                            document["intent_id"],
                            document["status"],
                            document["requested_at_utc"],
                            _canonical(document),
                            now,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise PaperStoreError(
                        f"intent {document['intent_id']} was already submitted"
                    ) from exc
            elif existing["intent_id"] != document["intent_id"]:
                raise PaperStoreError("a submission cannot move to another intent")
            else:
                db.execute(
                    "UPDATE order_submissions SET status = ?, document_json = ?, "
                    "updated_at_utc = ? WHERE submission_id = ?",
                    (document["status"], _canonical(document), now, document["submission_id"]),
                )

    def unknown_submission_count(self) -> int:
        with self._connect() as db:
            row = db.execute(
                "SELECT count(*) FROM order_submissions WHERE status IN (?, ?)",
                OPEN_SUBMISSION_STATUSES,
            ).fetchone()
        return int(row[0])

    def submissions_between(self, start: datetime, end: datetime) -> int:
        """Submissions requested in [start, end), for a daily order count."""
        with self._connect() as db:
            row = db.execute(
                "SELECT count(*) FROM order_submissions "
                "WHERE requested_at_utc >= ? AND requested_at_utc < ?",
                (_utc(start), _utc(end)),
            ).fetchone()
        return int(row[0])

    def approval(self, approval: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT content_json FROM approvals WHERE approval_id = ?", (approval,)
            ).fetchone()
        return None if row is None else dict(json.loads(row["content_json"]))

    def submissions(self, *statuses: str) -> list[dict[str, Any]]:
        marks = ",".join("?" for _ in statuses)
        with self._connect() as db:
            rows = db.execute(
                f"SELECT document_json FROM order_submissions WHERE status IN ({marks}) "
                "ORDER BY requested_at_utc, submission_id",
                statuses,
            ).fetchall()
        return [dict(json.loads(row["document_json"])) for row in rows]

    def known_order_refs(self) -> set[str]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT json_extract(document_json, '$.broker_order_ref') FROM order_submissions"
            ).fetchall()
        return {str(row[0]) for row in rows if row[0] is not None}

    def accepted_without_execution(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT s.document_json FROM order_submissions s WHERE s.status = 'accepted' "
                "AND NOT EXISTS (SELECT 1 FROM paper_executions e WHERE "
                "json_extract(e.document_json, '$.broker_order_ref') = "
                "json_extract(s.document_json, '$.broker_order_ref') "
                "AND e.broker_account_ref = json_extract(s.document_json, '$.broker_account_ref'))"
                " ORDER BY s.requested_at_utc"
            ).fetchall()
        return [dict(json.loads(row["document_json"])) for row in rows]

    def position_quantity(self, broker_account_ref: str, instrument_id: str) -> Decimal:
        """Shares held from paper fills of one account (buys minus sells)."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT document_json FROM paper_executions "
                "WHERE broker_account_ref = ? AND instrument_id = ?",
                (broker_account_ref, instrument_id),
            ).fetchall()
        total = Decimal(0)
        for row in rows:
            document = json.loads(row["document_json"])
            quantity = Decimal(document["quantity"])
            total += quantity if document["side"] == "buy" else -quantity
        return total

    def record_execution(self, document: dict[str, Any]) -> bool:
        validate_document(document)
        with self._connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO paper_executions VALUES (?, ?, ?, ?, ?, ?)",
                (
                    document["execution_id"],
                    document["broker_id"],
                    document["broker_account_ref"],
                    document["instrument_id"],
                    _canonical(document),
                    document["synced_at_utc"],
                ),
            )
        return cursor.rowcount == 1

    def latest_approvals(self, limit: int = 20) -> list[tuple[str, str, dict[str, Any]]]:
        """(approval ID, approved at, content), newest first."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM approvals ORDER BY approved_at_utc DESC, approval_id LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            (row["approval_id"], row["approved_at_utc"], dict(json.loads(row["content_json"])))
            for row in rows
        ]

    def intents(self, limit: int = 100) -> list[dict[str, Any]]:
        """Newest intents with their outcome and submission, for the Agent screen."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT i.document_json AS intent, o.status, o.reason, s.document_json AS sub "
                "FROM order_intents i LEFT JOIN intent_outcomes o USING (intent_id) "
                "LEFT JOIN order_submissions s USING (intent_id) "
                "ORDER BY i.created_at_utc DESC, i.intent_id LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "intent": json.loads(row["intent"]),
                "outcome": None
                if row["status"] is None
                else {"status": row["status"], "reason": row["reason"]},
                "submission": None if row["sub"] is None else json.loads(row["sub"]),
            }
            for row in rows
        ]

    def executions(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT document_json FROM paper_executions "
                "ORDER BY synced_at_utc DESC, execution_id LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(json.loads(row["document_json"])) for row in rows]

    def verify_market(self, broker_id: str, market: str, evidence: str) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO market_verifications VALUES (?, ?, ?, ?)",
                (broker_id, market, evidence[:500], _utc(self._now())),
            )

    def verified_markets(self, broker_id: str) -> frozenset[str]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT market FROM market_verifications WHERE broker_id = ?", (broker_id,)
            ).fetchall()
        return frozenset(str(row["market"]) for row in rows)

    # Kill switch and arming

    def _event(
        self,
        db: sqlite3.Connection,
        event: str,
        source: str,
        reason: str | None = None,
        approval: str | None = None,
    ) -> None:
        db.execute(
            "INSERT INTO control_events (event, reason, source, approval_id, at_utc) "
            "VALUES (?, ?, ?, ?, ?)",
            (event, reason, source, approval, _utc(self._now())),
        )

    def halt(self, reason: str, source: str) -> None:
        """Kill switch: stop new orders and disarm. Resting orders are left alone."""
        reason = reason.strip()[:200] or "halted"
        with self._connect() as db:
            db.execute(
                "UPDATE control_state SET halted = 1, halted_reason = ?, armed = 0, "
                "updated_at_utc = ? WHERE id = 1",
                (reason, _utc(self._now())),
            )
            self._event(db, "halt", source, reason)

    def release(self, source: str) -> None:
        """Clear the kill switch. The runner stays disarmed until it is armed again."""
        with self._connect() as db:
            db.execute(
                "UPDATE control_state SET halted = 0, halted_reason = NULL, armed = 0, "
                "updated_at_utc = ? WHERE id = 1",
                (_utc(self._now()),),
            )
            self._event(db, "release", source)

    def arm(self, approval: str, source: str) -> None:
        with self._connect() as db:
            state = db.execute("SELECT halted FROM control_state WHERE id = 1").fetchone()
            if state["halted"]:
                raise PaperStoreError("the kill switch is on; release it before arming")
            if (
                db.execute("SELECT 1 FROM approvals WHERE approval_id = ?", (approval,)).fetchone()
                is None
            ):
                raise PaperStoreError("arming needs a recorded approval")
            db.execute(
                "UPDATE control_state SET armed = 1, approval_id = ?, updated_at_utc = ? "
                "WHERE id = 1",
                (approval, _utc(self._now())),
            )
            self._event(db, "arm", source, approval=approval)

    def disarm(self, source: str) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE control_state SET armed = 0, updated_at_utc = ? WHERE id = 1",
                (_utc(self._now()),),
            )
            self._event(db, "disarm", source)

    def heartbeat(self) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE control_state SET last_heartbeat_utc = ? WHERE id = 1",
                (_utc(self._now()),),
            )

    def may_submit(self, approval: str) -> tuple[bool, str | None]:
        """Checked again just before every submission."""
        with self._connect() as db:
            state = db.execute("SELECT * FROM control_state WHERE id = 1").fetchone()
        if state["halted"]:
            return False, "halted"
        if not state["armed"] or state["approval_id"] != approval:
            return False, "not_armed"
        if self.unknown_submission_count() > 0:
            return False, "unknown_submission_pending"
        return True, None

    def status(self) -> dict[str, Any]:
        with self._connect() as db:
            state = db.execute("SELECT * FROM control_state WHERE id = 1").fetchone()
        document = {
            "kind": "paper_runner_status",
            "schema_version": "0.1",
            "halted": bool(state["halted"]),
            "halted_reason": state["halted_reason"],
            "armed": bool(state["armed"]),
            "approval_id": state["approval_id"],
            "unknown_submission_count": self.unknown_submission_count(),
            "last_heartbeat_utc": state["last_heartbeat_utc"],
            "updated_at_utc": state["updated_at_utc"],
        }
        validate_document(document)
        return document
