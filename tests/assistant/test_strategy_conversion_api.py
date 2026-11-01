"""Pine to rules: nothing dropped, nothing invented, nothing saved (ADR 0042 step 3)."""

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
from services.assistant.conversion import (
    SAVE_OPEN,
    call_convert_claude,
    check_mapping,
    rule_parts,
    system_prompt,
)
from services.review.store import ReviewStore
from tests.assistant.test_indicator_assistant_api import Scripted
from tests.review.test_conversation_api import answer

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
BASE = "/v1/strategy-research/conversations"
CODE = '//@version=5\nstrategy("x")'
CONDITIONS = ["ta.crossover(f, s)", "ta.crossunder(f, s)", "quantity: the default of 1 share"]
CROSS_UP = {
    "op": "cross_above",
    "left": {"indicator": "ema", "params": {"length": 5}},
    "right": {"indicator": "ema", "params": {"length": 20}},
}
CROSS_DOWN = {**CROSS_UP, "op": "cross_below"}
DEFINITION = {
    "type": "rules",
    "entry": CROSS_UP,
    "exit": CROSS_DOWN,
    "sizing": {"mode": "fixed_shares", "value": 1},
    "max_positions": 1,
}
MAPPED = [
    {"index": 0, "status": "mapped", "rule_paths": ["/entry"], "reason": ""},
    {"index": 1, "status": "mapped", "rule_paths": ["/exit"], "reason": ""},
    {"index": 2, "status": "mapped", "rule_paths": ["/sizing"], "reason": ""},
]


def reply(
    definition: object = DEFINITION, mapping: Any = None, notes: str = "ok"
) -> dict[str, Any]:
    source = definition if isinstance(definition, str) else json.dumps(definition)
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "definition_json": source,
                        "mapping": MAPPED if mapping is None else mapping,
                        "notes": notes,
                    }
                ),
            }
        ],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }


class Converter:
    def __init__(self) -> None:
        self.replies: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []

    def __call__(self, system: str, messages: list[Any], key: str) -> dict[str, Any]:
        self.calls.append({"system": system, "messages": list(messages), "key": key})
        return self.replies.pop(0)


@pytest.fixture
def converter() -> Converter:
    return Converter()


@pytest.fixture
def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, converter: Converter
) -> Iterator[TestClient]:
    monkeypatch.delenv("SIW_DATA_DIR", raising=False)
    import services.api.main as main

    original = main._claude_key
    main._claude_key = lambda _credentials: "test-key-not-real"  # type: ignore[assignment]
    script = Scripted()
    script.turns += [answer("좋습니다"), answer("좋습니다")]
    app = create_app(
        review_store=ReviewStore(tmp_path / "review.sqlite3", lambda: NOW),
        strategy_converse=script,
        strategy_convert=converter,
        clock=lambda: NOW,
    )
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        main._claude_key = original  # type: ignore[assignment]


def conversation(client: TestClient) -> str:
    started = client.post(
        BASE,
        json={
            "question": "전략",
            "reference": "Supported: strategy.entry",
            "context": {"symbol": "AAPL", "timeframe": "5m", "bars": 10, "code": None},
        },
    )
    assert started.status_code == 201, started.text
    return str(started.json()["conversation_id"])


def convert(client: TestClient, conditions: list[str] | None = None) -> dict[str, Any]:
    response = client.post(
        f"{BASE}/{conversation(client)}/convert",
        json={"code": CODE, "conditions": conditions or CONDITIONS, "language": "ko"},
    )
    assert response.status_code == 200, response.text
    document = response.json()
    validate_document(document)
    return document


def latest(document: dict[str, Any]) -> dict[str, Any]:
    return next(e for e in reversed(document["events"]) if e["type"] == "conversion")["conversion"]


def test_a_complete_conversion_saves_nothing(client: TestClient, converter: Converter) -> None:
    converter.replies.append(reply())
    document = convert(client)
    result = latest(document)
    assert result["complete"] is True
    assert result["problems"] == []
    assert result["save_open"] is False and SAVE_OPEN is False
    assert result["definition"]["entry"]["op"] == "cross_above"
    assert [item["condition"] for item in result["mapping"]] == CONDITIONS
    assert document["usage"]["output_tokens"] >= 20
    sent = converter.calls[0]["messages"][0]["content"]
    assert CODE in sent and "2. quantity: the default of 1 share" in sent
    assert client.get("/v1/backtests/strategies").json().get("strategies", []) == []


def test_there_is_no_route_that_saves_a_conversion(client: TestClient) -> None:
    routes = {route.path for route in client.app.routes}  # type: ignore[attr-defined]
    assert not [path for path in routes if "strategy-research" in path and "save" in path]


def test_a_dropped_condition_stops_it(client: TestClient, converter: Converter) -> None:
    converter.replies.append(reply(mapping=MAPPED[:2]))
    result = latest(convert(client))
    assert result["complete"] is False
    assert "condition 2 has no mapping: quantity: the default of 1 share" in result["problems"]


def test_a_condition_added_by_claude_stops_it(client: TestClient, converter: Converter) -> None:
    converter.replies.append(reply({**DEFINITION, "stop_loss_pct": 5}))
    result = latest(convert(client))
    assert result["complete"] is False
    assert "/stop_loss_pct is in the rules but comes from no Pine condition" in result["problems"]


def test_an_unmapped_part_of_a_group_stops_it(client: TestClient, converter: Converter) -> None:
    extra = {"op": "gt", "left": {"price": "close"}, "right": {"value": 1}}
    converter.replies.append(
        reply(
            {**DEFINITION, "entry": {"all": [CROSS_UP, extra]}},
            [
                {"index": 0, "status": "mapped", "rule_paths": ["/entry/all/0"], "reason": ""},
                *MAPPED[1:],
            ],
        )
    )
    result = latest(convert(client))
    assert "/entry/all/1 is in the rules but comes from no Pine condition" in result["problems"]


