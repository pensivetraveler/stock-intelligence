"""Strategy research: runs fix mistakes, and profit goes only when sent (ADR 0042 step 2)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from packages.contracts.validator import validate_document
from services.api.main import create_app
from services.assistant.indicator import MAX_RUNS
from services.review.store import ReviewStore
from tests.assistant.test_indicator_assistant_api import Scripted
from tests.review.test_conversation_api import answer, tool_use

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
BASE = "/v1/strategy-research/conversations"
CODE = '//@version=5\nstrategy("x")\nstrategy.entry("L", strategy.long)'
CONTEXT = {"symbol": "AAPL", "timeframe": "5m", "bars": 390, "code": None}
START = {
    "question": "EMA 돌파 전략",
    "language": "ko",
    "reference": "Supported: strategy.entry",
    "context": CONTEXT,
}
RESULTS: dict[str, Any] = {
    "symbol": "AAPL",
    "timeframe": "5m",
    "bars": 390,
    "from_utc": "2026-10-30T13:30:00Z",
    "to_utc": "2026-10-30T20:00:00Z",
    "version": 1,
    "currency": "USD",
    "initial_capital": "100000.00",
    "net_profit": "-1234.56",
    "net_profit_percent": "-1.23",
    "gross_profit": "100.00",
    "gross_loss": "1334.56",
    "profit_factor": "0.075",
    "closed_trades": 8,
    "win_trades": 2,
    "loss_trades": 6,
    "win_rate": "25.00",
    "max_drawdown": "1500.00",
    "commission": "0.00",
    "assumed_fills": 1,
    "skipped_orders": 0,
    "open_position": None,
    "concentration": {
        "days_with_trades": 3,
        "top_days": [{"date": "2026-10-30", "trades": 6}, {"date": "2026-10-29", "trades": 1}],
        "top3_share_percent": "87.50",
    },
    "trades": [
        {
            "direction": "long",
            "qty": "10",
            "entry_time_utc": "2026-10-30T13:35:00Z",
            "entry_price": "100.50",
            "exit_time_utc": "2026-10-30T14:00:00Z",
            "exit_price": "99.00",
            "profit": "-15.00",
            "assumed": False,
        }
    ],
}


def run(tool_id: str, code: str = CODE) -> dict[str, Any]:
    return tool_use((tool_id, "run_pine", {"code": code, "explanation": "first try"}))


def make_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: Scripted, key: str | None
) -> Iterator[TestClient]:
    monkeypatch.delenv("SIW_DATA_DIR", raising=False)
    import services.api.main as main

    original = main._claude_key
    main._claude_key = lambda _credentials: key  # type: ignore[assignment]
    app = create_app(
        review_store=ReviewStore(tmp_path / "review.sqlite3", lambda: NOW),
        strategy_converse=script,
        clock=lambda: NOW,
    )
    try:
        with TestClient(app) as client:
            yield client
    finally:
        main._claude_key = original  # type: ignore[assignment]


@pytest.fixture
def scripted() -> Scripted:
    return Scripted()


@pytest.fixture
def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scripted: Scripted
) -> Iterator[TestClient]:
    yield from make_client(tmp_path, monkeypatch, scripted, "test-key-not-real")


def answered_with_run(client: TestClient, scripted: Scripted) -> str:
    """A conversation whose one script ran; the run's backtest stays with the app."""
    scripted.turns += [run("t1"), answer("작성했습니다.")]
    started = client.post(BASE, json=START).json()
    posted = client.post(
        f"{BASE}/{started['conversation_id']}/runs",
        json={
            "tool_use_id": "t1",
            "ok": True,
            "summary": "Closed trades: 8; open position: no.",
            "results": RESULTS,
        },
    )
    assert posted.status_code == 200, posted.text
    return str(started["conversation_id"])


def test_a_request_uses_the_assigned_model_and_a_strategy_prompt(
    client: TestClient, scripted: Scripted
) -> None:
    scripted.turns += [run("t1")]
    response = client.post(BASE, json=START)
    assert response.status_code == 201, response.text
    document = response.json()
    validate_document(document)
    assert document["kind"] == "strategy_conversation"
    assert document["pending_run"]["tool_use_id"] == "t1"
    first = scripted.calls[0]
    assert (first["model"], first["effort"]) == ("claude-sonnet-5-5", "high")
    assert "Write strategy() scripts" in first["system"]
    assert "Supported: strategy.entry" in first["system"]
    assert "no recommendation to buy or sell" in first["system"]


def test_a_runs_backtest_is_kept_but_never_reaches_claude(
    client: TestClient, scripted: Scripted
) -> None:
    conversation = answered_with_run(client, scripted)
    document = client.get(f"{BASE}/{conversation}").json()
    validate_document(document)
    (run_event,) = [event for event in document["events"] if event["type"] == "run"]
    assert run_event["results"]["net_profit"] == "-1234.56"
    sent = json.dumps(scripted.calls[-1]["messages"], ensure_ascii=False)
    assert "Closed trades: 8" in sent
    assert "-1234.56" not in sent
    assert "win_rate" not in sent


