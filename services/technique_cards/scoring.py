"""Score a model's technique card against an answer (ADR 0056 §5, gateway reply #SIW-lib-4).

The answer is the source's words, not the author's code: a condition only in the code is not in
the answer, and a card that states it counts as a made-up condition (the model never sees code).

A predicted statement matches an answer item when its text hits every term group of that item.
Per card:
- field agreement: answer entry / exit / stop-loss conditions matched by a statement in the same
  field (a match in another field is a miss there, not a made-up condition);
- made-up conditions: entry / exit / stop-loss statements matching nothing in the answer
  (a vague remark or an inexpressible sentence put there is counted as misplaced instead);
- citation agreement: citations passing the script citation check (`citations.check_card`);
- ambiguity recall: answer ambiguities matched by the card's ambiguities.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from services.technique_cards.card_schema import CONDITION_FIELDS, validate_card
from services.technique_cards.citations import Line, check_card, normalise

# Pass marks proposed by the gateway (#SIW-lib-4) and adopted in ADR 0056 §5.
THRESHOLDS = {
    "made_up": 0,
    "citation_rate": 0.95,
    "field_rate": 0.90,
    "ambiguity_recall": 0.70,
}


def _number_hit(number: str, text: str) -> bool:
    return re.search(rf"(?<![\d.]){re.escape(number)}(?![\d]|\.\d)", text) is not None


def matches(terms: list[list[str]], text: str, exclude: list[str] | None = None) -> bool:
    folded = normalise(text)
    raw = text.casefold()
    if any(normalise(word) in folded for word in exclude or []):
        return False
    for group in terms:
        hit = False
        for alternative in group:
            if alternative.startswith("#"):
                hit = _number_hit(alternative[1:], raw)
            else:
                hit = normalise(alternative) in folded
            if hit:
                break
        if not hit:
            return False
    return True


@dataclass
class CardScore:
    source_id: str
    schema_errors: list[str] = field(default_factory=list)
    conditions: int = 0
    conditions_matched: int = 0
    made_up: list[str] = field(default_factory=list)
    misplaced: list[str] = field(default_factory=list)
    wrong_field: list[str] = field(default_factory=list)
    citations: int = 0
    citations_ok: int = 0
    citation_failures: list[str] = field(default_factory=list)
    ambiguities: int = 0
    ambiguities_found: int = 0
    missed: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _hits(item: dict[str, Any], text: str) -> bool:
    return matches(item["terms"], text, item.get("exclude"))


def score_card(card: object, answer: dict[str, Any], lines: list[Line]) -> CardScore:
    result = CardScore(source_id=str(answer["source_id"]))
    result.schema_errors = validate_card(card)
    result.conditions = len(answer["conditions"])
    result.ambiguities = len(answer["ambiguities"])
    if result.schema_errors or not isinstance(card, dict):
        result.missed = [item["text"] for item in answer["conditions"]]
        return result

    def statements(name: str) -> list[str]:
        return [str(item.get("text", "")) for item in card.get(name, [])]

    for item in answer["conditions"]:
        if any(_hits(item, text) for text in statements(item["field"])):
            result.conditions_matched += 1
        elif any(
            _hits(item, text)
            for name in CONDITION_FIELDS
            if name != item["field"]
            for text in statements(name)
        ):
            result.wrong_field.append(f"{item['field']}: {item['text']}")
        else:
            result.missed.append(f"{item['field']}: {item['text']}")

    vague_or_inexpressible = answer["ambiguities"] + answer["inexpressible"]
    for name in CONDITION_FIELDS:
        for text in statements(name):
            if any(_hits(item, text) for item in answer["conditions"]):
                continue
            if any(_hits(item, text) for item in vague_or_inexpressible):
                result.misplaced.append(f"{name}: {text}")
            else:
                result.made_up.append(f"{name}: {text}")

    predicted_vague = [
        f"{item.get('text', '')} {item.get('question', '')}" for item in card.get("ambiguities", [])
    ]
    for item in answer["ambiguities"]:
        if any(_hits(item, text) for text in predicted_vague):
            result.ambiguities_found += 1
        else:
            result.missed.append(f"ambiguity: {item['text']}")

    checks = check_card(card, lines)
    result.citations = len(checks)
    result.citations_ok = sum(1 for check in checks if check.ok)
    result.citation_failures = [
        f"{check.locator} {check.quote!r}: {check.reason}" for check in checks if not check.ok
    ]
    return result


def summarise(scores: list[CardScore]) -> dict[str, Any]:
    """Totals over cards and whether they meet the pass marks."""
    conditions = sum(s.conditions for s in scores)
    citations = sum(s.citations for s in scores)
    ambiguities = sum(s.ambiguities for s in scores)
    totals: dict[str, Any] = {
        "cards": len(scores),
        "schema_failures": sum(1 for s in scores if s.schema_errors),
        "made_up": sum(len(s.made_up) for s in scores),
        "misplaced": sum(len(s.misplaced) for s in scores),
        "field_rate": round(sum(s.conditions_matched for s in scores) / conditions, 3)
        if conditions
        else None,
        "citation_rate": round(sum(s.citations_ok for s in scores) / citations, 3)
        if citations
        else None,
        "ambiguity_recall": round(sum(s.ambiguities_found for s in scores) / ambiguities, 3)
        if ambiguities
        else None,
    }
    passed = (
        totals["schema_failures"] == 0
        and totals["made_up"] <= THRESHOLDS["made_up"]
        and all(
            totals[name] is not None and totals[name] >= THRESHOLDS[name]
            for name in ("citation_rate", "field_rate", "ambiguity_recall")
        )
    )
    return {**totals, "thresholds": THRESHOLDS, "pass": passed}
