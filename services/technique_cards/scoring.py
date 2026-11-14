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
- ambiguity recall: answer ambiguities matched by the card's ambiguities (or by its
  inexpressible list, for a remark whose answer item accepts both);
- incomplete: a source condition stated without its indicator period — not made up, but it does
  not count towards field agreement either;
- split: the testable part of a vague remark in its field (answer item marked `split`), with the
  vague rest in ambiguities under the same citation — right; without that rest, incomplete;
- misplaced: a vague remark or rule-free advice put in entry / exit / stop-loss (pass mark 0).

Rules v2 (gateway #SIW-lib-12, 2026-11-14) — v1 could not judge real sources: of 28 "made-up"
conditions in the real-source run, none was missing from the source (the answer had left the
rule out, or the model had split one condition into several sentences):
- made-up = an entry / exit / stop-loss statement that is not grounded (`grounding`): no passing
  citation, or a number or an indicator name that is not in the text around its citation;
- field agreement: an answer condition is matched by the statements of its field taken together
  (each without the condition's opposite-direction words, and each sharing a term with it);
- citations use a ±15 s window; answer items marked `post_hoc` (filled in after a run, from the
  source) can be left out to report the answer as first written.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from services.technique_cards.card_schema import CONDITION_FIELDS, validate_card
from services.technique_cards.citations import WINDOW_SECONDS, Line, check_card, normalise
from services.technique_cards.grounding import check_statement

# Frozen before the real-source set (gateway #SIW-lib-7, 2026-11-13): real results are judged
# only by these rules. Any change raises the version and is agreed with the gateway first.
# v2 (#SIW-lib-12) replaces v1 after the real-source run; it is confirmed on new sources whose
# answers are written before any model output. v1 stays available to reproduce earlier results.
SCORING_RULES_VERSION = 2

# Pass marks proposed by the gateway (#SIW-lib-4) and adopted in ADR 0056 §5.
THRESHOLDS = {
    "made_up": 0,
    "misplaced": 0,  # added by #SIW-lib-6: an unmeasured remark in entry/exit is untestable
    "citation_rate": 0.95,
    "field_rate": 0.90,
    "ambiguity_recall": 0.70,
}


def _number_hit(number: str, text: str) -> bool:
    return re.search(rf"(?<![\d.]){re.escape(number)}(?![\d]|\.\d)", text) is not None


def matches(
    terms: list[list[str]], text: str, exclude: list[str] | None = None, relaxed: bool = False
) -> bool:
    """All term groups hit; `relaxed` skips soft groups (`~`, an indicator's period)."""
    folded = normalise(text)
    raw = text.casefold()
    if any(normalise(word) in folded for word in exclude or []):
        return False
    for group in terms:
        if relaxed and all(alternative.startswith("~") for alternative in group):
            continue
        hit = False
        for alternative in group:
            alternative = alternative.removeprefix("~")
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
    incomplete: list[str] = field(default_factory=list)
    split: list[str] = field(default_factory=list)
    citations: int = 0
    citations_ok: int = 0
    citation_failures: list[str] = field(default_factory=list)
    ambiguities: int = 0
    ambiguities_found: int = 0
    missed: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _hits(item: dict[str, Any], text: str, relaxed: bool = False) -> bool:
    return matches(item["terms"], text, item.get("exclude"), relaxed)


def _without_post_hoc(answer: dict[str, Any]) -> dict[str, Any]:
    kept = dict(answer)
    for name in ("conditions", "ambiguities", "inexpressible", "other"):
        kept[name] = [item for item in answer.get(name, []) if not item.get("post_hoc")]
    return kept


def _field_union_hits(item: dict[str, Any], texts: list[str], relaxed: bool = False) -> bool:
    """Rules v2: the field's statements together hold the condition (#SIW-lib-12)."""
    exclude = item.get("exclude") or []
    candidates = [
        text
        for text in texts
        if not any(normalise(word) in normalise(text) for word in exclude)
        and any(matches([group], text) for group in item["terms"] if not group[0].startswith("~"))
    ]
    return bool(candidates) and matches(item["terms"], " ".join(candidates), None, relaxed)


def score_card(
    card: object,
    answer: dict[str, Any],
    lines: list[Line],
    rules: int = SCORING_RULES_VERSION,
    include_post_hoc: bool = True,
) -> CardScore:
    if rules not in (1, 2):
        raise ValueError("scoring rules are version 1 or 2")
    if not include_post_hoc:
        answer = _without_post_hoc(answer)
    result = CardScore(source_id=str(answer["source_id"]))
    result.schema_errors = validate_card(card)
    result.conditions = len(answer["conditions"])
    result.ambiguities = len(answer["ambiguities"])
    if result.schema_errors or not isinstance(card, dict):
        result.missed = [item["text"] for item in answer["conditions"]]
        return result

    def statements(name: str) -> list[str]:
        return [str(item.get("text", "")) for item in card.get(name, [])]

    def locators(item: dict[str, Any]) -> set[str]:
        return {str(c.get("locator", "")).strip() for c in item.get("citations") or []}

    def split_kept(remark: dict[str, Any], statement: dict[str, Any]) -> bool:
        """The vague rest of a split remark is in ambiguities with the same citation."""
        return any(
            _hits(remark, f"{vague.get('text', '')} {vague.get('question', '')}")
            and locators(vague) & locators(statement)
            for vague in card.get("ambiguities", [])
        )

    def held(item: dict[str, Any], name: str) -> bool:
        if rules == 1:
            return any(_hits(item, text) for text in statements(name))
        return _field_union_hits(item, statements(name))

    for item in answer["conditions"]:
        if held(item, item["field"]):
            result.conditions_matched += 1
        elif any(held(item, name) for name in CONDITION_FIELDS if name != item["field"]):
            result.wrong_field.append(f"{item['field']}: {item['text']}")
        else:
            result.missed.append(f"{item['field']}: {item['text']}")

    vague_or_inexpressible = answer["ambiguities"] + answer["inexpressible"]
    for name in CONDITION_FIELDS:
        for statement in card.get(name, []):
            text = str(statement.get("text", ""))
            if rules == 2:
                reason = check_statement(statement, lines)
                if reason:
                    result.made_up.append(f"{name}: {text} ({reason})")
                    continue
            if any(_hits(item, text) for item in answer["conditions"]):
                continue
            if any(_hits(item, text, relaxed=True) for item in answer["conditions"]):
                result.incomplete.append(f"{name}: {text}")  # the condition, missing its period
                continue
            if any(_hits(item, text) for item in vague_or_inexpressible):
                result.misplaced.append(f"{name}: {text}")
                continue
            # The testable part of a vague remark, split off (#SIW-lib-7): right when the vague
            # rest is kept in ambiguities with the same citation, otherwise incomplete.
            remarks = [
                item
                for item in answer["ambiguities"]
                if item.get("split", {}).get("field") == name
                and matches(item["split"]["terms"], text, item["split"].get("exclude"))
            ]
            if remarks:
                if any(split_kept(item, statement) for item in remarks):
                    result.split.append(f"{name}: {text}")
                else:
                    result.incomplete.append(f"{name}: {text}")  # lost what narrowed it
                continue
            if rules == 1:
                result.made_up.append(f"{name}: {text}")
            # v2: grounded in the source but not in the answer — the answer is the narrower one.

    predicted_vague = [
        f"{item.get('text', '')} {item.get('question', '')}" for item in card.get("ambiguities", [])
    ]
    predicted_inexpressible = statements("inexpressible")
    for item in answer["ambiguities"]:
        # A remark the answer accepts in either place (item by item, #SIW-lib-6).
        also = predicted_inexpressible if "inexpressible" in item.get("accept", []) else []
        if any(_hits(item, text) for text in predicted_vague + also):
            result.ambiguities_found += 1
        else:
            result.missed.append(f"ambiguity: {item['text']}")

    checks = check_card(card, lines, WINDOW_SECONDS if rules == 2 else None)
    result.citations = len(checks)
    result.citations_ok = sum(1 for check in checks if check.ok)
    result.citation_failures = [
        f"{check.locator} {check.quote!r}: {check.reason}" for check in checks if not check.ok
    ]
    return result


def summarise(scores: list[CardScore], rules: int = SCORING_RULES_VERSION) -> dict[str, Any]:
    """Totals over cards and whether they meet the pass marks."""
    conditions = sum(s.conditions for s in scores)
    citations = sum(s.citations for s in scores)
    ambiguities = sum(s.ambiguities for s in scores)
    totals: dict[str, Any] = {
        "cards": len(scores),
        "schema_failures": sum(1 for s in scores if s.schema_errors),
        "made_up": sum(len(s.made_up) for s in scores),
        "misplaced": sum(len(s.misplaced) for s in scores),
        "incomplete": sum(len(s.incomplete) for s in scores),
        "split": sum(len(s.split) for s in scores),
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
        and totals["misplaced"] <= THRESHOLDS["misplaced"]
        and all(
            totals[name] is not None and totals[name] >= THRESHOLDS[name]
            for name in ("citation_rate", "field_rate", "ambiguity_recall")
        )
    )
    return {
        **totals,
        "thresholds": THRESHOLDS,
        "scoring_rules_version": rules,
        "pass": passed,
    }