def test_results_are_sent_with_the_versions_tested(client: TestClient, scripted: Scripted) -> None:
    conversation = answered_with_run(client, scripted)
    scripted.turns += [answer("근거: 거래 8건은 적습니다.")]
    response = client.post(
        f"{BASE}/{conversation}/results",
        json={"question": "검토해 주세요", "context": CONTEXT, "results": RESULTS},
    )
    assert response.status_code == 200, response.text
    document = response.json()
    validate_document(document)
    last = scripted.calls[-1]
    assert last["effort"] == "high"
    blocks = last["messages"][-1]["content"]
    sent = json.loads(next(b["text"] for b in blocks if "backtest_results" in b.get("text", "")))
    assert sent["backtest_results"]["net_profit"] == "-1234.56"
    assert sent["backtest_results"]["trades"][0]["profit"] == "-15.00"
    assert sent["backtest_results"]["concentration"]["top3_share_percent"] == "87.50"
    assert sent["versions_tested_in_this_conversation"] == 1
    question = next(e for e in reversed(document["events"]) if e["type"] == "question")
    assert question["results"]["closed_trades"] == 8


def test_results_count_only_versions_that_ran(client: TestClient, scripted: Scripted) -> None:
    scripted.turns += [run("t1"), run("t2"), answer("완료"), answer("검토")]
    started = client.post(BASE, json=START).json()
    path = f"{BASE}/{started['conversation_id']}"
    client.post(f"{path}/runs", json={"tool_use_id": "t1", "ok": False, "error": "line 1"})
    client.post(f"{path}/runs", json={"tool_use_id": "t2", "ok": True, "summary": "ran"})
    client.post(
        f"{path}/results", json={"question": "검토", "context": CONTEXT, "results": RESULTS}
    )
    blocks = scripted.calls[-1]["messages"][-1]["content"]
    text = next(b["text"] for b in blocks if "backtest_results" in b.get("text", ""))
    assert json.loads(text)["versions_tested_in_this_conversation"] == 1


def test_a_script_left_unrun_is_answered_before_results(
    client: TestClient, scripted: Scripted
) -> None:
    scripted.turns += [run("t1"), answer("검토")]
    started = client.post(BASE, json=START).json()
    response = client.post(
        f"{BASE}/{started['conversation_id']}/results",
        json={"question": "검토", "context": CONTEXT, "results": RESULTS},
    )
    assert response.status_code == 200, response.text
    assert scripted.calls[-1]["messages"][-1]["content"][0]["tool_use_id"] == "t1"


@pytest.mark.parametrize(
    "change",
    [
        {"trades": [RESULTS["trades"][0]] * 51},
        {"net_profit": "lots"},
        {"account": "12345678"},
        {"concentration": None},
    ],
)
def test_bad_results_are_refused_before_claude(
    client: TestClient, scripted: Scripted, change: dict[str, Any]
) -> None:
    conversation = answered_with_run(client, scripted)
    calls = len(scripted.calls)
    response = client.post(
        f"{BASE}/{conversation}/results",
        json={"question": "검토", "context": CONTEXT, "results": {**RESULTS, **change}},
    )
    assert response.status_code == 422
    assert len(scripted.calls) == calls


def test_a_retry_alone_goes_to_opus_at_high_effort(client: TestClient, scripted: Scripted) -> None:
    conversation = answered_with_run(client, scripted)
    scripted.turns += [answer("Opus"), answer("다시 Sonnet")]
    path = f"{BASE}/{conversation}/messages"
    client.post(path, json={"question": "다시", "context": CONTEXT, "retry": True})
    assert (scripted.calls[-1]["model"], scripted.calls[-1]["effort"]) == (
        "claude-opus-5-5",
        "high",
    )
    client.post(path, json={"question": "다음", "context": CONTEXT})
    assert scripted.calls[-1]["model"] == "claude-sonnet-5-5"


def test_the_run_limit_stops_and_shows_the_error(client: TestClient, scripted: Scripted) -> None:
    scripted.turns += [run(f"t{i}") for i in range(MAX_RUNS)]
    started = client.post(BASE, json=START).json()
    document = started
    for i in range(MAX_RUNS):
        document = client.post(
            f"{BASE}/{started['conversation_id']}/runs",
            json={"tool_use_id": f"t{i}", "ok": False, "error": "line 2: nope"},
        ).json()
    assert document["status"] == "stopped"


def test_conversations_do_not_cross_with_the_indicator_assistant(
    client: TestClient, scripted: Scripted
) -> None:
    conversation = answered_with_run(client, scripted)
    crossed = client.get(f"/v1/indicator-assistant/conversations/{conversation}")
    assert crossed.status_code == 404


def test_missing_key_and_unknown_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scripted: Scripted, client: TestClient
) -> None:
    assert client.get(f"{BASE}/nope").status_code == 404
    for keyless in make_client(tmp_path / "other", monkeypatch, scripted, None):
        response = keyless.post(BASE, json=START)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "agent_key_missing"
