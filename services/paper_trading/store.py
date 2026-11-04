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
from typing import Any, Literal

from packages.contracts.validator import validate_document
from services.market_data.storage import MigrationRunner
from services.paper_trading.brokers.base import SubmitResult
from services.paper_trading.limits import LimitResult
from services.paper_trading.protection import ProtectionState

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
    # The live ledger (ADR 0047) is the same store in another file; a document of the
    # other environment is refused, so paper and real orders never share a ledger.
    database_name = DATABASE_NAME
    environment: Literal["demo", "real"] = "demo"
    execution_ledger = "broker_paper"

    def __init__(self, root: Path, *, now: Callable[[], datetime] | None = None) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database_path = self.root / self.database_name
        self._now = now or (lambda: datetime.now(timezone.utc))
        MigrationRunner(self.database_path, MIGRATIONS_DIR).apply()

    def _own(self, document: dict[str, Any]) -> None:
        if (
            document.get("environment") != self.environment
            or document.get("ledger", self.execution_ledger) != self.execution_ledger
        ):
            raise PaperStoreError(
                f"a {document.get('environment')} document does not belong in {self.database_name}"
            )

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

    def record_intent(
        self, document: dict[str, Any], result: LimitResult, *, blocking: bool = True
    ) -> bool:
        """Store an intent once. False if the same intent was already recorded.

        An intent that failed its limit check is stored as blocked, with its reasons. With
        `blocking` off (a protective exit, ADR 0044) the reasons are kept and it goes on.
        """
        validate_document(document)
        self._own(document)
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
                if blocking:
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
        self._own(document)
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

    def record_cancel_attempt(
        self, submission_id: str, requested_at: datetime, result: SubmitResult
    ) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO order_cancel_attempts (submission_id, requested_at_utc, status, "
                "cancel_order_ref, response_code, response_message) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    submission_id,
                    _utc(requested_at),
                    result.status,
                    result.broker_order_ref,
                    result.response_code,
                    result.response_message,
                ),
            )

    def cancel_attempts(self, submission_id: str) -> list[str]:
        """When the runner asked to cancel this order, oldest first."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT requested_at_utc FROM order_cancel_attempts WHERE submission_id = ? "
                "ORDER BY attempt_id",
                (submission_id,),
            ).fetchall()
        return [str(row["requested_at_utc"]) for row in rows]

    # Confirmations of live orders (ADR 0047)

    def request_confirmation(self, intent_id: str, expires_at: datetime) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO order_confirmations "
                "(intent_id, requested_at_utc, expires_at_utc) VALUES (?, ?, ?)",
                (intent_id, _utc(self._now()), _utc(expires_at)),
            )

    def decide_confirmation(self, intent_id: str, decision: str, source: str) -> None:
        """The user's answer. Only a request still open and not expired takes one."""
        if decision not in ("confirmed", "declined"):
            raise ValueError("decision must be confirmed or declined")
        now = self._now()
        with self._connect() as db:
            row = db.execute(
                "SELECT c.expires_at_utc, c.decision, o.status FROM order_confirmations c "
                "LEFT JOIN intent_outcomes o USING (intent_id) WHERE c.intent_id = ?",
                (intent_id,),
            ).fetchone()
            if row is None:
                raise PaperStoreError("no order waits for this confirmation")
            if row["decision"] is not None or row["status"] is not None:
                raise PaperStoreError("this order was already decided")
            if _utc(now) >= row["expires_at_utc"]:
                raise PaperStoreError("the time to confirm this order has passed")
            db.execute(
                "UPDATE order_confirmations SET decision = ?, decided_at_utc = ?, source = ? "
                "WHERE intent_id = ?",
                (decision, _utc(now), source[:40], intent_id),
            )

    def open_confirmations(self) -> list[dict[str, Any]]:
        """Requests whose intent has no outcome yet, oldest first."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT c.*, i.document_json FROM order_confirmations c "
                "JOIN order_intents i USING (intent_id) "
                "WHERE NOT EXISTS (SELECT 1 FROM intent_outcomes o WHERE o.intent_id = c.intent_id)"
                " ORDER BY c.requested_at_utc, c.intent_id"
            ).fetchall()
        return [
            {
                "intent": dict(json.loads(row["document_json"])),
                "requested_at_utc": row["requested_at_utc"],
                "expires_at_utc": row["expires_at_utc"],
                "decision": row["decision"],
                "decided_at_utc": row["decided_at_utc"],
            }
            for row in rows
        ]

    # Protective states (ADR 0044 G2)

    def open_protection(self, broker_account_ref: str, state: ProtectionState) -> None:
        """Keep a new position's levels; an open one for this instrument stays in the way."""
        try:
            with self._connect() as db:
                db.execute(
                    "INSERT INTO protection_states VALUES (?, ?, ?, 'open', ?, NULL, ?)",
                    (
                        state.intent_id,
                        broker_account_ref,
                        state.instrument_id,
                        state.to_json(),
                        _utc(self._now()),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise PaperStoreError(
                f"{state.instrument_id} already has protection, or this entry had it before"
            ) from exc

    def active_protection(
        self, broker_account_ref: str, instrument_id: str
    ) -> ProtectionState | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT state_json FROM protection_states WHERE broker_account_ref = ? "
                "AND instrument_id = ? AND status = 'open'",
                (broker_account_ref, instrument_id),
            ).fetchone()
        return None if row is None else ProtectionState.from_json(row["state_json"])

    def save_protection(self, state: ProtectionState) -> None:
        """Store the state after a bar. A stop never falls and bars never go back."""
        with self._connect() as db:
            row = db.execute(
                "SELECT state_json FROM protection_states WHERE intent_id = ? AND status = 'open'",
                (state.intent_id,),
            ).fetchone()
            if row is None:
                raise PaperStoreError(f"no open protection for intent {state.intent_id}")
            before = ProtectionState.from_json(row["state_json"])
            if before.levels.stop != state.levels.stop or before.levels.target != (
                state.levels.target
            ):
                raise PaperStoreError("a position's stop and target are set at entry")
            if (
                before.levels.trail_stop is not None
                and state.levels.trail_stop is not None
                and state.levels.trail_stop < before.levels.trail_stop
            ):
                raise PaperStoreError("a trailing stop cannot move down")
            if before.last_bar_time is not None and (
                state.last_bar_time is None or state.last_bar_time < before.last_bar_time
            ):
                raise PaperStoreError("protection cannot go back to an earlier bar")
            db.execute(
                "UPDATE protection_states SET state_json = ?, updated_at_utc = ? "
                "WHERE intent_id = ?",
                (state.to_json(), _utc(self._now()), state.intent_id),
            )

    def close_protection(self, intent_id: str, reason: str) -> None:
        with self._connect() as db:
            updated = db.execute(
                "UPDATE protection_states SET status = 'closed', closed_reason = ?, "
                "updated_at_utc = ? WHERE intent_id = ? AND status = 'open'",
                (reason, _utc(self._now()), intent_id),
            )
            if updated.rowcount != 1:
                raise PaperStoreError(f"no open protection for intent {intent_id}")

    def latest_buy(self, broker_account_ref: str, instrument_id: str) -> dict[str, Any] | None:
        """The newest paper buy fill of one account and instrument."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT document_json FROM paper_executions "
                "WHERE broker_account_ref = ? AND instrument_id = ? "
                "ORDER BY synced_at_utc DESC, execution_id",
                (broker_account_ref, instrument_id),
            ).fetchall()
        documents = (dict(json.loads(row["document_json"])) for row in rows)
        return next((item for item in documents if item["side"] == "buy"), None)

    def positions(self) -> list[dict[str, Any]]:
        """Shares held from paper fills, each with its protection, for the Agent screen.

        `watched`: the runner's plan is the strategy that opened this protection, and it
        runs (armed, or under the kill switch, which still sends protective exits).
        """
        with self._connect() as db:
            rows = db.execute("SELECT document_json FROM paper_executions").fetchall()
        held: dict[tuple[str, str], tuple[str, Decimal]] = {}
        for row in rows:
            fill = json.loads(row["document_json"])
            key = (fill["broker_account_ref"], fill["instrument_id"])
            quantity = Decimal(fill["quantity"])
            before = held.get(key, (fill["symbol"], Decimal(0)))[1]
            change = quantity if fill["side"] == "buy" else -quantity
            held[key] = (fill["symbol"], before + change)
        status = self.status()
        running = status["armed"] or status["halted"]
        plan = self.approval(status["approval_id"]) if status["approval_id"] and running else None
        positions = []
        for (account, instrument), (symbol, quantity) in sorted(held.items()):
            if quantity <= 0:
                continue
            state = self.active_protection(account, instrument)
            entry = None if state is None else self.intent(state.intent_id)
            watched = (
                plan is not None
                and entry is not None
                and plan["broker_account_ref"] == account
                and plan["strategy"]["definition_sha256"] == entry["strategy"]["definition_sha256"]
                and any(item["instrument_id"] == instrument for item in plan["instruments"])
            )
            positions.append(
                {
                    "broker_account_ref": account,
                    "instrument_id": instrument,
                    "symbol": symbol,
                    "quantity": format(quantity.normalize(), "f"),
                    "protection": None if state is None else state.document(),
                    "watched": watched,
                }
            )
        return positions

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
        self._own(document)
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

    def may_exit(self, approval: str) -> tuple[bool, str | None]:
        """A protective exit: the kill switch does not stop it (ADR 0044, decision 1). Only
        disarming or releasing does, which is how the user stops every order."""
        with self._connect() as db:
            state = db.execute("SELECT * FROM control_state WHERE id = 1").fetchone()
        if state["approval_id"] != approval or not (state["armed"] or state["halted"]):
            return False, "not_armed"
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
