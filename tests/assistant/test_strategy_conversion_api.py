"""Pine to rules: nothing dropped, nothing invented, saved only on evidence (ADR 0042 step 3)."""

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
    code_hash,
    comparison_blocks,
    rule_parts,
    save_blocks,
    system_prompt,
)
from services.backtest.strategy import fingerprint
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
    assert result["save_open"] is SAVE_OPEN
    assert result["definition"]["entry"]["op"] == "cross_above"
    assert [item["condition"] for item in result["mapping"]] == CONDITIONS
    assert document["usage"]["output_tokens"] >= 20
    sent = converter.calls[0]["messages"][0]["content"]
    assert CODE in sent and "2. quantity: the default of 1 share" in sent
    assert client.get("/v1/backtests/strategies").json().get("strategies", []) == []


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


# --- comparison ---------------------------------------------------------------------------

BARS = [
    {"time": 1_760_000_000 + i * 300, "open": c, "high": c, "low": c, "close": c, "volume": 100}
    for i, c in enumerate([9.0, 11.0, 12.0, 13.0, 9.0, 8.0])
]
ABOVE = {"op": "gt", "left": {"price": "close"}, "right": {"value": 10}}
BELOW = {"op": "lt", "left": {"price": "close"}, "right": {"value": 10}}
SIMPLE = {
    "type": "rules",
    "entry": ABOVE,
    "exit": BELOW,
    "sizing": {"mode": "fixed_shares", "value": 1},
    "max_positions": 1,
}
SIMPLE_MAP = [
    {"index": 0, "status": "mapped", "rule_paths": ["/entry"], "reason": ""},
    {"index": 1, "status": "mapped", "rule_paths": ["/exit"], "reason": ""},
    {"index": 2, "status": "mapped", "rule_paths": ["/sizing"], "reason": ""},
]
# Signal on the 11 close, fill at the next open of 12; exit signal on 9, fill at 8.
FIRST_ENTRY = BARS[2]["time"]


def comparison_request(trades: int = 1, net: float = -4.0, **extra: Any) -> dict[str, Any]:
    return {
        "code": CODE,
        "pine": {"closed_trades": trades, "net_profit": net, "entry_times": [FIRST_ENTRY]},
        "bars": BARS,
        "time_zone": "America/New_York",
        "initial_capital": 1000,
        **extra,
    }


def converted(client: TestClient, converter: Converter) -> str:
    converter.replies.append(reply(SIMPLE, SIMPLE_MAP))
    path = conversation(client)
    response = client.post(
        f"{BASE}/{path}/convert", json={"code": CODE, "conditions": CONDITIONS, "language": "ko"}
    )
    assert response.status_code == 200, response.text
    return path


def test_the_comparison_runs_the_rules_on_the_given_bars_and_records_its_basis(
    client: TestClient, converter: Converter
) -> None:
    path = converted(client, converter)
    response = client.post(f"{BASE}/{path}/comparison", json=comparison_request())
    assert response.status_code == 200, response.text
    document = response.json()
    validate_document(document)
    event = next(e for e in reversed(document["events"]) if e["type"] == "comparison")
    result = event["comparison"]
    assert (result["pine_trades"], result["rule_trades"], result["rule_net"]) == (1, 1, -4.0)
    assert result["entry_match"] == 100
    assert result["bars"] == len(BARS)
    assert result["code_sha256"] == code_hash(CODE)
    assert result["engine_version"] and result["fill_model_version"]
    assert result["blocks"] == ["too_few_trades"]
    assert client.get("/v1/backtests/runs").json().get("runs", []) == []


def test_there_is_nothing_to_compare_without_rules(client: TestClient) -> None:
    response = client.post(f"{BASE}/{conversation(client)}/comparison", json=comparison_request())
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "no_rules_to_compare"


# --- the save gate: pilot B's outcomes, kept as the fixed test the assignment asked for -------


def result_of(definition: Any = SIMPLE, complete: bool = True) -> dict[str, Any]:
    return {
        "code_sha256": code_hash(CODE),
        "definition": definition,
        "mapping": [{"status": "mapped"}] if complete else [{"status": "unconvertible"}],
        "problems": [] if complete else ["open"],
        "complete": complete,
    }


def compared(
    pine: int,
    rules: int,
    pine_net: float,
    rule_net: float,
    match: float,
    definition: dict[str, Any] = SIMPLE,
) -> dict[str, Any]:
    return {
        "code_sha256": code_hash(CODE),
        "definition_sha256": fingerprint(definition),
        "blocks": comparison_blocks(pine, rules, pine_net, rule_net, match),
    }


# Case: Pine trades, rule trades, Pine net, rule net, entry match; from pilot B's recheck.
PASSING = {
    "S3": (65, 65, 2.19, 2.19, 100),
    "S4": (32, 32, 2.45, 2.45, 100),
    "S5": (79, 79, -1.61, -1.61, 100),
    "S7": (94, 94, 4.15, 4.15, 100),
    "S8": (220, 220, 0.04, 0.04, 100),
    "S9": (121, 121, 0.86, 0.86, 100),
    "S10": (71, 71, 41.85, 41.85, 100),
    "B1": (51, 51, 9.67, 9.67, 100),
    "B4": (26, 26, 2.89, 2.89, 100),
}
# They behaved alike, on too few trades for that to count (user decision: 20).
TOO_FEW = {
    "S1": (14, 14, 3.86, 3.86, 100),
    "S2": (4, 4, 3.27, 3.27, 100),
    "S6": (6, 6, 8.92, 8.92, 100),
}
COMPLETE_BUT_DIFFERENT = {
    "B2": (3, 4, -1.0, 3.27, 100),
    "B3": (93, 91, 10.82, 7.29, 98),
}
OPEN = {
    "U4": (2, 0, 10.48, 0.0, 50),
    "M1": (130, 65, -19.79, -7.3, 50),
    "M2": (132, 117, 1.52, 1.15, 99),
    "B6": (117, 0, 1.3, 0.0, 1),
}


