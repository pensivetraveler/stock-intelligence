"""The `off_exchange_structure` screening check (ADR 0053): reads the closed sessions the
model needs (in the background when they are not cached), runs the structure model at the
cutoff, records the forecast in the registry and returns the card's part.

The card never carries research scores: those stay in the registry for the walk-forward
evaluation. A stage (LOW~EXTREME) is shown only for a forecast `ISSUED` under an approved
parameter set.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from services.market_data.calendar import (
    NEW_YORK,
    ExchangeCalendar,
    SessionWindow,
    TradingSessionName,
)
from services.screening.off_exchange import LIMITATIONS as OFF_EXCHANGE_LIMITATIONS
from services.screening.off_exchange import SOURCE_ID
from services.screening.structure.model import (
    GATE_TEXT,
    MODEL_VERSION,
    NOT_APPROVED,
    build_forecast,
)
from services.screening.structure.registry import Registry
from services.screening.structure.sessions import (
    LEVELS_VERSION,
    SessionCache,
    SessionFiller,
    TradePages,
    latest_trade,
    utc,
)

CHECK_VERSION = "off-exchange-structure-check-1"
LABEL = "장외 체결 가격 구조"
MAX_ZONES = 8

LIMITATIONS = (
    *OFF_EXCHANGE_LIMITATIONS,
    "가격대 구조는 장외 체결과 거래소 체결을 합친 가격별 거래량에서 나온다. "
    "구간·빈 구간은 과거 거래가 몰렸거나 비었던 곳이지 가격 전망이 아니다.",
    "단계(LOW~EXTREME)는 승인된 parameter set이 있을 때만 나온다. 승인 전에는 UNKNOWN이다.",
)
UNVERIFIED = (
    "Alpaca 일봉(분할 미조정)의 고가·저가·종가가 정규장만의 값인지 공급자 문서로 확인하지 않았다. "
    "ATR20과 종가 경로에만 쓴다.",
    "원본 모델(darkpool-stress/2.0.0)은 Unusual Whales 가격대별 자료를 썼다. "
    "Alpaca SIP 체결로 바꾼 뒤의 성능은 아직 평가하지 않았다.",
)

#: (symbol, since, until) -> [ProviderSplit], Alpaca corporate actions.
SplitActions = Callable[[str, date, date], list[Any]]
#: (instrument_id, start, end, as_of) -> closed daily bars as traded:
#: [{date, high, low, close}] in Decimal.
DailyBars = Callable[[str, date, date, datetime], list[dict[str, Any]]]


def closed_sessions(
    calendar: ExchangeCalendar, known_until: datetime, count: int
) -> list[SessionWindow]:
    """The `count` latest sessions closed (post-close) by `known_until`, oldest first."""
    found: list[SessionWindow] = []
    day = known_until.astimezone(NEW_YORK).date()
    for _ in range(count * 2 + 15):
        window = calendar.window_for_date(day)
        if window is not None and window.post_close_utc <= known_until:
            found.append(window)
            if len(found) == count:
                break
        day -= timedelta(days=1)
    return list(reversed(found))


def _session_of(calendar: ExchangeCalendar, stamp: str) -> str | None:
    moment = datetime.fromisoformat(stamp[:19] + "+00:00")
    window = calendar.window_for_date(moment.astimezone(NEW_YORK).date())
    if window is None:
        return None
    names: tuple[TradingSessionName, ...] = ("pre", "regular", "post")
    for name in names:
        start, end = window.bounds(name)
        if start <= moment < end:
            return name
    return None


def _corporate_actions(
    split_actions: SplitActions | None, symbol: str, first: date, last: date
) -> dict[str, Any]:
    """Splits with an ex-date after the first session mix price bases (trades are as traded)."""
    if split_actions is None:
        return {"status": "UNRESOLVED", "splits": [], "reason": "no corporate action source"}
    try:
        actions = split_actions(symbol, first, last)
    except Exception as exc:  # noqa: BLE001 - any provider failure leaves this unresolved
        return {"status": "UNRESOLVED", "splits": [], "reason": f"{type(exc).__name__}"[:200]}
    inside = [a for a in actions if first < a.on <= last]
    return {
        "status": "IN_WINDOW" if inside else "PASS",
        "splits": [{"ex_date": a.on.isoformat(), "ratio": str(a.ratio)} for a in inside],
        "reason": None,
        "range": [first.isoformat(), last.isoformat()],
        "source": "alpaca corporate actions",
    }


def _gates(codes: list[str]) -> list[dict[str, str]]:
    return [{"code": code, "text": GATE_TEXT.get(code, code)} for code in codes]


def _card_zones(forecast: dict[str, Any]) -> list[dict[str, Any]]:
    reference = forecast["reference_price"]["value"]
    zones = forecast["active_zones"]
    if reference is not None:
        price = Decimal(reference)
        zones = sorted(zones, key=lambda z: abs(Decimal(z["center_price"]) - price))
    picked = []
    for zone in zones[:MAX_ZONES]:
        side = "at"
        if reference is not None:
            if Decimal(zone["low"]) > Decimal(reference):
                side = "above"
            elif Decimal(zone["high"]) < Decimal(reference):
                side = "below"
        picked.append({**zone, "side": side})
    return sorted(picked, key=lambda z: Decimal(z["low"]))


def structure_check(
    *,
    symbol: str,
    instrument_id: str,
    cutoff: datetime,
    now: datetime,
    publication_delay_minutes: int,
    calendar: ExchangeCalendar,
    cache: SessionCache,
    filler: SessionFiller,
    registry: Registry,
    parameters: dict[str, Any],
    trade_pages: TradePages,
    split_actions: SplitActions | None,
    daily_bars: DailyBars | None,
    wait_seconds: float,
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    """The check-specific part of a `screening_check` (status, result, versions, warnings,
    sources). The caller adds the envelope."""
    if cutoff.tzinfo is None or now.tzinfo is None or cutoff > now:
        raise ValueError("cutoff must be timezone-aware and not after now")
    known_until = min(cutoff, now - timedelta(minutes=publication_delay_minutes)).replace(
        microsecond=0
    )
    required = int(parameters.get("long_window_sessions", 20))
    windows = closed_sessions(calendar, known_until, required)
    filler.request(symbol, windows)
    filler.wait(symbol, wait_seconds, sleep)

    sessions: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    problems: list[str] = []
    for window in windows:
        try:
            document = cache.get(symbol, window.session_date)
        except ValueError as exc:
            problems.append(str(exc)[:200])
            document = None
        if document is None:
            missing.append(
                {
                    "session_date": window.session_date.isoformat(),
                    "reason": filler.error(symbol, window.session_date),
                }
            )
        else:
            sessions.append(document)
    reading = filler.pending(symbol)
    progress = {
        "total_sessions": len(windows),
        "cached_sessions": len(sessions),
        "reading_sessions": reading,
        "missing": missing,
    }
    versions = {
        "check": CHECK_VERSION,
        "model": MODEL_VERSION,
        "session_levels": LEVELS_VERSION,
        "calendar": calendar.calendar_version,
        "parameter_set": str(parameters.get("parameter_set_id")),
    }
    sources = {
        "trades": {
            "source_id": SOURCE_ID,
            "endpoint": "GET /v2/stocks/{symbol}/trades",
            "feed": "sip",
            "off_exchange_code": "D",
            "publication_delay_minutes": publication_delay_minutes,
        },
        "daily_bars": "alpaca daily bars, as traded (raw)",
        "corporate_actions": "alpaca corporate actions (splits)",
        "model": {
            "derived_from": "darkpool-stress/2.0.0",
            "registry": "screening_structure.sqlite3",
        },
    }
    base_result: dict[str, Any] = {
        "label": LABEL,
        "data_until_utc": utc(known_until),
        "progress": progress,
        "limitations": list(LIMITATIONS),
        "unverified": list(UNVERIFIED),
    }
    if reading and missing:
        # Still reading: no forecast yet, the screen asks again.
        return {
            "status": "pending",
            "result": {
                **base_result,
                "reason": f"세션 {len(sessions)}/{len(windows)}개를 받았다. 나머지를 받는 중이다.",
            },
            "versions": versions,
            "warnings": list(LIMITATIONS),
            "sources": sources,
        }

    reference: dict[str, Any] = {
        "value": None,
        "timestamp_utc": None,
        "session": None,
        "source": SOURCE_ID,
    }
    current = calendar.window_for_date(known_until.astimezone(NEW_YORK).date())
    try:
        found = None
        if current is not None and current.post_close_utc > known_until:
            found = latest_trade(trade_pages, symbol, current.pre_open_utc, known_until)
        if found is not None:
            reference["value"], reference["timestamp_utc"] = str(found[0]), found[1]
    except Exception as exc:  # noqa: BLE001 - the gate reports a missing reference price
        problems.append(f"reference price: {type(exc).__name__}"[:200])
    if reference["value"] is None and sessions and sessions[-1]["last_price"] is not None:
        reference["value"] = sessions[-1]["last_price"]
        reference["timestamp_utc"] = sessions[-1]["last_time_utc"]
    if reference["timestamp_utc"]:
        reference["session"] = _session_of(calendar, str(reference["timestamp_utc"]))

    first = windows[0].session_date if windows else known_until.astimezone(NEW_YORK).date()
    corporate = _corporate_actions(
        split_actions, symbol, first, known_until.astimezone(NEW_YORK).date()
    )
    bars: list[dict[str, Any]] = []
    if daily_bars is not None and windows:
        try:
            last = windows[-1].session_date
            bars = [
                bar
                for bar in daily_bars(instrument_id, first - timedelta(days=45), last, known_until)
                if bar["date"] <= last.isoformat()
            ]
        except Exception as exc:  # noqa: BLE001 - only ATR and path features need the bars
            problems.append(f"daily bars: {type(exc).__name__}"[:200])
            bars = []

    forecast, research = build_forecast(
        symbol=symbol,
        instrument_id=instrument_id,
        decision=known_until,
        created_at=now,
        required_dates=[w.session_date for w in windows],
        sessions=sessions,
        bars=bars,
        reference=reference,
        corporate_actions=corporate,
        parameters=parameters,
        input_problems=[p for p in problems if "hash mismatch" in p],
    )
    registry.add_forecast(forecast, research)

    hard = forecast["eligibility"]["hard_failures"]
    if "H05_PROFILE_DENOMINATOR_INVALID" in hard:
        status = "no_data"
    elif [code for code in hard if code not in (NOT_APPROVED, "H11_SCORE_FEATURES_INCOMPLETE")]:
        status = "incomplete"
    else:
        status = "ok"
    issued = forecast["forecast_status"] == "ISSUED"
    notes = [p for p in problems if "hash mismatch" not in p]
    result = {
        **base_result,
        "forecast_id": forecast["forecast_id"],
        "forecast_sha256": forecast["forecast_sha256"],
        "forecast_status": forecast["forecast_status"],
        "stage": forecast["scores"]["stress_level"],
        "deployment_stage": forecast["deployment_stage"],
        "parameter_set_id": forecast["parameter_set_id"],
        "reference_price": forecast["reference_price"],
        "coverage": forecast["coverage"],
        "corporate_actions": corporate,
        "binning": forecast["binning"],
        "zones": _card_zones(forecast),
        "zone_count": len(forecast["active_zones"]),
        "gaps": forecast["gaps"],
        "features": forecast["features"],
        "scores": forecast["scores"] if issued else None,
        "expected_responses": forecast["expected_responses"],
        "eligibility": {
            "status": forecast["eligibility"]["status"],
            "hard_failures": _gates(hard),
            "soft_failures": _gates(forecast["eligibility"]["soft_failures"]),
        },
        "notes": notes,
    }
    warnings = [*LIMITATIONS, *notes]
    if forecast["deployment_stage"] == "SHADOW_APPROVED" and issued:
        warnings.append("shadow 단계(운영 승인 전) parameter set의 단계다. 아직 검증 중이다.")
    return {
        "status": status,
        "result": result,
        "versions": versions,
        "warnings": warnings,
        "sources": sources,
    }
