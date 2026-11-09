"""The structure model: dark-pool stress model v2 (`darkpool-stress/2.0.0`, darkpool-analysis
`scripts/build_stress_forecast.py`, 2026-11-09) moved onto Alpaca SIP session levels (ADR 0053).

Same steps and gates as the original, computed in Decimal:

adaptive fine/coarse bins -> time-decayed price profile -> percentile zones -> resolution
stability -> directional gaps -> short/long zone stability -> close path features -> robust
off/lit anomaly -> scores and band -> hard/soft gates.

What changed from the original, and why (ADR 0053):
- Input is volume by exact trade price per closed session, off-exchange (`D`) and lit,
  from Alpaca SIP trades instead of Unusual Whales price levels.
- Under $1 the tick is $0.0001 (sub-penny quotes are allowed there), not $0.01.
- Scores are only a forecast (`ISSUED`) under a parameter set the registry approved. When
  every gate passes except approval, the same scores are kept apart as a research record
  for the walk-forward evaluation; they never reach the screen.
- Hash: SHA-256 of sorted compact JSON (every number is a string), not RFC 8785.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext
from typing import Any

from services.market_data.calendar import NEW_YORK
from services.screening.structure.sessions import canonical_sha256, text, utc

MODEL_VERSION = "darkpool-stress-siw/2.0.0"
DERIVED_FROM = "darkpool-stress/2.0.0"
FEATURE_VERSION = "darkpool-stress-siw-features/2.0.0"
FORECAST_HASH_VERSION = "darkpool-stress-siw-forecast/2.0"
SCHEMA_VERSION = "2.0-siw.1"
APPROVED_STATUSES = frozenset({"SHADOW_APPROVED", "PRODUCTION_APPROVED"})
SOURCE_SCOPE = "ALPACA_SIP_OFF_EXCHANGE_D_PLUS_LIT"
SUB_DOLLAR_TICK = Decimal("0.0001")
NOT_APPROVED = "H10_PARAMETER_SET_NOT_OPERATIONALLY_APPROVED"
RECONSTRUCTED_AFTER = timedelta(hours=1)

ZERO = Decimal(0)
ONE = Decimal(1)
BPS = Decimal(10000)
RATIO_PLACES = Decimal("1e-8")

#: What each gate means, for the card (ADR 0053).
GATE_TEXT = {
    "H00_INPUT_INCOMPLETE": "받아야 할 세션을 다 받지 못했다",
    "H01_REFERENCE_PRICE_MISSING": "cutoff 전 기준 가격(마지막 체결)이 없다",
    "H01_REFERENCE_PRICE_TIME_OR_SESSION_MISSING": "기준 가격의 시각이나 세션을 모른다",
    "H03_POST_DECISION_FEATURE_ROW": "cutoff 뒤 자료가 섞였다",
    "H04_CORPORATE_ACTION_UNRESOLVED": "기간 안 분할·역분할 여부를 확인하지 못했다",
    "H04_CORPORATE_ACTION_IN_WINDOW": "기간 안에 분할·역분할이 있어 가격 기준이 섞인다",
    "H05_PROFILE_DENOMINATOR_INVALID": "가격대별 거래량이 없다",
    "H07_INSUFFICIENT_COMPLETE_SESSION_COVERAGE": "완전한 세션이 부족하다(10개·80% 미만)",
    "H09_INPUT_HASH_MISMATCH": "저장한 세션 자료의 해시가 맞지 않는다",
    "H10_PARAMETER_SET_NOT_OPERATIONALLY_APPROVED": (
        "승인된 parameter set이 없어 단계를 내지 않는다"
    ),
    "H11_SCORE_FEATURES_INCOMPLETE": "점수에 필요한 값이 비어 있다",
    "H12_FORECAST_CREATED_BEFORE_DECISION": "만든 시각이 기준 시각보다 앞선다",
    "S04_SOURCE_SCOPE_LIMITED": "체결 출처가 Alpaca SIP 하나뿐이다",
    "S08_ATR20_UNAVAILABLE_PRICE_ONLY_BIN": "일봉 ATR20이 없어 가격만으로 구간 폭을 정했다",
    "S09_RECONSTRUCTED_AFTER_CUTOFF": (
        "cutoff 뒤에 받은 체결로 다시 만들었다(취소·정정 표시가 그때와 다를 수 있다)"
    ),
}


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except ArithmeticError:
        return None


def ratio(value: Decimal | None) -> str | None:
    return None if value is None else text(value.quantize(RATIO_PLACES))


def _percentile(values: list[Decimal], q: Decimal) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = int(position.to_integral_value(rounding=ROUND_FLOOR))
    high = int(position.to_integral_value(rounding=ROUND_CEILING))
    if low == high:
        return ordered[low]
    return ordered[low] * (high - position) + ordered[high] * (position - low)


def _round_up(value: Decimal, tick: Decimal) -> Decimal:
    return (value / tick).to_integral_value(rounding=ROUND_CEILING) * tick


def tick_for(reference: Decimal, minimum_tick: Decimal) -> Decimal:
    return SUB_DOLLAR_TICK if reference < ONE else minimum_tick


def _atr20(bars: list[dict[str, Any]]) -> Decimal | None:
    complete = sorted(
        (str(bar["date"]), bar["high"], bar["low"], bar["close"])
        for bar in bars
        if min(bar["high"], bar["low"], bar["close"]) > 0
    )
    if len(complete) < 2:
        return None
    ranges = []
    for index in range(1, len(complete)):
        _, high, low, _ = complete[index]
        previous = complete[index - 1][3]
        ranges.append(max(high - low, abs(high - previous), abs(low - previous)))
    sample = ranges[-20:]
    return sum(sample, ZERO) / len(sample)


def _aggregate(
    rows: list[tuple[date, Decimal, Decimal, Decimal]],
    width: Decimal,
    half_life: Decimal,
    decision_date: date,
) -> list[dict[str, Any]]:
    """(session date, price, off, lit) rows -> time-decayed profile by price bin."""
    dates = sorted({row[0] for row in rows})
    age = {day: len([other for other in dates if day < other <= decision_date]) for day in dates}
    weights = {day: Decimal(2) ** (-(Decimal(age[day]) / half_life)) for day in dates}
    buckets: dict[Decimal, dict[str, Any]] = defaultdict(
        lambda: {"off": ZERO, "lit": ZERO, "raw": ZERO, "dates": defaultdict(lambda: ZERO)}
    )
    for day, price, off, lit in rows:
        low = (price / width).to_integral_value(rounding=ROUND_FLOOR) * width
        bucket = buckets[low]
        bucket["off"] += off * weights[day]
        bucket["lit"] += lit * weights[day]
        bucket["raw"] += off + lit
        bucket["dates"][day.isoformat()] += off + lit
    total = sum((v["off"] + v["lit"] for v in buckets.values()), ZERO)
    off_total = sum((v["off"] for v in buckets.values()), ZERO)
    profile = []
    for low in sorted(buckets):
        value = buckets[low]
        combined = value["off"] + value["lit"]
        profile.append(
            {
                "low": low,
                "high": low + width,
                "decayed_off": value["off"],
                "decayed_lit": value["lit"],
                "decayed_volume": combined,
                "raw_volume": value["raw"],
                "density": combined / total if total else None,
                "off_density": value["off"] / off_total if off_total else None,
                "dates": dict(value["dates"]),
            }
        )
    return profile


def _zones(
    profile: list[dict[str, Any]], percentile: Decimal, reference: Decimal
) -> list[dict[str, Any]]:
    density_cut = _percentile(
        [r["density"] for r in profile if r["density"] is not None], percentile
    )
    off_cut = _percentile(
        [r["off_density"] for r in profile if r["off_density"] is not None], percentile
    )
    candidates = [
        row
        for row in profile
        if (
            density_cut is not None and row["density"] is not None and row["density"] >= density_cut
        )
        or (
            off_cut is not None and row["off_density"] is not None and row["off_density"] >= off_cut
        )
    ]
    merged: list[dict[str, Any]] = []
    for row in candidates:
        if merged and merged[-1]["high"] == row["low"]:
            zone = merged[-1]
            zone["high"] = row["high"]
            zone["decayed_volume"] += row["decayed_volume"]
            zone["decayed_off"] += row["decayed_off"]
            for day, value in row["dates"].items():
                zone["contributions"][day] = zone["contributions"].get(day, ZERO) + value
        else:
            merged.append(
                {
                    "low": row["low"],
                    "high": row["high"],
                    "decayed_volume": row["decayed_volume"],
                    "decayed_off": row["decayed_off"],
                    "contributions": dict(row["dates"]),
                }
            )
    total = sum((row["decayed_volume"] for row in profile), ZERO)
    zones = []
    for index, zone in enumerate(merged, 1):
        contributions = zone.pop("contributions")
        raw_total = sum(contributions.values(), ZERO)
        zones.append(
            {
                "zone_id": f"Z{index}",
                "low": zone["low"],
                "high": zone["high"],
                "center_price": (zone["low"] + zone["high"]) / 2,
                "width_bps": BPS * (zone["high"] - zone["low"]) / reference,
                "decayed_volume": zone["decayed_volume"],
                "period_share": zone["decayed_volume"] / total if total else None,
                "off_share": zone["decayed_off"] / zone["decayed_volume"]
                if zone["decayed_volume"]
                else None,
                "active_session_count": len(contributions),
                "dominant_day_share": max(contributions.values()) / raw_total
                if raw_total
                else None,
                "last_contribution_date": max(contributions) if contributions else None,
            }
        )
    return zones


def _overlap(a: dict[str, Any], b: dict[str, Any]) -> Decimal:
    intersection = max(ZERO, min(a["high"], b["high"]) - max(a["low"], b["low"]))
    union = max(a["high"], b["high"]) - min(a["low"], b["low"])
    return intersection / union if union else ZERO


def _resolution_stability(
    fine: list[dict[str, Any]], coarse: list[dict[str, Any]], threshold: Decimal
) -> None:
    for zone in fine:
        best = max((_overlap(zone, other) for other in coarse), default=ZERO)
        zone["resolution_stable"] = best >= threshold
        zone["coarse_overlap"] = best


def _gap(
    direction: str, reference: Decimal, zones: list[dict[str, Any]], profile: list[dict[str, Any]]
) -> dict[str, Any]:
    gap_low: Decimal | None
    gap_high: Decimal | None
    if direction == "up":
        above = sorted((z for z in zones if z["low"] > reference), key=lambda z: z["low"])
        boundary = above[0]["low"] if above else None
        gap_low, gap_high = reference, boundary
    else:
        below = sorted((z for z in zones if z["high"] < reference), key=lambda z: -z["high"])
        boundary = below[0]["high"] if below else None
        gap_low, gap_high = boundary, reference
    if boundary is None or gap_low is None or gap_high is None:
        return {
            "direction": direction,
            "gap_is_open_ended": True,
            "gap_low": None,
            "gap_high": None,
            "gap_width_bps": None,
            "gap_density_ratio": None,
            "next_zone_distance_bps": None,
        }
    inside = [
        r["density"]
        for r in profile
        if r["density"] is not None and r["low"] >= gap_low and r["high"] <= gap_high
    ]
    median = _percentile(
        [r["density"] for r in profile if r["density"] is not None], Decimal("0.5")
    )
    return {
        "direction": direction,
        "gap_is_open_ended": False,
        "gap_low": gap_low,
        "gap_high": gap_high,
        "gap_width_bps": BPS * abs(gap_high - gap_low) / reference,
        "gap_density_ratio": (sum(inside, ZERO) / len(inside)) / median
        if inside and median
        else None,
        "next_zone_distance_bps": BPS * abs(boundary - reference) / reference,
    }


def _distance(zone: dict[str, Any], reference: Decimal) -> Decimal:
    low: Decimal = zone["low"]
    high: Decimal = zone["high"]
    if low <= reference <= high:
        return ZERO
    return min(abs(reference - low), abs(reference - high))


def _nearest(reference: Decimal, zones: list[dict[str, Any]]) -> dict[str, Any] | None:
    return min(zones, key=lambda zone: _distance(zone, reference)) if zones else None


def _zone_stability(
    rows: list[tuple[date, Decimal, Decimal, Decimal]],
    width: Decimal,
    half_life: Decimal,
    percentile: Decimal,
    reference: Decimal,
    decision_date: date,
    short_sessions: int,
    long_sessions: int,
    threshold: Decimal,
) -> list[dict[str, Any]]:
    dates = sorted({row[0] for row in rows})
    short_dates, long_dates = set(dates[-short_sessions:]), set(dates[-long_sessions:])
    short_rows = [row for row in rows if row[0] in short_dates]
    long_rows = [row for row in rows if row[0] in long_dates]
    short = (
        _zones(_aggregate(short_rows, width, half_life, decision_date), percentile, reference)
        if short_rows
        else []
    )
    long = (
        _zones(_aggregate(long_rows, width, half_life, decision_date), percentile, reference)
        if long_rows
        else []
    )
    result = []
    for zone in short:
        matches = sorted(((other, _overlap(zone, other)) for other in long), key=lambda m: -m[1])
        matched, overlap = matches[0] if matches else (None, ZERO)
        mass_change = None
        migration = None
        if matched is not None:
            if matched.get("period_share") and zone.get("period_share") is not None:
                mass_change = zone["period_share"] / matched["period_share"] - 1
            migration = BPS * abs(zone["center_price"] - matched["center_price"]) / reference
        if matched is None:
            state = "NEW"
        elif overlap < threshold:
            state = "MIGRATING"
        else:
            state = "MATCHED_UNCALIBRATED"
        result.append(
            {
                "zone_id": zone["zone_id"],
                "state": state,
                "short_long_overlap": ratio(overlap),
                "mass_change": ratio(mass_change),
                "center_migration_bps": ratio(migration),
            }
        )
    return result


def _path_features(zones: list[dict[str, Any]], bars: list[dict[str, Any]]) -> dict[str, Any]:
    closes = sorted((str(bar["date"]), bar["close"]) for bar in bars)[-20:]
    reentries = crossings = holds = 0
    for zone in zones:
        previous = None
        for _, close in closes:
            if zone["low"] <= close <= zone["high"]:
                state = "inside"
            elif close < zone["low"]:
                state = "below"
            else:
                state = "above"
            if previous in ("below", "above") and state == "inside":
                reentries += 1
            if previous in ("below", "above") and state in ("below", "above") and previous != state:
                crossings += 1
                holds += 1
            previous = state
    return {
        "reentry_count_20": reentries if closes else None,
        "cross_count_20": crossings if closes else None,
        "close_hold_rate": ratio(Decimal(holds) / crossings) if crossings else None,
    }


def _robust_z(values: list[Decimal]) -> Decimal | None:
    if len(values) < 3:
        return None
    history = values[:-1]
    median = _percentile(history, Decimal("0.5"))
    if median is None:
        return None
    mad = _percentile([abs(value - median) for value in history], Decimal("0.5"))
    return (values[-1] - median) / (Decimal("1.4826") * mad) if mad else None


def _off_lit_anomaly(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    daily = sorted(
        (
            str(s["session_date"]),
            sum((Decimal(level[1]) for level in s["levels"]), ZERO),
            sum((Decimal(level[2]) for level in s["levels"]), ZERO),
        )
        for s in sessions
    )
    if not daily:
        return {"off_share_robust_z": None, "off_volume_robust_z": None, "status": "NOT_EVALUATED"}
    shares = [off / (off + lit) for _, off, lit in daily if off + lit]
    volumes = [(ONE + off).ln() for _, off, _ in daily]
    return {
        "off_share_robust_z": ratio(_robust_z(shares)),
        "off_volume_robust_z": ratio(_robust_z(volumes)),
        "status": "EVALUATED" if len(daily) >= 3 else "NOT_EVALUATED",
    }


def stress_band(score: Decimal | None, cut_points: dict[str, Any] | None) -> str:
    if score is None or not cut_points:
        return "UNKNOWN"
    if score < Decimal(str(cut_points["p50"])):
        return "LOW"
    if score < Decimal(str(cut_points["p75"])):
        return "MODERATE"
    if score < Decimal(str(cut_points["p90"])):
        return "HIGH"
    return "EXTREME"


def _response_rank(zone: dict[str, Any], gap: dict[str, Any] | None) -> dict[str, str]:
    mass = zone.get("period_share") or ZERO
    stable = bool(zone.get("resolution_stable"))
    density = (gap or {}).get("gap_density_ratio")
    if stable and mass >= Decimal("0.15"):
        rejection = "HIGH"
    else:
        rejection = "MODERATE" if mass >= Decimal("0.05") else "LOW"
    if density is not None and density < Decimal("0.5"):
        transit = "HIGH"
    else:
        transit = "MODERATE" if density is not None and density < 1 else "LOW"
    if stable and zone.get("active_session_count", 0) >= 5:
        acceptance = "HIGH"
    else:
        acceptance = "MODERATE" if stable else "LOW"
    return {
        "zone_id": zone["zone_id"],
        "rejection_rank": rejection,
        "acceptance_rank": acceptance,
        "transit_rank": transit,
    }


def _sigmoid(value: Decimal) -> Decimal:
    clamped = max(Decimal(-30), min(Decimal(30), value))
    return ONE / (ONE + (-clamped).exp())


def _score(
    gap: dict[str, Any],
    nearest: dict[str, Any] | None,
    reference: Decimal,
    coefficients: dict[str, Any],
) -> Decimal | None:
    if nearest is None:
        return None
    values: dict[str, Decimal | None] = {
        "intercept": ONE,
        "distance_to_zone_bps": BPS * _distance(nearest, reference) / reference,
        "zone_relative_mass": nearest.get("period_share"),
        "gap_width_bps": gap.get("gap_width_bps"),
        "gap_density_ratio": gap.get("gap_density_ratio"),
        "next_zone_distance_bps": gap.get("next_zone_distance_bps"),
    }
    if any(values.get(key) is None for key in coefficients if key != "intercept"):
        return None
    linear = sum(
        (Decimal(str(weight)) * values[key] for key, weight in coefficients.items()),  # type: ignore[operator]
        ZERO,
    )
    return 100 * _sigmoid(linear)


def _zone_out(zone: dict[str, Any]) -> dict[str, Any]:
    return {
        "zone_id": zone["zone_id"],
        "low": text(zone["low"]),
        "high": text(zone["high"]),
        "center_price": text(zone["center_price"]),
        "width_bps": ratio(zone["width_bps"]),
        "period_share": ratio(zone["period_share"]),
        "off_share": ratio(zone["off_share"]),
        "active_session_count": zone["active_session_count"],
        "dominant_day_share": ratio(zone["dominant_day_share"]),
        "last_contribution_date": zone["last_contribution_date"],
        "resolution_stable": zone.get("resolution_stable"),
        "coarse_overlap": ratio(zone.get("coarse_overlap")),
    }


def _gap_out(gap: dict[str, Any] | None) -> dict[str, Any] | None:
    if gap is None:
        return None
    return {
        key: (ratio(value) if key.endswith(("_bps", "_ratio")) else text(value))
        if isinstance(value, Decimal)
        else value
        for key, value in gap.items()
    }


def parameter_sha256(parameters: dict[str, Any]) -> str:
    return canonical_sha256(parameters)


def forecast_sha256(forecast: dict[str, Any]) -> str:
    return canonical_sha256({k: v for k, v in forecast.items() if k != "forecast_sha256"})


def build_forecast(
    *,
    symbol: str,
    instrument_id: str,
    decision: datetime,
    created_at: datetime,
    required_dates: list[date],
    sessions: list[dict[str, Any]],
    bars: list[dict[str, Any]],
    reference: dict[str, Any],
    corporate_actions: dict[str, Any],
    parameters: dict[str, Any],
    input_problems: list[str] | None = None,
    forecast_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Returns the forecast document and, when only approval is missing, the research
    scores (kept apart, never shown). `bars` are closed regular-session daily bars as
    traded: {date, high, low, close} in Decimal."""
    with localcontext() as context:
        context.prec = 34
        return _build(
            symbol=symbol,
            instrument_id=instrument_id,
            decision=decision,
            created_at=created_at,
            required_dates=required_dates,
            sessions=sessions,
            bars=bars,
            reference=reference,
            corporate_actions=corporate_actions,
            parameters=parameters,
            input_problems=input_problems or [],
            forecast_id=forecast_id,
        )


