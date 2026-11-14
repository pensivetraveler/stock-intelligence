"""Structured cards (card schema v2), prompt v3, scoring rules v3, long-source splitting."""

from __future__ import annotations

import copy
import json
from typing import Any

from services.technique_cards import evalset
from services.technique_cards.citations import Line, parse_transcript
from services.technique_cards.prompt import structured_prompt
from services.technique_cards.structured_schema import STRUCTURED_SCHEMA, validate_structured
from services.technique_cards.structured_scoring import (
    condition_fit,
    ground_condition,
    score_structured,
)

TRANSCRIPT = parse_transcript(
    "[00:10] We draw one trend line, a fifty period moving average, an E M A.\n"
    "[00:50] When the price closes back above that line, we buy.\n"
    "[01:10] The stop sits two A T R below the entry price.\n"
    "[01:40] If the tools disagree, just wait and do not trade.\n"
    "[02:30] Volume should really surge on the breakout.\n"
    "[03:10] The target is three ATR above the entry."
)


def _op(
    kind: str, name: str, period: float | None = None, value: float | None = None
) -> dict[str, Any]:
    return {"kind": kind, "name": name, "period": period, "value": value}


def _cite(locator: str, quote: str) -> list[dict[str, str]]:
    return [{"locator": locator, "quote": quote}]


ANSWER: dict[str, Any] = {
    "source_id": "demo",
    "title": "demo",
    "conditions": [
        {
            "field": "entry", "side": "long", "match": "structured",
            "left": _op("price", "close"), "comparator": "cross_above",
            "right": _op("indicator", "ema", period=50),
            "text": "close crosses above EMA(50)", "terms": [["ema"], ["#50", "fifty"]],
            "locator": "00:50", "quote": "When the price closes back above that line, we buy.",
        },
        {
            "field": "stop_loss", "side": "long", "match": "structured",
            "left": _op("price", "stop_price"), "comparator": "below",
            "right": _op("atr_multiple", "atr", value=2),
            "text": "stop 2 ATR below entry", "terms": [["atr"], ["#2", "two"]],
            "locator": "01:10", "quote": "The stop sits two A T R below the entry price.",
        },
        {
            "field": "entry", "side": "both", "match": "text",
            "left": _op("other", "other"), "comparator": "other", "right": _op("none", "none"),
            "text": "skip when the tools disagree",
            "terms": [["disagree", "엇갈"], ["wait", "skip", "do not trade", "관망"]],
            "locator": "01:40", "quote": "If the tools disagree, just wait and do not trade.",
        },
    ],
    "ambiguities": [
        {
            "text": "how much volume is a surge", "terms": [["volume"], ["surge"]],
            "accept": ["ambiguities"], "locator": "02:30",
            "quote": "Volume should really surge on the breakout.",
        }
    ],
    "inexpressible": [],
    "other": [],
}  # fmt: skip


def _card() -> dict[str, Any]:
    entry = {
        "field": "entry",
        "side": "long",
        "text": "Buy when the close crosses above the 50 EMA.",
        "basis": "source",
        "left": _op("price", "close"),
        "comparator": "cross_above",
        "right": _op("indicator", "ema", period=50),
        "timeframe": "",
        "citations": _cite("00:50", "When the price closes back above that line, we buy."),
        "value_citations": [
            {"value": 50, "locator": "00:10", "quote": "a fifty period moving average"}
        ],
    }
    stop = {
        "field": "stop_loss",
        "side": "long",
        "text": "Stop two ATR below entry.",
        "basis": "source",
        "left": _op("price", "stop_price"),
        "comparator": "below",
        "right": _op("atr_multiple", "atr", value=2),
        "timeframe": "",
        "citations": _cite("01:10", "The stop sits two A T R below the entry price."),
        "value_citations": [{"value": 2, "locator": "01:10", "quote": "The stop sits two A T R"}],
    }
    skip = {
        "field": "entry",
        "side": "both",
        "text": "If the tools disagree, wait and do not trade.",
        "basis": "source",
        "left": _op("other", "other"),
        "comparator": "other",
        "right": _op("none", "none"),
        "timeframe": "",
        "citations": _cite("01:40", "If the tools disagree, just wait and do not trade."),
        "value_citations": [],
    }
    return {
        "source_id": "demo", "title": "demo", "conditions": [entry, stop, skip],
        "timeframe": [], "universe": [], "position": [], "inexpressible": [],
        "ambiguities": [
            {"text": "How much is a volume surge?", "question": "What volume ratio?",
             "citations": _cite("02:30", "Volume should really surge on the breakout.")}
        ],
    }  # fmt: skip


def _objects(schema: dict[str, Any]) -> list[dict[str, Any]]:
    found = [schema] if schema.get("type") == "object" else []
    found += [d for d in STRUCTURED_SCHEMA["$defs"].values()] if schema is STRUCTURED_SCHEMA else []
    return found


def test_schema_is_strict_and_refs_defined_once() -> None:
    for item in _objects(STRUCTURED_SCHEMA):
        assert item["additionalProperties"] is False
        assert item["required"] == list(item["properties"])
    text = json.dumps(STRUCTURED_SCHEMA)
    assert text.count('"comparator"') == 2  # property and required entry, once each
    assert validate_structured(_card()) == []
    broken = _card()
    broken["conditions"][0]["comparator"] = "sideways"
    assert validate_structured(broken)


