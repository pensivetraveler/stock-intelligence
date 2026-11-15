"""Script checks on an extracted card (ADR 0056 §5, the guard for the below-mark assignment).

1. `unbacked` (근거 없음): a condition whose citations do not pass, whose number has no value
   citation saying it within ±15 s, or whose indicator is not said near the citation. The same
   code as scoring rules v3 (`structured_scoring.ground_condition`). An unstated indicator
   period (RSI 14 as a default) is caught here.
2. `no_criterion` (기준 없음): an entry / exit / stop-loss condition with nothing to compare
   against — no comparator, no right side, or a numeric side without its number.
3. The timeline: every transcript line with the conditions and universe filters that cite it,
   so the user can see what the card left out.

A flagged condition does not go to the rule builder or Pine until the user resolves it.
"""

from __future__ import annotations

from typing import Any

from services.technique_cards.citations import Line, format_locator, locator_position, unit_of
from services.technique_cards.structured_scoring import ground_condition

CHECKS_VERSION = 1
NUMERIC_KINDS = {"value", "percent", "atr_multiple", "range_multiple", "bars"}
VAGUE = {"none", "other"}
WINDOW_SECONDS = 15


def no_criterion(condition: dict[str, Any]) -> str:
    """Why a condition has no criterion to test, or "" when it has one."""
    comparator = str(condition.get("comparator") or "none")
    left = condition.get("left") or {}
    right = condition.get("right") or {}
    if comparator in VAGUE:
        return "no comparison"
    if str(left.get("kind") or "none") in VAGUE:
        return "nothing compared"
    for side, operand in (("left", left), ("right", right)):
        if str(operand.get("kind")) in NUMERIC_KINDS and operand.get("value") is None:
            return f"{side} side has no number"
    if comparator in ("rises", "falls"):
        return ""  # "RSI rises" says a direction; how much is asked only when a number is given
    if str(right.get("kind") or "none") in VAGUE:
        return "nothing to compare against"
    return ""


def check_card(card: dict[str, Any], lines: list[Line]) -> dict[str, Any]:
    conditions = []
    for index, condition in enumerate(card.get("conditions") or []):
        flags = []
        reason = ground_condition(condition, lines)
        if reason:
            flags.append({"code": "unbacked", "reason": reason})
        reason = no_criterion(condition)
        if reason:
            flags.append({"code": "no_criterion", "reason": reason})
        if condition.get("basis") == "claude_inference":
            flags.append({"code": "inference", "reason": "the model's inference, not the source"})
        conditions.append({"index": index, "field": condition.get("field"), "flags": flags})
    return {
        "version": CHECKS_VERSION,
        "conditions": conditions,
        "counts": {
            "unbacked": sum(any(f["code"] == "unbacked" for f in c["flags"]) for c in conditions),
            "no_criterion": sum(
                any(f["code"] == "no_criterion" for f in c["flags"]) for c in conditions
            ),
            "ambiguities": len(card.get("ambiguities") or []),
            "inexpressible": len(card.get("inexpressible") or []),
        },
    }


def _cited_positions(item: dict[str, Any], unit: str) -> list[int]:
    found = []
    for citation in list(item.get("citations") or []) + list(item.get("value_citations") or []):
        at = locator_position(str(citation.get("locator", "")), unit)
        if at is not None:
            found.append(at)
    return found


def timeline(card: dict[str, Any], lines: list[Line]) -> list[dict[str, Any]]:
    """Each source line with what cites it (conditions by index, universe by index). A page or
    paragraph citation marks every line of that page or paragraph."""
    unit = unit_of(lines)
    marks: dict[int, dict[str, list[int]]] = {}
    for kind in ("conditions", "universe"):
        for index, item in enumerate(card.get(kind) or []):
            for at in _cited_positions(item, unit):
                if unit != "time":
                    for i, line in enumerate(lines):
                        if line.seconds == at:
                            marks.setdefault(i, {"conditions": [], "universe": []})[kind].append(
                                index
                            )
                    continue
                nearest = min(
                    range(len(lines)), key=lambda i: abs(lines[i].seconds - at), default=None
                )
                if nearest is None or abs(lines[nearest].seconds - at) > WINDOW_SECONDS:
                    continue
                marks.setdefault(nearest, {"conditions": [], "universe": []})[kind].append(index)
    return [
        {
            "time": format_locator(line.unit, line.seconds),
            "text": line.text,
            "conditions": sorted(set(marks.get(i, {}).get("conditions", []))),
            "universe": sorted(set(marks.get(i, {}).get("universe", []))),
        }
        for i, line in enumerate(lines)
    ]
