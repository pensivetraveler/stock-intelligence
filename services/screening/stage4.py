"""Stage 4 — making the information (ADR 0057 §4, U-c): what the next stage can read.

Stage 4 removes nothing. For every candidate that passed stage 2 ("재료 미확인" passes
too) it gathers three facts per session:

- dilution (check C, rules only, `llm=false`): the prices where new shares can appear,
  as the analyzer reported them. Turning them into price zones is stage 5's job.
- off-exchange (check D): TRF-reported volume peaks over the closed sessions and today.
- short interest: no source yet, so it is left out and says so (`available: false`).

This module only reads stored checks; `services/api/screening_stage4.py` starts them.
Each part keeps its own cutoff and creation time, and an `as_of` cut drops any check made
after it, so a view at an earlier time never shows a later result. Nothing here places,
changes or approves an order.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import Any

from services.screening.candidates import utc_text
from services.screening.material import stage2_verdict
from services.screening.store import ScreeningStore

VERSION = "stage4-info-1"
RULES_VERSION = "adr-0057-stage4-1"
LABEL = "정보 만들기(4단계)"
REQUESTED_BY_RUNNER = "stage4_auto"
SHORT_INTEREST_NOTE = "공매도 자료 없음: 자료가 생길 때까지 공매도 없이 진행한다(ADR 0057 4단계)."
DILUTION_DATE_NOTE = "희석 분석은 뉴욕 날짜 기준이다: 그날 cutoff 뒤에 낸 공시가 들어 있을 수 있다."
DILUTION_LATER_NOTE = (
    "희석 분석 기준일이 세션 날짜보다 늦다: 그 세션 뒤의 공시가 들어 있을 수 있다(미래 자료)."
)
FACT_NOTE = "4단계 결과는 사실이다. 가격대로 바꾸는 것은 5단계 규칙이다(아직 없음)."
MAX_LEVELS = 50


def request_note(verdict: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    """Stored inside the check's result: who started it and the stage 2 verdict then."""
    return {
        "requested_by": REQUESTED_BY_RUNNER,
        "requested_at_utc": utc_text(now),
        "stage2_outcome": verdict.get("outcome"),
        "stage2_reason": verdict.get("reason"),
        "stage2_news_check_id": verdict.get("news_check_id"),
        "stage2_news_cutoff_utc": verdict.get("news_cutoff_utc"),
    }


def _requested_by(document: Mapping[str, Any]) -> str:
    note = (document.get("result") or {}).get("stage4")
    if isinstance(note, Mapping) and note.get("requested_by") == REQUESTED_BY_RUNNER:
        return REQUESTED_BY_RUNNER
    return "user"


def pick(documents: Iterable[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The newest settled check; the newest waiting one only when none settled."""
    settled: Mapping[str, Any] | None = None
    waiting: Mapping[str, Any] | None = None
    for document in documents:
        if document.get("status") == "pending":
            waiting = document
        else:
            settled = document
    return settled if settled is not None else waiting


def rules_only(document: Mapping[str, Any]) -> bool:
    """Stage 4 reads rules-only analyses; an LLM one belongs to the weekly review."""
    return (document.get("result") or {}).get("llm") is False


def _dilution(document: Mapping[str, Any]) -> dict[str, Any]:
    result = document.get("result") or {}
    meta = result.get("meta") or {}
    return {
        "check_id": document["check_id"],
        "status": document["status"],
        "cutoff_utc": document["cutoff_utc"],
        "created_at_utc": document["created_at_utc"],
        "requested_by": _requested_by(document),
        "analysis_as_of": meta.get("as_of") or document["session_date"],
        "securities": list(result.get("securities") or []),
        "capacity": list(result.get("capacity") or []),
        "shelves": list(result.get("shelves") or []),
        "incomplete_reasons": [str(item) for item in result.get("incomplete_reasons") or []],
        "reason": result.get("reason"),
    }


def _off_exchange(document: Mapping[str, Any]) -> dict[str, Any]:
    result = document.get("result") or {}
    return {
        "check_id": document["check_id"],
        "status": document["status"],
        "cutoff_utc": document["cutoff_utc"],
        "created_at_utc": document["created_at_utc"],
        "requested_by": _requested_by(document),
        "data_until_utc": result.get("data_until_utc"),
        "reference_price": result.get("reference_price"),
        "reference_time_utc": result.get("reference_time_utc"),
        "off_exchange_share_pct": result.get("off_exchange_share_pct"),
        "sessions_used": len(result.get("sessions") or []),
        "levels": list(result.get("levels") or [])[:MAX_LEVELS],
        "reason": result.get("reason"),
    }


def stage4_summary(
    verdict: Mapping[str, Any],
    dilution: Mapping[str, Any] | None,
    off_exchange: Mapping[str, Any] | None,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """One candidate's stage 4 information from its chosen checks."""
    if verdict.get("outcome") == "excluded_today":
        state = "stage2_excluded"
    elif dilution is None and off_exchange is None:
        state = "not_started"
    elif (
        dilution is None
        or off_exchange is None
        or dilution.get("status") == "pending"
        or off_exchange.get("status") == "pending"
    ):
        state = "waiting"
    else:
        state = "done"
    notes = [FACT_NOTE]
    made = None if dilution is None else _dilution(dilution)
    if dilution is not None and made is not None:
        notes.append(DILUTION_DATE_NOTE)
        if str(made["analysis_as_of"]) > str(dilution.get("session_date")):
            notes.append(DILUTION_LATER_NOTE)
    return {
        "label": LABEL,
        "version": VERSION,
        "rules": RULES_VERSION,
        "state": state,
        "as_of_utc": None if as_of is None else utc_text(as_of),
        "stage2_outcome": verdict.get("outcome"),
        "dilution": made,
        "off_exchange": None if off_exchange is None else _off_exchange(off_exchange),
        "short_interest": {"available": False, "reason": "no_source", "note": SHORT_INTEREST_NOTE},
        "notes": notes,
    }


def stage4_by_symbol(
    screening: ScreeningStore,
    session_date: date,
    verdicts: Mapping[str, Mapping[str, Any]],
    symbols: Iterable[str],
    as_of: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    """Each candidate's stage 4 information; checks made after `as_of` are left out."""
    cut = None if as_of is None else utc_text(as_of)

    def by_symbol(kind: str) -> dict[str, list[dict[str, Any]]]:
        found: dict[str, list[dict[str, Any]]] = {}
        for document in screening.documents(session_date, kind):
            if cut is not None and str(document["created_at_utc"]) > cut:
                continue
            if kind == "dilution" and not rules_only(document):
                continue
            found.setdefault(str(document["symbol"]), []).append(document)
        return found

    dilution, off_exchange = by_symbol("dilution"), by_symbol("off_exchange")
    return {
        symbol: stage4_summary(
            verdicts.get(symbol) or stage2_verdict(None),
            pick(dilution.get(symbol, [])),
            pick(off_exchange.get(symbol, [])),
            as_of,
        )
        for symbol in symbols
    }