def test_condition_fit_slots() -> None:
    answer, card = ANSWER["conditions"][0], _card()["conditions"][0]
    assert condition_fit(answer, card) == "match"
    swapped = {**card, "left": card["right"], "right": card["left"], "comparator": "cross_below"}
    assert condition_fit(answer, swapped) == "match"  # EMA crosses below close = close above
    generic = copy.deepcopy(card)
    generic["right"]["name"] = "ma"
    assert condition_fit(answer, generic) == "match"  # "a moving average" for the EMA
    no_period = copy.deepcopy(card)
    no_period["right"]["period"] = None
    assert condition_fit(answer, no_period) == "incomplete"
    wrong = copy.deepcopy(card)
    wrong["right"]["period"] = 20
    assert condition_fit(answer, wrong) == "no"
    assert condition_fit(answer, {**card, "comparator": "cross_below"}) == "no"
    assert condition_fit(answer, {**card, "side": "short"}) == "no"
    assert condition_fit(answer, {**card, "field": "exit"}) == "no"


def test_numbers_need_their_own_value_citation() -> None:
    card = _card()["conditions"][0]
    assert ground_condition(card, TRANSCRIPT) == ""
    no_vc = {**card, "value_citations": []}
    assert "value citation" in ground_condition(no_vc, TRANSCRIPT)
    # The number said elsewhere: a value citation pointing at a line without it fails.
    elsewhere = {**card, "value_citations": [{"value": 50, "locator": "00:50", "quote": "we buy"}]}
    assert "value citation" in ground_condition(elsewhere, TRANSCRIPT)
    invented = copy.deepcopy(card)
    invented["right"]["period"] = 21
    invented["value_citations"] = [{"value": 21, "locator": "00:10", "quote": "a fifty period"}]
    assert ground_condition(invented, TRANSCRIPT)
    rsi = copy.deepcopy(card)
    rsi["right"] = _op("indicator", "rsi")
    rsi["value_citations"] = []
    assert "indicator" in ground_condition(rsi, TRANSCRIPT)
    assert ground_condition({**card, "citations": [], "value_citations": []}, TRANSCRIPT)


def test_score_structured_perfect_text_fallback_and_misplaced() -> None:
    result = score_structured(_card(), ANSWER, TRANSCRIPT)
    assert result.conditions_matched == 3 and not result.made_up and not result.misplaced
    assert result.ambiguities_found == 1 and result.citations_ok == result.citations
    card = _card()
    card["conditions"].append(
        {**card["conditions"][2], "text": "Volume should really surge.", "field": "entry",
         "citations": _cite("02:30", "Volume should really surge on the breakout.")}
    )  # fmt: skip
    card["conditions"][0]["right"]["period"] = None
    card["conditions"][0]["value_citations"] = []
    result = score_structured(card, ANSWER, TRANSCRIPT)
    assert result.misplaced and result.incomplete
    assert result.conditions_matched == 2


def test_split_parts_and_prompt() -> None:
    short = [Line(s, f"line {s}") for s in range(0, 1700, 10)]
    assert len(evalset.split_parts(short)) == 1
    long = [Line(s, f"line {s}") for s in range(0, 2460, 10)]
    parts = evalset.split_parts(long)
    assert len(parts) == 3
    assert parts[0][-1].seconds > parts[1][0].seconds  # 30 s of overlap
    assert {line.seconds for part in parts for line in part} == {line.seconds for line in long}
    assert "part 2 of 3" in structured_prompt("x", "t", "[00:01] a", (2, 3))
    assert "part" not in structured_prompt("x", "t", "[00:01] a").split("source_id")[0][-200:]


def test_structured_eval_set_and_merged_scoring(tmp_path: Any) -> None:
    long_text = "\n".join(
        f"[{s // 60:02d}:{s % 60:02d}] line {s}" for s in range(0, 2460, 30)
    ).replace("[00:00]", "[00:00]")
    items = [{"source_id": "demo", "title": "demo", "transcript": long_text, "answer": ANSWER}]
    evalset.write_structured(tmp_path, "t", "private", items, "note")
    full = json.loads((tmp_path / "evalset.json").read_text())
    assert [c["id"] for c in full["cases"]] == ["demo#p1", "demo#p2", "demo#p3"]
    assert full["maxTokens"] == 16000 and full["responseSchema"]["strict"] is True
    answers = {"demo": {"answer": ANSWER, "transcript": "\n".join(
        f"[{line.seconds // 60:02d}:{line.seconds % 60:02d}] {line.text}" for line in TRANSCRIPT
    )}}  # fmt: skip
    first, second = _card(), _card()
    first["conditions"] = first["conditions"][:1]
    second["conditions"] = second["conditions"][1:]
    report = {
        "results": [
            {"caseId": "demo#p1", "model": "m", "answer": json.dumps(first), "costUsd": 0.1},
            {"caseId": "demo#p2", "model": "m", "answer": json.dumps(second), "costUsd": 0.1},
        ]
    }
    scored = evalset.score(report, answers, rules=3)
    assert scored["m"]["summary"]["field_rate"] == 1
    assert scored["m"]["summary"]["scoring_rules_version"] == 3
    report["results"][1]["answer"] = "not json"
    assert evalset.score(report, answers, rules=3)["m"]["summary"]["schema_failures"] == 1
