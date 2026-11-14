"""Scoring rules v3: structured cards, compared slot by slot (user decision 2026-11-14 "(b)").

Answer conditions carry the same slots as the card (`structured_schema`): field, side,
left · comparator · right with names, periods and values. A predicted condition matches an
answer condition when
- the field is the same and the sides are compatible (`both` fits either);
- the comparators point the same way (cross_above / above / at_or_above / rises are "up",
  their opposites "down", touch / equal alike), with left and right allowed to be swapped
  (A above B is B below A);
- each operand agrees: compatible names (a generic `ma` fits ema / sma / wma; highest_high and
  highest_close alike; bollinger_middle and sma alike), equal numbers for value / percent /
  multiples / bars, and the period when the answer states one — a period left out is
  "incomplete", not a match.
Answer conditions that no slot can hold (`match: "text"`, such as "skip when the tools
disagree") are matched by their term groups against the field's condition texts, as in v2.

Made up (v3): a condition with no passing citation (condition or value citations, ±15 s), a
number in a slot without a passing `value_citation` for that number whose window says it, or an
indicator name not said around its citations. Misplaced, split and ambiguity recall are as in
v2. Pass marks are unchanged.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from services.technique_cards.citations import (
    WINDOW_SECONDS,
    Line,
    check_citation,
    locator_seconds,
    window_text,
)
from services.technique_cards.grounding import grounds, numbers
from services.technique_cards.scoring import (
    THRESHOLDS,
    CardScore,
    _field_union_hits,
    _hits,
    _without_post_hoc,
    matches,
)
from services.technique_cards.structured_schema import validate_structured

SCORING_RULES_VERSION_STRUCTURED = 3
CONDITION_FIELDS = ("entry", "exit", "stop_loss")

_UP = {"cross_above", "above", "at_or_above", "rises"}
_DOWN = {"cross_below", "below", "at_or_below", "falls"}
_TOUCH = {"touch", "equal"}
_FLIP = {"up": "down", "down": "up", "touch": "touch"}
_NAME_GROUPS = [
    {"ma", "ema", "sma", "wma"},
    {"highest_high", "highest_close"},
    {"lowest_low", "lowest_close"},
    {"bollinger_middle", "sma", "ma"},
]
_GENERIC = {"ma": {"ma", "ema", "sma", "wma"}}
_NUMERIC_KINDS = {"value", "percent", "atr_multiple", "range_multiple", "bars"}
# Card names that are indicators, and the grounding family that must be said around a citation.
_INDICATOR_FAMILY = {
    "ma": "moving average", "ema": "ema", "sma": "moving average", "wma": "moving average",
    "rsi": "rsi", "atr": "atr", "macd": "macd", "macd_signal": "macd", "macd_histogram": "macd",
    "bollinger_upper": "bollinger", "bollinger_middle": "bollinger",
    "bollinger_lower": "bollinger", "bollinger_width": "bollinger",
    "adx": "adx", "plus_di": "adx", "minus_di": "adx", "stochastic_k": "stochastic",
    "stochastic_d": "stochastic", "vwap": "vwap", "obv": "obv",
}  # fmt: skip


def _direction(comparator: str) -> str | None:
    if comparator in _UP:
        return "up"
    if comparator in _DOWN:
        return "down"
    if comparator in _TOUCH:
        return "touch"
    return None


def _names_fit(answer: str, predicted: str) -> bool:
    if answer == predicted:
        return True
    if predicted in _GENERIC and answer in _GENERIC[predicted]:
        return True  # the card said "a moving average" for the source's EMA
    return any(answer in group and predicted in group for group in _NAME_GROUPS)


def _dec(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value)).normalize()
    except ArithmeticError:
        return None


def _operand_fit(answer: dict[str, Any], predicted: dict[str, Any]) -> str:
    """'match', 'incomplete' (the period left out) or 'no'."""
    a_kind, p_kind = answer.get("kind"), predicted.get("kind")
    if a_kind in _NUMERIC_KINDS:
        if p_kind not in _NUMERIC_KINDS or _dec(answer.get("value")) != _dec(
            predicted.get("value")
        ):
            return "no"
        return "match"
    if not _names_fit(str(answer.get("name")), str(predicted.get("name"))):
        return "no"
    a_period, p_period = _dec(answer.get("period")), _dec(predicted.get("period"))
    if a_period is not None:
        if p_period is None:
            return "incomplete"
        if a_period != p_period:
            return "no"
    return "match"


def condition_fit(answer: dict[str, Any], predicted: dict[str, Any]) -> str:
    """How a predicted condition fits an answer condition: 'match', 'incomplete' or 'no'."""
    if answer.get("field") != predicted.get("field"):
        return "no"
    sides = {answer.get("side"), predicted.get("side")}
    if "both" not in sides and len(sides) > 1:
        return "no"
    a_dir, p_dir = (
        _direction(str(answer.get("comparator"))),
        _direction(str(predicted.get("comparator"))),
    )
    if a_dir is None or p_dir is None:
        return "no"
    best = "no"
    for left, right, direction in (
        (predicted["left"], predicted["right"], p_dir),
        (predicted["right"], predicted["left"], _FLIP[p_dir]),
    ):
        if direction != a_dir:
            continue
        fits = {_operand_fit(answer["left"], left), _operand_fit(answer["right"], right)}
        if fits == {"match"}:
            return "match"
        if "no" not in fits:
            best = "incomplete"
    return best


def _passing_windows(lines: list[Line], citations: list[dict[str, Any]]) -> list[str]:
    windows = []
    for citation in citations:
        locator, quote = str(citation.get("locator", "")), str(citation.get("quote", ""))
        if check_citation(lines, locator, quote, WINDOW_SECONDS).ok:
            at = locator_seconds(locator)
            if at is not None:
                windows.append(window_text(lines, at, WINDOW_SECONDS))
    return windows


def ground_condition(condition: dict[str, Any], lines: list[Line]) -> str:
    """Why a structured condition is not backed by the source, or "" when it is."""
    all_citations = list(condition.get("citations") or []) + list(
        condition.get("value_citations") or []
    )
    windows = _passing_windows(lines, all_citations)
    if not windows:
        return "no passing citation"
    for side in ("left", "right"):
        operand = condition.get(side) or {}
        for slot in ("period", "value"):
            number = _dec(operand.get(slot))
            if number is None:
                continue
            backed = any(
                _dec(vc.get("value")) == number
                and any(number in numbers(w) for w in _passing_windows(lines, [vc]))
                for vc in condition.get("value_citations") or []
            )
            if not backed:
                return f"no value citation saying {format(number, 'f')} ({side}.{slot})"
        family = _INDICATOR_FAMILY.get(str(operand.get("name")))
        if family and not grounds(family, " ".join(windows)):
            return f"indicator not in the cited source: {operand.get('name')}"
    return ""


def score_structured(
    card: object, answer: dict[str, Any], lines: list[Line], include_post_hoc: bool = True
) -> CardScore:
    if not include_post_hoc:
        answer = _without_post_hoc(answer)
    result = CardScore(source_id=str(answer["source_id"]))
    result.schema_errors = validate_structured(card)
    result.conditions = len(answer["conditions"])
    result.ambiguities = len(answer["ambiguities"])
    if result.schema_errors or not isinstance(card, dict):
        result.missed = [item["text"] for item in answer["conditions"]]
        return result
    predicted: list[dict[str, Any]] = list(card.get("conditions", []))

    def texts(field: str) -> list[str]:
        return [str(c.get("text", "")) for c in predicted if c.get("field") == field]

    for item in answer["conditions"]:
        if item.get("match") == "text":
            if _field_union_hits(item, texts(item["field"])):
                result.conditions_matched += 1
            else:
                result.missed.append(f"{item['field']}: {item['text']}")
            continue
        fits = {condition_fit(item, c) for c in predicted}
        if "match" in fits:
            result.conditions_matched += 1
        elif "incomplete" in fits:
            result.incomplete.append(f"{item['field']}: {item['text']}")
        elif any(
            condition_fit({**item, "field": c.get("field")}, c) == "match"
            for c in predicted
            if c.get("field") != item["field"]
        ):
            result.wrong_field.append(f"{item['field']}: {item['text']}")
        else:
            result.missed.append(f"{item['field']}: {item['text']}")

    vague_or_inexpressible = answer["ambiguities"] + answer["inexpressible"]
    for condition in predicted:
        field, text = str(condition.get("field")), str(condition.get("text", ""))
        reason = ground_condition(condition, lines)
        if reason:
            result.made_up.append(f"{field}: {text} ({reason})")
            continue
        in_answer = any(
            (item.get("match") == "text" and _hits(item, text))
            or (item.get("match") != "text" and condition_fit(item, condition) != "no")
            for item in answer["conditions"]
        )
        if in_answer:
            continue
        if any(_hits(item, text) for item in vague_or_inexpressible):
            split_ok = [
                item
                for item in answer["ambiguities"]
                if item.get("split", {}).get("field") == field
                and matches(item["split"]["terms"], text, item["split"].get("exclude"))
            ]
            if split_ok:
                result.split.append(f"{field}: {text}")
            else:
                result.misplaced.append(f"{field}: {text}")

    predicted_vague = [
        f"{item.get('text', '')} {item.get('question', '')}" for item in card.get("ambiguities", [])
    ]
    inexpressible = [str(s.get("text", "")) for s in card.get("inexpressible", [])]
    for item in answer["ambiguities"]:
        also = inexpressible if "inexpressible" in item.get("accept", []) else []
        if any(_hits(item, text) for text in predicted_vague + also):
            result.ambiguities_found += 1
        else:
            result.missed.append(f"ambiguity: {item['text']}")

    every: list[dict[str, Any]] = []
    for condition in predicted:
        every += list(condition.get("citations") or []) + list(
            condition.get("value_citations") or []
        )
    for name in ("timeframe", "universe", "position", "inexpressible", "ambiguities"):
        for statement in card.get(name, []):
            every += list(statement.get("citations") or [])
    checks = [
        check_citation(lines, str(c.get("locator", "")), str(c.get("quote", "")), WINDOW_SECONDS)
        for c in every
    ]
    result.citations = len(checks)
    result.citations_ok = sum(1 for check in checks if check.ok)
    result.citation_failures = [f"{c.locator} {c.quote!r}: {c.reason}" for c in checks if not c.ok]
    return result


def merge_parts(parts: list[object], source_id: str) -> object:
    """Cards written for the time slices of one long source, merged into one card."""
    if any(not isinstance(p, dict) for p in parts):
        return None
    merged: dict[str, Any] = {"source_id": source_id, "title": ""}
    for name in ("conditions", "timeframe", "universe", "position", "inexpressible", "ambiguities"):
        merged[name] = []
        for part in parts:
            assert isinstance(part, dict)
            merged[name] += list(part.get(name, []))
    first = parts[0]
    assert isinstance(first, dict)
    merged["title"] = str(first.get("title", ""))
    return merged


__all__ = [
    "SCORING_RULES_VERSION_STRUCTURED",
    "THRESHOLDS",
    "condition_fit",
    "ground_condition",
    "merge_parts",
    "score_structured",
]
