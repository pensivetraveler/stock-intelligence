from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from services.paper_trading.__main__ import main as cli_main
from services.paper_trading.limits import LimitResult
from services.paper_trading.store import PaperOrderStore, PaperStoreError

FIXTURES = Path("tests/fixtures/contracts/valid")
NOW = datetime(2026, 10, 23, 14, 31, 10, tzinfo=timezone.utc)
PASSED = LimitResult(True, (), None)


def _fixture(name: str) -> dict[str, Any]:
    return dict(json.loads((FIXTURES / name).read_text(encoding="utf-8")))


def _store(tmp_path: Path) -> tuple[PaperOrderStore, str, dict[str, Any]]:
    store = PaperOrderStore(tmp_path, now=lambda: NOW)
    approval = store.record_approval({"strategy": "strat-sma-cross@3", "broker_id": "kiwoom"})
    intent = _fixture("paper-order-intent-limit.json")
    intent["approval_id"] = approval
    return store, approval, intent


def test_store_uses_its_own_file_only(tmp_path: Path) -> None:
    store, _, intent = _store(tmp_path)
    store.record_intent(intent, PASSED)
    assert sorted(path.name for path in tmp_path.iterdir()) == ["paper_orders.sqlite3"]


def test_intent_is_recorded_once_and_cannot_change(tmp_path: Path) -> None:
    store, _, intent = _store(tmp_path)
    assert store.record_intent(intent, PASSED)
    assert not store.record_intent(intent, PASSED)
    assert store.intent(intent["intent_id"]) == intent
    with sqlite3.connect(store.database_path) as db, pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE order_intents SET instrument_id = 'x'")
    with sqlite3.connect(store.database_path) as db, pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM order_intents")


def test_blocked_intent_keeps_its_reasons(tmp_path: Path) -> None:
    store, _, intent = _store(tmp_path)
    store.record_intent(intent, LimitResult(False, ("order_amount_exceeded",), None))
    assert store.outcome(intent["intent_id"]) == ("blocked", "order_amount_exceeded")
    with pytest.raises(PaperStoreError):
        store.set_outcome(intent["intent_id"], "submitted")


def test_an_intent_is_submitted_at_most_once(tmp_path: Path) -> None:
    store, _, intent = _store(tmp_path)
    store.record_intent(intent, PASSED)
    submission = _fixture("paper-order-submission.json")
    submission["intent_id"] = intent["intent_id"]
    store.record_submission(submission)
    store.record_submission(submission)  # the same submission updated
    second = {**submission, "submission_id": "sub-bbbbbbbbbbbb"}
    with pytest.raises(PaperStoreError):
        store.record_submission(second)


def test_kill_switch_stops_new_orders_and_needs_rearming(tmp_path: Path) -> None:
    store, approval, _ = _store(tmp_path)
    assert store.may_submit(approval) == (False, "not_armed")
    store.arm(approval, source="test")
    assert store.may_submit(approval) == (True, None)

    store.halt("kill switch pressed", source="test")
    assert store.may_submit(approval) == (False, "halted")
    with pytest.raises(PaperStoreError):
        store.arm(approval, source="test")

    store.release(source="test")
    assert store.may_submit(approval) == (False, "not_armed")
    store.arm(approval, source="test")
    assert store.may_submit(approval) == (True, None)
    assert store.status()["armed"] is True


def test_arming_needs_a_recorded_approval(tmp_path: Path) -> None:
    store, _, _ = _store(tmp_path)
    with pytest.raises(PaperStoreError):
        store.arm("f" * 64, source="test")


def test_unknown_submission_blocks_new_orders(tmp_path: Path) -> None:
    store, approval, intent = _store(tmp_path)
    store.record_intent(intent, PASSED)
    store.arm(approval, source="test")
    submission = _fixture("paper-order-submission.json")
    submission.update(
        intent_id=intent["intent_id"],
        status="unknown",
        broker_order_ref=None,
        response_code=None,
        response_message=None,
    )
    store.record_submission(submission)
    assert store.may_submit(approval) == (False, "unknown_submission_pending")
    assert store.status()["unknown_submission_count"] == 1


def test_paper_execution_is_stored_once(tmp_path: Path) -> None:
    store, _, _ = _store(tmp_path)
    execution = _fixture("paper-execution.json")
    assert store.record_execution(execution)
    assert not store.record_execution(execution)


def test_cli_halt_works_without_the_app(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_main(["halt", "--reason", "manual stop", "--data-dir", str(tmp_path)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["halted"] is True
    assert status["halted_reason"] == "manual stop"
    assert cli_main(["release", "--data-dir", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["armed"] is False
