"""Script-first conversion of card condition slots (ADR 0056 L3, user decision 2026-11-15)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.api.technique_card_routes import register_technique_card_routes
from services.technique_cards import script_rules, strategy_draft
from services.technique_cards.library import Library
from services.technique_cards.service import TechniqueCards
from tests.technique_cards.test_card_pipeline import (
    DONCHIAN,
    KEYS,
    FakeCli,
    FakeStrategies,
    _condition,
    _config,
    _op,
    _ready_card,
    _reply,
    _store,
)


def _convert(**over: Any) -> dict[str, Any]:
    return script_rules.convert(_condition(**over), "")


def test_slots_become_rules_and_say_what_they_assumed() -> None:
    high = _convert(comparator="above", right=_op("price", "highest_high", period=20))
    assert high["place"] == {"kind": "entry", "rule": DONCHIAN["entry"]}
    assert high["assumptions"] == []

    ma = _convert(right=_op("indicator", "ma"))
    assert ma["place"]["rule"]["right"] == {
        "indicator": "sma", "params": {"length": 9}, "output": "value"
    }  # fmt: skip
    assert ma["assumptions"] == [
        {"kind": "ma_as_sma"},
        {"kind": "default_length", "indicator": "sma", "param": "length", "value": 9},
    ]

    rising = _convert(left=_op("indicator", "rsi", period=14), comparator="rises", right=None)
    assert rising["place"]["rule"]["op"] == "gt"
    assert rising["place"]["rule"]["right"]["offset"] == 1

    both = _convert(side="both", comparator="below", right=_op("value", "v", value=30),
                    left=_op("indicator", "rsi", period=14))  # fmt: skip
    assert both["place"]["rule"]["right"] == {"value": "30"}
    assert both["assumptions"] == [{"kind": "long_only"}]


def test_stops_targets_and_not_used_items() -> None:
    stop = _convert(field="stop_loss", comparator="below", right=_op("percent", "p", value=2))
    assert stop["place"] == {"kind": "field", "name": "stop_loss_pct", "value": "2"}
    target = _convert(field="exit", comparator="above", right=_op("percent", "p", value=10))
    assert target["place"]["name"] == "take_profit_pct"
    atr = _convert(field="stop_loss", comparator="below", right=_op("atr_multiple", "a", value=2))
    assert atr["place"]["value"] == {"length": 14, "mult": "2"}
    assert atr["assumptions"][0]["indicator"] == "atr"
    none = _convert(field="exit", comparator="none", left=_op("none", "none"),
                    right=_op("none", "none"))  # fmt: skip
    assert none["place"] == {"kind": "absent", "pointer": "/take_profit_pct"}


@pytest.mark.parametrize(
    "over",
    [
        {"side": "short"},
        {"timeframe": "1h"},
        {"right": _op("price", "entry_price")},
        {"right": _op("indicator", "ichimoku")},
        {"right": _op("range_multiple", "r", value=1.5)},
        {"comparator": "touch"},
        {"right": _op("price", "highest_high")},
    ],
)
def test_what_the_slots_cannot_settle_is_left(over: dict[str, Any]) -> None:
    result = script_rules.convert(_condition(**over), "1d")
    assert result["place"] is None and result["reason"]


def test_script_only_draft_calls_no_model_and_leaves_text_items_out(tmp_path: Path) -> None:
    row = _ready_card(_store(tmp_path))

    def call(system: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
        raise AssertionError("no model for a script draft")

    result = strategy_draft.draft(row, "script", KEYS, call=call)
    assert result["model"] == "script" and result["cost_usd"] == "0"
    definition = result["conversion"]["definition"]
    assert definition["entry"] == {"all": [DONCHIAN["entry"]]}
    assert definition["exit"] == {"any": [DONCHIAN["exit"]]}
    assert result["scripted"] == [0, 1] and result["blocking"] == []
    assert result["conversion"]["mapping"][2]["status"] == "unconvertible"
    assert strategy_draft.save_blocks(result, row, accept=True) == []


def test_a_model_gets_the_scripted_rules_fixed_and_may_not_change_them(tmp_path: Path) -> None:
    row = _ready_card(_store(tmp_path))
    sent: list[str] = []
    changed = {**DONCHIAN, "entry": {**DONCHIAN["entry"], "op": "gte"}}

    def call(system: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
        sent.append(messages[0]["content"])
        assert "fixed_rule" in system
        return _reply(
            changed,
            [
                {"index": 0, "status": "mapped", "rule_paths": ["/entry"], "reason": ""},
                {"index": 1, "status": "mapped", "rule_paths": ["/exit"], "reason": ""},
                {"index": 2, "status": "unconvertible", "rule_paths": [], "reason": "size"},
            ],
        )

    result = strategy_draft.draft(row, "google/gemini-4-argon", KEYS, call=call)
    assert "fixed_rule=" + json.dumps(DONCHIAN["entry"]) in sent[0]
    assert "fixed_rule" not in result["items"][0]
    assert result["conversion"]["mapping"][0]["condition"] == result["items"][0]
    assert any("condition 0 was converted by the script" in p for p in result["blocking"])
    assert not any("condition 1 " in p for p in result["blocking"])


def test_indented_rule_paths_from_the_model_still_keep_the_scripted_rules(
    tmp_path: Path,
) -> None:
    # Gemini 4 Argon answered "    /entry" (real draft 2026-11-16): the pointer is trimmed.
    row = _ready_card(_store(tmp_path))

    def call(system: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
        return _reply(
            DONCHIAN,
            [
                {"index": 0, "status": "mapped", "rule_paths": ["    /entry"], "reason": ""},
                {"index": 1, "status": "mapped", "rule_paths": ["/exit  "], "reason": ""},
                {"index": 2, "status": "unconvertible", "rule_paths": [], "reason": "size"},
            ],
        )

    result = strategy_draft.draft(row, "google/gemini-4-argon", KEYS, call=call)
    mapping = result["conversion"]["mapping"]
    assert [m["rule_paths"] for m in mapping] == [["/entry"], ["/exit"], []]
    assert result["blocking"] == []


def test_when_the_script_converts_every_item_no_model_is_called(tmp_path: Path) -> None:
    store = _store(tmp_path)
    row = _ready_card(store)
    row["card"]["position"] = []
    row["card"]["conditions"][1]["right"] = _op("indicator", "ma")  # an assumed length

    def call(system: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
        raise AssertionError("everything was scripted")

    result = strategy_draft.draft(row, "google/gemini-4-argon", KEYS, call=call)
    assert result["model"] == "script" and result["unconvertible"] == 0
    assert {a["kind"] for a in result["assumptions"]} == {"ma_as_sma", "default_length"}
    assert strategy_draft.save_blocks(result, row, accept=True) == [
        "1 item(s) rest on a reading the source did not state: tick"
    ]
    assert strategy_draft.save_blocks(result, row, accept=True, confirm=True) == []


def test_routes_take_script_and_need_the_confirm_tick(tmp_path: Path) -> None:
    store = _store(tmp_path)
    service = TechniqueCards(Library(_config(), tmp_path / "cache", FakeCli()), store)
    strategies = FakeStrategies()
    app = FastAPI()

    def error(request: Any, status: int, code: str, message: str) -> JSONResponse:
        return JSONResponse(
            status_code=status, content={"error": {"code": code, "message": message}}
        )

    register_technique_card_routes(
        app, cards=lambda: service, keys=lambda: KEYS, error_response=error,
        start=lambda w: w(), strategies=lambda: strategies,
    )  # fmt: skip
    client = TestClient(app)
    row = _ready_card(store)
    card = row["card"]
    card["conditions"][1]["right"] = _op("indicator", "ma", period=10)
    conditions = {**row["resolutions"]["conditions"]}
    conditions["1"] = {"action": "edit", "condition": card["conditions"][1], "note": ""}
    row = store.resolve(row["card_id"], row["version"], conditions, {"0": "2x"})

    started = client.post(f"/v1/technique-cards/{row['card_id']}/drafts", json={"model": "script"})
    found = client.get(f"/v1/technique-drafts/{started.json()['draft_id']}").json()
    assert found["model"] == "script" and found["needs_confirm"] is True
    path = f"/v1/technique-drafts/{found['draft_id']}/save"
    refused = client.post(path, json={"name": "S", "accept_unconvertible": True})
    assert refused.status_code == 409 and "tick" in refused.json()["error"]["message"]
    saved = client.post(
        path, json={"name": "S", "accept_unconvertible": True, "confirm_assumptions": True}
    )
    assert saved.status_code == 200
    assert strategies.saved[0][1]["exit"]["any"][0]["right"]["indicator"] == "sma"


def test_a_slot_named_other_is_left_to_the_text() -> None:
    """원펀치매매 2권 (draft 7701a747): "피보나치 0.5 구간" came out as low >= 0.5."""
    result = _convert(
        left=_op("price", "low"), comparator="at_or_above", right=_op("value", "other", value=0.5)
    )
    assert result["place"] is None and "other" in result["reason"]


def test_a_model_may_leave_out_a_fixed_item_whose_text_says_more(tmp_path: Path) -> None:
    """The slots kept "close >= previous close" but the text also asks for more volume."""
    row = _ready_card(_store(tmp_path))

    def call(system: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
        assert "says more than its fixed rule" in system
        return _reply(
            {"type": "rules", "entry": DONCHIAN["entry"]},
            [
                {"index": 0, "status": "mapped", "rule_paths": ["/entry"], "reason": ""},
                {"index": 1, "status": "unconvertible", "rule_paths": [],
                 "reason": "본문에 거래량 조건이 더 있음"},
                {"index": 2, "status": "unconvertible", "rule_paths": [], "reason": "size"},
            ],
        )  # fmt: skip

    result = strategy_draft.draft(row, "google/gemini-4-argon", KEYS, call=call)
    assert result["blocking"] == []
    assert result["unconvertible"] == 2

    # Leaving it out without a reason is still a problem.
    def silent(system: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
        return _reply(
            {"type": "rules", "entry": DONCHIAN["entry"]},
            [
                {"index": 0, "status": "mapped", "rule_paths": ["/entry"], "reason": ""},
                {"index": 1, "status": "unconvertible", "rule_paths": [], "reason": " "},
                {"index": 2, "status": "unconvertible", "rule_paths": [], "reason": "size"},
            ],
        )

    blocked = strategy_draft.draft(row, "google/gemini-4-argon", KEYS, call=silent)
    assert any("condition 1" in p for p in blocked["blocking"])
