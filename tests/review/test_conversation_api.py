"""Follow-up questions with data requests (ADR 0042, step 2b)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from packages.contracts.validator import validate_document
from services.api.main import create_app
from services.journal.store import JournalStore
from services.review.conversation import call_claude
from services.review.store import ReviewStore
from services.review.tools import ToolContext, ToolInputError
from services.review.trades import build_trades, fills_of
from tests.agents.test_review_agent import broker
from tests.backtest.test_backtest_api import RULES
from tests.review.test_report_api import PNG, Asked, bars

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
TRADE = "flat:broker:brk-1"
EXIT = "2026-03-03T18:00:00Z"


def trade_documents() -> list[dict[str, Any]]:
    documents = []
    for order_id, side, price, when in [
        ("1", "buy", "100", "2026-03-03T15:00:00Z"),
        ("2", "buy", "101", "2026-03-03T16:00:00Z"),
        ("3", "sell", "103", EXIT),
    ]:
        document = broker(order_id, side, price, when, "kiwoom-a")
        if order_id == "3":
            document["quantity"] = "20"
        documents.append(document)
    return documents


class Scripted:
    """Claude's turns, in order; records every request's messages."""

    def __init__(self, *turns: dict[str, Any]) -> None:
        self.turns = list(turns)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, model: str, system: str, tools: list[Any], messages: list[Any], key: str
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "model": model,
                "system": system,
                "tools": tools,
                "messages": json.loads(json.dumps(messages)),
            }
        )
        return self.turns.pop(0)


def tool_use(*calls: tuple[str, str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "model": "claude-sonnet-5-5",
        "stop_reason": "tool_use",
        "content": [
            {"type": "thinking", "thinking": "", "signature": "sig-1"},
            *[{"type": "tool_use", "id": i, "name": n, "input": inp} for i, n, inp in calls],
        ],
        "usage": {"input_tokens": 100, "output_tokens": 10},
    }


def answer(text: str) -> dict[str, Any]:
    return {
        "model": "claude-sonnet-5-5",
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 120, "output_tokens": 20},
    }


@pytest.fixture
def make(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    monkeypatch.delenv("SIW_DATA_DIR", raising=False)
    import services.api.main as main

    original_service, original_key = main.MarketBarsService, main._claude_key

    class Synthetic:
        def __init__(self, _provider: object) -> None:
            pass

        def get_bars(self, query: Any) -> Any:
            return bars(query)

    main.MarketBarsService = Synthetic  # type: ignore[assignment,misc]
    main._claude_key = lambda _credentials: "test-key-not-real"  # type: ignore[assignment]
    clients: list[TestClient] = []

    def build(script: Scripted) -> TestClient:
        journal = JournalStore(tmp_path / "journal")
        for document in trade_documents():
            journal.record_broker_execution(document)
        app = create_app(
            journal_store=journal,
            review_store=ReviewStore(tmp_path / "review.sqlite3", lambda: NOW),
            review_ask=Asked(),
            review_converse=script,
            clock=lambda: NOW,
        )
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    try:
        yield build
    finally:
        for client in clients:
            client.__exit__(None, None, None)
        main.MarketBarsService = original_service  # type: ignore[misc]
        main._claude_key = original_key  # type: ignore[assignment]


def report(client: TestClient) -> str:
    draft = client.post(f"/v1/review/trades/{TRADE}/report-drafts", json={}).json()
    images = [{"kind": i["kind"], "png_base64": PNG} for i in draft["images"]]
    sent = client.post(
        f"/v1/review/report-drafts/{draft['draft_id']}/report", json={"images": images}
    )
    assert sent.status_code == 201, sent.text
    report_id: str = sent.json()["report_id"]
    return report_id


def ask(client: TestClient, report_id: str, question: str) -> dict[str, Any]:
    response = client.post(
        f"/v1/review/reports/{report_id}/conversation/messages", json={"question": question}
    )
    assert response.status_code == 200, response.text
    document: dict[str, Any] = response.json()
    validate_document(document)
    return document


def test_a_data_request_then_an_answer_with_an_append_only_history(make: Any) -> None:
    script = Scripted(
        tool_use(
            (
                "t1",
                "get_bars",
                {
                    "symbol": "SPY",
                    "timeframe": "5m",
                    "start_date": "2026-03-03",
                    "end_date": "2026-03-03",
                },
            )
        ),
        answer("SPY는 그날 꾸준히 올랐습니다."),
        answer("두 번째 답"),
    )
    client = make(script)
    report_id = report(client)
    assert client.get(f"/v1/review/reports/{report_id}/conversation").json()["events"] == []
    document = ask(client, report_id, "그날 SPY는?")
    assert document["status"] == "idle"
    assert [e["type"] for e in document["events"]] == ["question", "request", "answer"]
    assert document["events"][1]["summary"].startswith("us:spy:arcx:2026 5m")
    assert document["usage"] == {
        "input_tokens": 220,
        "output_tokens": 30,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    first = script.calls[0]["messages"]
    # The report's evidence: its three images (same bytes), the facts, the review, the question.
    kinds = [block["type"] for block in first[0]["content"]]
    assert kinds.count("image") == 3
    assert first[0]["content"][-1] == {"type": "text", "text": "그날 SPY는?"}
    assert "your_review" in first[0]["content"][-2]["text"]
    assert "kiwoom" not in json.dumps(first)
    # The tool result stops at the exit: every listed bar ends by 18:00Z.
    result = script.calls[1]["messages"][2]["content"][0]
    data = json.loads(result["content"])
    assert data["hindsight_bars"] == 0
    assert all(row[0] < EXIT for row in data["bars"])
    assert data["bars_listed"] <= 100
    # A second question replays every earlier message unchanged, thinking blocks included.
    ask(client, report_id, "또 다른 질문")
    later = script.calls[2]["messages"]
    assert later[: len(script.calls[1]["messages"])] == script.calls[1]["messages"]
    assert later[1]["content"][0] == {"type": "thinking", "thinking": "", "signature": "sig-1"}
    assert script.calls[0]["system"] == script.calls[2]["system"]
    assert script.calls[0]["tools"] == script.calls[2]["tools"]


def test_chart_images_pause_the_answer_until_the_app_draws_them(make: Any) -> None:
    script = Scripted(
        tool_use(
            (
                "c1",
                "chart_image",
                {
                    "symbol": "this",
                    "timeframe": "5m",
                    "start_date": "2026-03-03",
                    "end_date": "2026-03-03",
                },
            )
        ),
        answer("차트를 봤습니다."),
    )
    client = make(script)
    report_id = report(client)
    document = ask(client, report_id, "진입 전후 차트를 봐줘")
    assert document["status"] == "needs_images"
    (pending,) = document["pending_images"]
    assert pending["tool_use_id"] == "c1"
    assert all(bar["interval_end_utc"] <= EXIT for bar in pending["bars"])
    assert [m["side"] for m in pending["markers"]] == ["buy", "buy"]  # the sell is at the cut
    # Asking again before the image arrives is refused.
    busy = client.post(
        f"/v1/review/reports/{report_id}/conversation/messages", json={"question": "또"}
    )
    assert busy.json()["error"]["code"] == "conversation_busy"
    done = client.post(
        f"/v1/review/conversations/{document['conversation_id']}/images",
        json={"images": [{"tool_use_id": "c1", "png_base64": PNG}]},
    ).json()
    assert done["status"] == "idle"
    assert done["pending_images"] == []
    result = script.calls[1]["messages"][2]["content"][0]
    assert result["tool_use_id"] == "c1"
    assert [block["type"] for block in result["content"]] == ["image", "text"]


def test_later_bars_are_flagged_and_the_request_limit_holds(make: Any) -> None:
    later = {
        "symbol": "this",
        "timeframe": "1d",
        "start_date": "2026-03-01",
        "end_date": "2026-03-20",
        "include_after_exit": True,
    }
    script = Scripted(
        tool_use(*[(f"t{i}", "get_bars", later) for i in range(7)]),
        answer("끝"),
    )
    client = make(script)
    report_id = report(client)
    document = ask(client, report_id, "청산 뒤 흐름은?")
    requests = [e for e in document["events"] if e["type"] == "request"]
    assert len(requests) == 7
    assert requests[0]["hindsight"] is True
    assert requests[6]["error"] is True
    results = script.calls[1]["messages"][2]["content"]
    data = json.loads(results[0]["content"])
    assert data["hindsight_bars"] > 0
    assert any(row[6] for row in data["bars"])
    assert results[6]["is_error"] is True


def test_bad_requests_go_back_to_claude_as_errors(make: Any) -> None:
    script = Scripted(
        tool_use(
            (
                "t1",
                "get_bars",
                {
                    "symbol": "NOPE",
                    "timeframe": "1d",
                    "start_date": "2026-01-01",
                    "end_date": "2026-02-01",
                },
            ),
            ("t2", "strategy_signals", {"timeframe": "1d", "at": "entry"}),
        ),
        answer("자료가 없어 판단할 수 없습니다."),
    )
    client = make(script)
    report_id = report(client)
    document = ask(client, report_id, "질문")
    results = script.calls[1]["messages"][2]["content"]
    assert [r["is_error"] for r in results] == [True, True]
    assert "unknown symbol" in results[0]["content"]
    assert "no linked strategy" in results[1]["content"]
    assert document["status"] == "idle"


def test_strategy_signals_and_indicators_stop_at_the_entry() -> None:
    fills, _ = fills_of("broker", trade_documents())
    (trade,) = build_trades(fills, {}, {}, {})
    strategy = {"strategy_id": "s1", "name": "Cross", "strategy_type": "rules", "definition": RULES}
    context = ToolContext(bars, trade, NOW, lambda _s: None, lambda: [strategy])
    signals = context.run(
        "strategy_signals", {"strategy_id": "s1", "timeframe": "1d", "at": "entry"}
    )
    assert signals.data is not None
    rows = signals.data["last_bars"]
    assert len(rows) == 5
    assert all(row["bar_start_utc"] < "2026-03-03T15:00:00Z" for row in rows)
    indicator = context.run(
        "get_indicator",
        {
            "symbol": "this",
            "timeframe": "5m",
            "indicator": "rsi",
            "params": {"length": 14},
            "at": "entry",
        },
    )
    assert indicator.data is not None
    assert indicator.data["bar_start_utc"] < "2026-03-03T15:00:00Z"
    assert set(indicator.data["values"]) == {"value"}
    listed = context.run("list_strategies", {})
    assert listed.data == {
        "strategies": [
            {
                "strategy_id": "s1",
                "name": "Cross",
                "type": "rules",
                "linked_to_this_trade": False,
                "definition": RULES,
            }
        ]
    }
    with pytest.raises(ToolInputError, match="at most 5 sessions"):
        context.run(
            "get_bars",
            {
                "symbol": "this",
                "timeframe": "5m",
                "start_date": "2026-02-01",
                "end_date": "2026-03-03",
            },
        )


def test_call_claude_sends_adaptive_thinking_with_binding_and_tools() -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json=answer("ok"))

    message = call_claude(
        "claude-sonnet-5-5",
        "system",
        [{"name": "get_bars", "input_schema": {"type": "object"}}],
        [{"role": "user", "content": "q"}],
        "k",
        transport=httpx.MockTransport(handler),
    )
    assert message["stop_reason"] == "end_turn"
    body = json.loads(sent[0].content)
    assert body["thinking"] == {
        "type": "adaptive",
        "block_binding": {"prefix_mismatch_behavior": "drop_block"},
    }
    assert sent[0].headers["anthropic-beta"] == "thinking-binding-controls-2026-08-01"
    assert body["cache_control"] == {"type": "ephemeral"}
    assert body["tools"][0]["name"] == "get_bars"