def test_an_unconvertible_condition_stops_it_and_keeps_its_reason(
    client: TestClient, converter: Converter
) -> None:
    mapping = [
        *MAPPED[:2],
        {"index": 2, "status": "unconvertible", "rule_paths": [], "reason": "공매도는 없습니다"},
    ]
    converter.replies.append(reply(mapping=mapping))
    result = latest(convert(client))
    assert result["complete"] is False
    assert result["mapping"][2]["reason"] == "공매도는 없습니다"
    assert any("cannot be converted" in problem for problem in result["problems"])


def test_a_pointer_to_nothing_stops_it(client: TestClient, converter: Converter) -> None:
    mapping = [{**MAPPED[0], "rule_paths": ["/entry/all/7"]}, *MAPPED[1:]]
    converter.replies.append(reply(mapping=mapping))
    result = latest(convert(client))
    assert any("/entry/all/7" in problem for problem in result["problems"])


def test_an_invalid_definition_goes_back_and_then_stops(
    client: TestClient, converter: Converter
) -> None:
    bad = {**DEFINITION, "entry": {"op": "nope", "left": {}, "right": {}}}
    converter.replies += [reply(bad), reply()]
    assert latest(convert(client))["complete"] is True
    assert "invalid" in converter.calls[1]["messages"][-1]["content"]

    converter.calls.clear()
    converter.replies += [reply(bad), reply(bad), reply(bad)]
    result = latest(convert(client))
    assert len(converter.calls) == 3
    assert result["definition"] is None
    assert result["problems"][0].startswith("the rules are invalid")


def test_no_definition_is_a_result_not_a_failure(client: TestClient, converter: Converter) -> None:
    mapping = [
        {"index": i, "status": "unconvertible", "rule_paths": [], "reason": "공매도"}
        for i in range(3)
    ]
    converter.replies.append(reply("", mapping))
    result = latest(convert(client))
    assert result["definition"] is None
    assert "there is no rules strategy" in result["problems"]
    assert len(converter.calls) == 1


def test_a_python_strategy_is_not_a_conversion(client: TestClient, converter: Converter) -> None:
    python = {"type": "python", "code": "def on_bar(ctx): pass"}
    converter.replies += [reply(python)] * 3
    assert latest(convert(client))["definition"] is None


def test_a_refusal_is_shown_without_another_model(client: TestClient, converter: Converter) -> None:
    converter.replies.append({"content": [], "stop_reason": "refusal"})
    document = convert(client)
    assert [e["text"] for e in document["events"] if e["type"] == "error"] == [
        "Claude declined this conversion"
    ]
    assert len(converter.calls) == 1


def test_the_request_is_opus_at_high_effort_without_fallback() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=reply())

    call_convert_claude(
        system_prompt("ko"),
        [{"role": "user", "content": "x"}],
        "test-key-not-real",
        httpx.MockTransport(handler),
    )
    assert seen["model"] == "claude-opus-5-5"
    assert seen["output_config"]["effort"] == "high"
    assert seen["output_config"]["format"]["type"] == "json_schema"
    assert "fallbacks" not in seen
    assert "tool_choice" not in seen and "temperature" not in seen


def test_the_prompt_names_every_indicator_and_engine_limit() -> None:
    prompt = system_prompt("en")
    assert "supertrend:" in prompt and "cross_above" in prompt
    assert "no short positions" in prompt


def test_rule_parts_and_mapping_helpers() -> None:
    definition = {**DEFINITION, "trailing_stop": {"mode": "pct", "value": "5"}}
    assert rule_parts(definition) == ["/entry", "/exit", "/trailing_stop"]
    assert check_mapping(None, ["a"], [{"index": 9, "status": "mapped", "rule_paths": []}])[:2] == [
        "mapping names condition 9, which does not exist",
        "condition 0 has no mapping: a",
    ]


# --- backtest preview --------------------------------------------------------------------

BARS = [
    {"time": 1_760_000_000 + i * 300, "open": c, "high": c, "low": c, "close": c, "volume": 100}
    for i, c in enumerate([9.0, 11.0, 12.0, 13.0, 9.0, 8.0])
]
PREVIEW = {
    "definition": {
        "type": "rules",
        "entry": {"op": "gt", "left": {"price": "close"}, "right": {"value": 10}},
        "exit": {"op": "lt", "left": {"price": "close"}, "right": {"value": 10}},
        "sizing": {"mode": "fixed_shares", "value": 1},
    },
    "bars": BARS,
    "time_zone": "America/New_York",
    "initial_capital": 1000,
}


def test_the_preview_runs_on_the_given_bars_and_stores_nothing(client: TestClient) -> None:
    response = client.post("/v1/strategy-research/backtest-preview", json=PREVIEW)
    assert response.status_code == 200, response.text
    document = response.json()
    validate_document(document)
    (trade,) = document["trades"]
    # Signal on the 11 close, fill at the next open of 12; exit signal on 9, fill at 8.
    assert (float(trade["entry_price"]), float(trade["exit_price"])) == (12.0, 8.0)
    assert float(trade["net_pnl"]) == -4.0
    assert client.get("/v1/backtests/runs").json().get("runs", []) == []


def test_the_preview_refuses_a_bad_definition(client: TestClient) -> None:
    bad = {**PREVIEW, "definition": {"type": "rules", "entry": {"op": "nope"}}}
    response = client.post("/v1/strategy-research/backtest-preview", json=bad)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_strategy_definition"