def _build(
    *,
    symbol: str,
    instrument_id: str,
    decision: datetime,
    created_at: datetime,
    required_dates: list[date],
    sessions: list[dict[str, Any]],
    bars: list[dict[str, Any]],
    reference: dict[str, Any],
    corporate_actions: dict[str, Any],
    parameters: dict[str, Any],
    input_problems: list[str],
    forecast_id: str | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if decision.tzinfo is None or created_at.tzinfo is None:
        raise ValueError("decision and created_at must be timezone-aware")
    hard: list[str] = []
    soft: list[str] = ["S04_SOURCE_SCOPE_LIMITED"]
    # Session dates are New York dates.
    decision_date = decision.astimezone(NEW_YORK).date()
    if created_at < decision:
        hard.append("H12_FORECAST_CREATED_BEFORE_DECISION")
    if input_problems:
        hard.append("H00_INPUT_INCOMPLETE")
    for session in sessions:
        body = {k: v for k, v in session.items() if k != "sha256"}
        if canonical_sha256(body) != session.get("sha256"):
            hard.append("H09_INPUT_HASH_MISMATCH")
        if session["range_utc"][1] > utc(decision):
            hard.append("H03_POST_DECISION_FEATURE_ROW")
        # Read well after the decision (D's rule: an hour past the delay-adjusted cutoff).
        if session["read_at_utc"] > utc(decision + RECONSTRUCTED_AFTER):
            soft.append("S09_RECONSTRUCTED_AFTER_CUTOFF")
    if corporate_actions.get("status") == "IN_WINDOW":
        hard.append("H04_CORPORATE_ACTION_IN_WINDOW")
    elif corporate_actions.get("status") != "PASS":
        hard.append("H04_CORPORATE_ACTION_UNRESOLVED")
    reference_price = _dec(reference.get("value"))
    if reference_price is None or reference_price <= 0:
        hard.append("H01_REFERENCE_PRICE_MISSING")
        reference_price = None
    elif not reference.get("timestamp_utc") or not reference.get("session"):
        hard.append("H01_REFERENCE_PRICE_TIME_OR_SESSION_MISSING")
    if reference.get("timestamp_utc") and str(reference["timestamp_utc"]) > utc(decision):
        hard.append("H03_POST_DECISION_FEATURE_ROW")

    expected = {day.isoformat() for day in required_dates}
    available = {str(s["session_date"]) for s in sessions}
    covered = expected & available
    coverage_ratio = Decimal(len(covered)) / len(expected) if expected else None
    minimum_sessions = int(parameters.get("minimum_complete_sessions", 10))
    minimum_ratio = Decimal(str(parameters.get("minimum_coverage_ratio", "0.8")))
    if len(covered) < minimum_sessions or coverage_ratio is None or coverage_ratio < minimum_ratio:
        hard.append("H07_INSUFFICIENT_COMPLETE_SESSION_COVERAGE")
    rows = [
        (date.fromisoformat(str(s["session_date"])), Decimal(p), Decimal(off), Decimal(lit))
        for s in sessions
        for p, off, lit in s["levels"]
    ]
    if not rows or sum((off + lit for _, _, off, lit in rows), ZERO) <= 0:
        hard.append("H05_PROFILE_DENOMINATOR_INVALID")
    if parameters.get("approval_status") not in APPROVED_STATUSES:
        hard.append(NOT_APPROVED)

    atr = _atr20(bars)
    if atr is None:
        soft.append("S08_ATR20_UNAVAILABLE_PRICE_ONLY_BIN")
    zones: list[dict[str, Any]] = []
    fine: list[dict[str, Any]] = []
    gaps: dict[str, dict[str, Any] | None] = {"up": None, "down": None}
    width: Decimal | None = None
    tick: Decimal | None = None
    stability: list[dict[str, Any]] = []
    path: dict[str, Any] = {
        "reentry_count_20": None,
        "cross_count_20": None,
        "close_hold_rate": None,
    }
    anomaly: dict[str, Any] = {
        "off_share_robust_z": None,
        "off_volume_robust_z": None,
        "status": "NOT_EVALUATED",
    }
    if reference_price is not None and rows:
        tick = tick_for(reference_price, Decimal(str(parameters.get("minimum_tick", "0.01"))))
        width = _round_up(
            max(reference_price * Decimal("0.0025"), (atr / 20) if atr else ZERO, tick), tick
        )
        half_life = Decimal(str(parameters["half_life_sessions"]))
        percentile = Decimal(str(parameters["zone_density_percentile"]))
        threshold = Decimal(str(parameters["resolution_overlap_threshold"]))
        fine = _aggregate(rows, width, half_life, decision_date)
        coarse = _aggregate(rows, width * 2, half_life, decision_date)
        zones = _zones(fine, percentile, reference_price)
        _resolution_stability(zones, _zones(coarse, percentile, reference_price), threshold)
        gaps = {d: _gap(d, reference_price, zones, fine) for d in ("up", "down")}
        stability = _zone_stability(
            rows,
            width,
            half_life,
            percentile,
            reference_price,
            decision_date,
            int(parameters.get("short_window_sessions", 5)),
            int(parameters.get("long_window_sessions", 20)),
            threshold,
        )
        path = _path_features(zones, bars)
        anomaly = _off_lit_anomaly(sessions)

    horizon = int(parameters.get("horizon_sessions", 5))
    empty_scores: dict[str, Any] = {
        "stress_up": None,
        "stress_down": None,
        "path_instability": None,
        "directional_asymmetry": None,
        "zone_interaction_rank": "UNKNOWN",
        "stress_level": "UNKNOWN",
        "confidence": "UNKNOWN",
        "horizon_sessions": horizon,
    }
    computed: dict[str, Any] | None = None
    gates_but_approval = [code for code in hard if code != NOT_APPROVED]
    if not gates_but_approval and reference_price is not None:
        nearest = _nearest(reference_price, zones)
        up = _score(gaps["up"] or {}, nearest, reference_price, parameters["coefficients"]["up"])
        down = _score(
            gaps["down"] or {}, nearest, reference_price, parameters["coefficients"]["down"]
        )
        if up is None or down is None:
            hard.append("H11_SCORE_FEATURES_INCOMPLETE")
        else:
            instability = max(up, down)
            computed = {
                "stress_up": ratio(up),
                "stress_down": ratio(down),
                "path_instability": ratio(instability),
                "directional_asymmetry": ratio(up - down),
                "zone_interaction_rank": "PROVISIONAL_RANK",
                "stress_level": stress_band(instability, parameters.get("band_cut_points")),
                "confidence": "MODERATE" if soft else "HIGH",
                "horizon_sessions": horizon,
            }
    eligible = not hard
    issued = eligible and computed is not None
    soft = sorted(set(soft))
    forecast: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "forecast_hash_version": FORECAST_HASH_VERSION,
        "forecast_id": forecast_id or str(uuid.uuid4()),
        "model_version": MODEL_VERSION,
        "derived_from": DERIVED_FROM,
        "feature_version": FEATURE_VERSION,
        "analysis_mode": "AS_OF_SIGNAL",
        "symbol": symbol,
        "instrument_id": instrument_id,
        "decision_timestamp_utc": utc(decision),
        "feature_cutoff_utc": max((str(s["range_utc"][1]) for s in sessions), default=None),
        "forecast_created_at_utc": utc(created_at),
        "forecast_status": "ISSUED" if issued else "NOT_ISSUED",
        "forecast_valid_from_utc": utc(decision) if issued else None,
        "forecast_expires_after_complete_sessions": horizon if issued else None,
        "reference_price": {
            "value": None if reference_price is None else text(reference_price),
            "timestamp_utc": reference.get("timestamp_utc"),
            "session": reference.get("session"),
            "source": reference.get("source"),
        },
        "corporate_actions": corporate_actions,
        "eligibility": {
            "status": "PASS" if issued else "UNKNOWN",
            "hard_failures": sorted(set(hard)),
            "soft_failures": soft,
            "input_problems": input_problems,
        },
        "coverage": {
            "expected_complete_sessions": len(expected),
            "available_complete_sessions": len(covered),
            "coverage_ratio": ratio(coverage_ratio),
            "missing_sessions": sorted(expected - available),
            "source_scope": SOURCE_SCOPE,
        },
        "scores": computed if issued and computed is not None else empty_scores,
        "binning": {
            "tick": None if tick is None else text(tick),
            "fine_width": None if width is None else text(width),
            "coarse_width": None if width is None else text(width * 2),
            "atr20": None if atr is None else ratio(atr),
        },
        "features": {"zone_stability": stability, "path": path, "off_lit_anomaly": anomaly},
        "active_zones": [_zone_out(zone) for zone in zones],
        "expected_responses": [
            _response_rank(z, gaps["up"] if z["low"] > (reference_price or ZERO) else gaps["down"])
            for z in zones
        ]
        if issued
        else [],
        "gaps": {direction: _gap_out(gap) for direction, gap in gaps.items()},
        "activation_conditions": [],
        "relief_conditions": [],
        "invalidation_conditions": [
            "Source revision or evidence hash mismatch",
            "Corporate action adjustment basis becomes unresolved",
        ],
        "parameter_set_id": parameters.get("parameter_set_id"),
        "parameter_set_sha256": parameter_sha256(parameters),
        "deployment_stage": parameters.get("approval_status"),
        "source_record_sha256": sorted(str(s["sha256"]) for s in sessions),
    }
    forecast["forecast_sha256"] = forecast_sha256(forecast)
    research = None
    if not issued and computed is not None and set(hard) == {NOT_APPROVED}:
        research = {
            "basis": "research",
            "forecast_id": forecast["forecast_id"],
            "forecast_sha256": forecast["forecast_sha256"],
            "parameter_set_id": parameters.get("parameter_set_id"),
            "parameter_set_sha256": forecast["parameter_set_sha256"],
            "scores": computed,
        }
        research["research_sha256"] = canonical_sha256(research)
    return forecast, research
