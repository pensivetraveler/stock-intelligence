"""Stage 2 — material impact (ADR 0057 §2, U-b): pass, excluded for the day, or unconfirmed.

The verdict is read from the latest news check (check B) of the session: the news-analysis
service's two conclusions (`news_check.conclusions`, `material-judgement-1/2`, news_check
1.1+), never its `status` (a `no_material` check can still have `material: present` when
the LLM found a rally rationale in an article without a rule category).

| conclusions                          | stage 2            | strategy     |
| ------------------------------------ | ------------------ | ------------ |
| present, filter yes                  | excluded for today | none         |
| present, filter no, rationale yes    | passed             | normal       |
| present, filter no, rationale no     | passed (no reason) | conservative |
| present, filter no, rationale other  | passed, unconfirmed| conservative |
| present, filter unknown (or other)   | passed, unconfirmed| conservative |
| none                                 | passed, no material| conservative |
| unknown (service status error)       | passed, unconfirmed| conservative |

No news check, a check still waiting (service down), a failed check, or a result without
`conclusions` (news_check 1.0) is also "unconfirmed": ADR 0057 passes what it cannot judge.

The conclusions are inference (rules plus the service's LLM reading); this module copies
their basis (`rules`, `nir`, `rules_fallback`, an inherited NIR answer) next to the verdict
so the weekly review can see why. Nothing here places or changes an order.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any, Literal

from services.screening.store import ScreeningStore

CHECK_VERSION = "material-stage2-1"
RULES_VERSION = "adr-0057-stage2-1"
LABEL = "재료 영향력(2단계)"
MAX_ARTICLES = 20

Outcome = Literal["passed", "excluded_today"]
Material = Literal["rationale", "no_rationale", "low_impact", "none", "unconfirmed"]
Strategy = Literal["normal", "conservative"]


def _verdict(
    outcome: Outcome,
    material: Material,
    strategy: Strategy | None,
    reason: str,
) -> dict[str, Any]:
    return {
        "label": LABEL,
        "version": CHECK_VERSION,
        "rules": RULES_VERSION,
        "outcome": outcome,
        "material": material,
        "strategy": strategy,
        "reason": reason,
        "conclusions": None,
        "articles": [],
        "news_check_id": None,
        "news_status": None,
        "news_cutoff_utc": None,
    }


def from_conclusions(conclusions: Mapping[str, Any]) -> dict[str, Any]:
    """The verdict for one `news_check.conclusions` block (the table above)."""
    material = conclusions.get("material")
    filter_ = conclusions.get("filter")
    rationale = conclusions.get("rationale")
    if material == "none":
        made = _verdict("passed", "none", "conservative", "no_material")
    elif material == "present" and filter_ == "yes":
        made = _verdict("excluded_today", "low_impact", None, "low_impact_material")
    elif material == "present" and filter_ == "no" and rationale == "yes":
        made = _verdict("passed", "rationale", "normal", "rally_rationale")
    elif material == "present" and filter_ == "no" and rationale == "no":
        # ADR 0057: a material that gives no reason for the rally counts as no material.
        made = _verdict("passed", "no_rationale", "conservative", "no_rally_rationale")
    elif material == "present" and filter_ == "no":
        made = _verdict("passed", "unconfirmed", "conservative", "rationale_unknown")
    elif material == "present":
        made = _verdict("passed", "unconfirmed", "conservative", "filter_unknown")
    else:
        made = _verdict("passed", "unconfirmed", "conservative", "material_unknown")
    made["conclusions"] = {
        "version": conclusions.get("version"),
        "material": material,
        "filter": filter_,
        "rationale": rationale,
    }
    return made


def _articles(check: Mapping[str, Any], conclusions: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Each concluded article's basis, with its headline from the check's articles."""
    by_ref = {
        (item.get("source_id"), item.get("record_id")): item for item in check.get("articles") or ()
    }
    rows = []
    for item in conclusions.get("articles") or ():
        if not isinstance(item, Mapping):
            continue
        ref = item.get("article") or {}
        article = by_ref.get((ref.get("source_id"), ref.get("record_id"))) or {}
        inherited = item.get("nir_inherited_from")
        rows.append(
            {
                "source_id": ref.get("source_id"),
                "record_id": ref.get("record_id"),
                "headline": article.get("headline"),
                "published_at_utc": article.get("published_at_utc"),
                "categories": [str(c) for c in item.get("categories") or ()],
                "filter": item.get("filter"),
                "filter_basis": item.get("filter_basis"),
                "filter_reasons": [str(r) for r in item.get("filter_reasons") or ()],
                "rationale": item.get("rationale"),
                "rationale_basis": item.get("rationale_basis"),
                "nir_record_id": item.get("nir_record_id"),
                "nir_state": item.get("nir_state"),
                "nir_inherited_from": (
                    {
                        "source_id": inherited.get("source_id"),
                        "record_id": inherited.get("record_id"),
                    }
                    if isinstance(inherited, Mapping)
                    else None
                ),
            }
        )
        if len(rows) >= MAX_ARTICLES:
            break
    return rows


def stage2_verdict(stored: Mapping[str, Any] | None) -> dict[str, Any]:
    """Stage 2 from one stored news check (`screening_check`, check `news`), or none."""
    if stored is None:
        return _verdict("passed", "unconfirmed", "conservative", "no_news_check")
    status = str(stored.get("status"))
    result = stored.get("result") or {}
    check = result.get("news_check") if isinstance(result, Mapping) else None
    conclusions = check.get("conclusions") if isinstance(check, Mapping) else None
    if status == "pending":
        made = _verdict("passed", "unconfirmed", "conservative", "news_pending")
    elif not isinstance(check, Mapping):
        made = _verdict("passed", "unconfirmed", "conservative", "news_error")
    elif not isinstance(conclusions, Mapping):
        # news_check 1.0 (before the two conclusions) cannot say what to filter.
        made = _verdict("passed", "unconfirmed", "conservative", "no_conclusions")
    else:
        made = from_conclusions(conclusions)
        made["articles"] = _articles(check, conclusions)
    made["news_check_id"] = stored.get("check_id")
    made["news_status"] = status
    made["news_cutoff_utc"] = stored.get("cutoff_utc")
    return made


def stage2_by_symbol(screening: ScreeningStore, session_date: date) -> dict[str, dict[str, Any]]:
    """Each symbol's stage 2 verdict from its news checks of the session.

    The newest finished check counts; a newer check still waiting does not undo it. A
    symbol with no news check is absent (the caller treats it as `no_news_check`).
    """
    found: dict[str, dict[str, Any]] = {}
    for stored in screening.documents(session_date, "news"):
        symbol = str(stored["symbol"])
        previous = found.get(symbol)
        if stored.get("status") == "pending" and previous and previous.get("status") != "pending":
            continue
        found[symbol] = stored
    return {symbol: stage2_verdict(stored) for symbol, stored in found.items()}


def flow_stage2(verdict: Mapping[str, Any]) -> Literal["passed", "failed"]:
    """The stage 3 view: an unconfirmed material passes (ADR 0057)."""
    return "failed" if verdict.get("outcome") == "excluded_today" else "passed"