@pytest.mark.parametrize("case", PASSING)
def test_cases_that_behaved_alike_can_be_saved(case: str) -> None:
    assert save_blocks(result_of(), compared(*PASSING[case]), CODE) == []


@pytest.mark.parametrize("case", TOO_FEW)
def test_too_few_trades_is_not_evidence(case: str) -> None:
    assert save_blocks(result_of(), compared(*TOO_FEW[case]), CODE) == ["too_few_trades"]


def test_zero_against_zero_does_not_pass() -> None:
    assert "too_few_trades" in comparison_blocks(0, 0, 0.0, 0.0, 100.0)


@pytest.mark.parametrize("case", COMPLETE_BUT_DIFFERENT)
def test_a_complete_conversion_that_behaves_differently_is_blocked(case: str) -> None:
    assert save_blocks(result_of(), compared(*COMPLETE_BUT_DIFFERENT[case]), CODE)


@pytest.mark.parametrize("case", OPEN)
def test_an_open_conversion_is_blocked(case: str) -> None:
    blocks = save_blocks(result_of(complete=False), compared(*OPEN[case]), CODE)
    assert "conversion_open" in blocks


def test_b5_sonnet_invented_an_expiry_and_is_blocked_even_if_the_numbers_agree() -> None:
    limit = {**SIMPLE, "entry_order": {"type": "limit", "offset_pct": "-1", "expire_bars": 1}}
    agreeing = compared(30, 30, 5.0, 5.0, 100, limit)
    assert save_blocks(result_of(limit), agreeing, CODE) == ["unmatched_order_terms"]
    actual = compared(22, 0, 2.08, 0.0, 0, limit)
    assert "unmatched_order_terms" in save_blocks(result_of(limit), actual, CODE)


def test_changed_code_or_rules_void_the_comparison() -> None:
    good = compared(30, 30, 5.0, 5.0, 100)
    assert "code_changed" in save_blocks(result_of(), good, CODE + "\n")
    assert "comparison_stale" in save_blocks(result_of(), good, CODE + "\n")
    other = {**SIMPLE, "exit": ABOVE}
    assert "comparison_stale" in save_blocks(result_of(other), good, CODE)
    assert save_blocks(result_of(), None, CODE) == ["no_comparison"]
    assert save_blocks(None, good, CODE) == ["no_rules"]


# --- saving --------------------------------------------------------------------------------


@pytest.fixture
def saving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, converter: Converter
) -> Iterator[TestClient]:
    monkeypatch.setenv("SIW_DATA_DIR", str(tmp_path))
    import services.api.main as main

    original = main._claude_key
    main._claude_key = lambda _credentials: "test-key-not-real"  # type: ignore[assignment]
    script = Scripted()
    script.turns += [answer("좋습니다")]
    app = create_app(
        review_store=ReviewStore(tmp_path / "review-test.sqlite3", lambda: NOW),
        strategy_converse=script,
        strategy_convert=converter,
        clock=lambda: NOW,
    )
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        main._claude_key = original  # type: ignore[assignment]


def many_bars() -> tuple[list[dict[str, Any]], list[int]]:
    bars, entries = [], []
    time = 1_760_000_000
    for _cycle in range(25):
        for close in (9.0, 11.0, 12.0, 13.0, 9.0, 8.0):
            bars.append(
                {
                    "time": time,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 100,
                }
            )
            if close == 12.0:
                entries.append(time)
            time += 300
    return bars, entries


def test_a_conversion_that_passes_saves_a_strategy_and_starts_nothing(
    saving: TestClient, converter: Converter
) -> None:
    path = converted(saving, converter)
    bars, entries = many_bars()
    request = comparison_request(25, -100.0, bars=bars)
    request["pine"]["entry_times"] = entries
    compared_doc = saving.post(f"{BASE}/{path}/comparison", json=request).json()
    assert (
        next(e for e in reversed(compared_doc["events"]) if e["type"] == "comparison")[
            "comparison"
        ]["blocks"]
        == []
    )
    response = saving.post(f"{BASE}/{path}/save", json={"code": CODE, "name": "교차 전략"})
    assert response.status_code == 200, response.text
    document = response.json()
    validate_document(document)
    event = next(e for e in reversed(document["events"]) if e["type"] == "conversion_saved")
    assert event["strategy_version"] == 1
    strategy = saving.get(f"/v1/backtests/strategies/{event['strategy_id']}").json()
    assert strategy["name"] == "교차 전략"
    assert strategy["definition"]["entry"]["op"] == "gt"
    assert saving.get("/v1/backtests/runs").json().get("runs", []) == []


def test_saving_is_refused_with_its_reasons(saving: TestClient, converter: Converter) -> None:
    path = converted(saving, converter)
    response = saving.post(f"{BASE}/{path}/save", json={"code": CODE, "name": "x"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conversion_not_savable"
    assert "no_comparison" in response.json()["error"]["message"]
    saving.post(f"{BASE}/{path}/comparison", json=comparison_request())
    refused = saving.post(f"{BASE}/{path}/save", json={"code": CODE, "name": "x"})
    assert "too_few_trades" in refused.json()["error"]["message"]
    assert saving.get("/v1/backtests/strategies").json().get("strategies", []) == []
