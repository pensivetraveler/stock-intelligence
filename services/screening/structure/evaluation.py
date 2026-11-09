"""Post-hoc validation, walk-forward evaluation and shadow audit of the structure model
(ADR 0053), from darkpool-analysis `validate_stress_forecast.py`,
`export_stress_evaluation_records.py`, `evaluate_stress_model.py` and
`audit_stress_shadow.py` (2026-11-09), in Decimal.

These only write reports. None of them approves a parameter set (`automatic_approval` is
always false); a person registers an approved document that points at their hashes.

Change from the original (ADR 0053): validation and evaluation records also take research
scores (scores kept apart when every gate passed except approval). Without them the
original could never score anything to evaluate, and so never approve anything.
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext
from typing import Any

from services.market_data.calendar import NEW_YORK
from services.screening.structure.model import forecast_sha256
from services.screening.structure.sessions import canonical_sha256, text

VALIDATION_VERSION = "darkpool-stress-siw-validation/2.0.0"
EVALUATION_VERSION = "darkpool-stress-siw-evaluation/2.0.0"
AUDIT_VERSION = "darkpool-stress-siw-shadow-audit/2.0.0"
BANDS = ("LOW", "MODERATE", "HIGH", "EXTREME")
ZERO = Decimal(0)
BPS = Decimal(10000)
PLACES = Decimal("1e-8")


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include an offset")
    return parsed


def _out(value: Decimal | None) -> str | None:
    return None if value is None else text(value.quantize(PLACES))


def validation_sha256(value: dict[str, Any]) -> str:
    return canonical_sha256({k: v for k, v in value.items() if k != "validation_sha256"})


def classify_zone(
    zone: dict[str, Any], reference: Decimal, bars: list[dict[str, Any]], policy: dict[str, Any]
) -> dict[str, Any]:
    buffer = reference * Decimal(str(policy["buffer_bps"])) / BPS
    rejection = reference * Decimal(str(policy["rejection_distance_bps"])) / BPS
    transit = reference * Decimal(str(policy["transit_distance_bps"])) / BPS
    low, high = Decimal(zone["low"]), Decimal(zone["high"])
    usable = sorted(bars, key=lambda bar: str(bar["date"]))
    touched = [b for b in usable if b["high"] >= low - buffer and b["low"] <= high + buffer]
    if not usable:
        outcome = "DATA_UNAVAILABLE"
    elif not touched:
        outcome = "NO_TOUCH"
    else:
        below, above = reference < low, reference > high
        if below:
            transit_hit = any(b["close"] >= high + transit for b in touched)
            rejection_hit = any(b["close"] <= low - rejection for b in touched)
        elif above:
            transit_hit = any(b["close"] <= low - transit for b in touched)
            rejection_hit = any(b["close"] >= high + rejection for b in touched)
        else:
            transit_hit = rejection_hit = False
        inside = sum(1 for b in touched if low - buffer <= b["close"] <= high + buffer)
        acceptance_hit = len(touched) >= int(policy["minimum_dwell_bars"]) and Decimal(
            inside
        ) / len(touched) >= Decimal(str(policy["minimum_inside_ratio"]))
        hits = [
            name
            for name, hit in (
                ("REJECTION", rejection_hit),
                ("ACCEPTANCE", acceptance_hit),
                ("TRANSIT", transit_hit),
            )
            if hit
        ]
        # Daily bars do not order intraday events: several outcomes at once is AMBIGUOUS.
        outcome = hits[0] if len(hits) == 1 else "AMBIGUOUS"
    return {
        "zone_id": zone["zone_id"],
        "original_low": zone["low"],
        "original_high": zone["high"],
        "outcome": outcome,
        "touched_bars": len(touched),
        "evaluated_bars": len(usable),
    }


def horizon_bars(
    forecast: dict[str, Any], bars: list[dict[str, Any]], horizon: int
) -> list[dict[str, Any]]:
    """Closed daily bars of the `horizon` sessions after the decision's New York date."""
    decision_day = _time(forecast["decision_timestamp_utc"]).astimezone(NEW_YORK).date()
    later = sorted(
        (b for b in bars if date.fromisoformat(str(b["date"])) > decision_day),
        key=lambda b: str(b["date"]),
    )
    return later[:horizon]


def build_validation(
    forecast: dict[str, Any],
    research: dict[str, Any] | None,
    bars: list[dict[str, Any]],
    policy: dict[str, Any],
    *,
    label_available_at_utc: str,
    bars_sha256: str | None,
    validation_id: str | None = None,
) -> dict[str, Any]:
    """Judges the forecast's own zones (never redrawn) on the bars after the decision.
    `bars` must be closed regular-session daily bars known at `label_available_at_utc`."""
    if forecast.get("forecast_sha256") != forecast_sha256(forecast):
        raise ValueError("forecast hash mismatch")
    if _time(label_available_at_utc) <= _time(forecast["decision_timestamp_utc"]):
        raise ValueError("label availability must be after forecast decision")
    if research is not None and research.get("forecast_sha256") != forecast["forecast_sha256"]:
        raise ValueError("research/forecast linkage mismatch")
    scores = forecast["scores"] if forecast.get("forecast_status") == "ISSUED" else None
    basis = "issued" if scores is not None else "research" if research is not None else None
    horizon = int(forecast["scores"]["horizon_sessions"])
    window = horizon_bars(forecast, bars, horizon)
    reference = (
        Decimal(forecast["reference_price"]["value"])
        if forecast["reference_price"]["value"]
        else None
    )
    with localcontext() as context:
        context.prec = 34
        reason: str | None
        if basis is None or reference is None:
            outcomes: list[dict[str, Any]] = []
            status, reason = "UNVERIFIABLE", "FORECAST_NOT_ISSUED"
        elif len(window) < horizon:
            outcomes = []
            status, reason = "UNVERIFIABLE", "PRICE_DATA_UNAVAILABLE"
        else:
            outcomes = [
                classify_zone(z, reference, window, policy) for z in forecast["active_zones"]
            ]
            ok = outcomes and all(row["outcome"] != "DATA_UNAVAILABLE" for row in outcomes)
            status = "VALIDATED" if ok else "UNVERIFIABLE"
            reason = None if ok else "PRICE_DATA_UNAVAILABLE"
        up = down = None
        if reference is not None and window:
            up = BPS * (max(b["high"] for b in window) - reference) / reference
            down = BPS * (reference - min(b["low"] for b in window)) / reference
    result: dict[str, Any] = {
        "schema_version": "2.0-siw.1",
        "validation_version": VALIDATION_VERSION,
        "validation_id": validation_id or str(uuid.uuid4()),
        "forecast_id": forecast["forecast_id"],
        "forecast_sha256": forecast["forecast_sha256"],
        "score_basis": basis,
        "symbol": forecast["symbol"],
        "status": status,
        "unverifiable_reason": reason,
        "label_available_at_utc": label_available_at_utc,
        "bars_sha256": bars_sha256,
        "evaluated_session_dates": [str(b["date"]) for b in window],
        "realized_path": {
            "up_excursion_bps": _out(up),
            "down_excursion_bps": _out(down),
            "max_excursion_bps": _out(max(up, down))
            if up is not None and down is not None
            else None,
        },
        "policy": policy,
        "zone_outcomes": outcomes,
    }
    result["validation_sha256"] = validation_sha256(result)
    return result


def _bucket(reference: Decimal | None) -> str:
    if reference is None:
        return "UNKNOWN"
    if reference < 1:
        return "LT_1"
    if reference < 5:
        return "1_TO_5"
    return "5_TO_20" if reference < 20 else "GE_20"


def evaluation_record(
    forecast: dict[str, Any], validation: dict[str, Any], research: dict[str, Any] | None
) -> dict[str, Any]:
    if forecast.get("forecast_sha256") != forecast_sha256(forecast):
        raise ValueError("forecast hash mismatch")
    if validation.get("validation_sha256") != validation_sha256(validation):
        raise ValueError("validation hash mismatch")
    if validation.get("forecast_sha256") != forecast["forecast_sha256"]:
        raise ValueError("forecast/validation linkage mismatch")
    issued = forecast.get("forecast_status") == "ISSUED"
    scores = forecast["scores"] if issued else (research or {}).get("scores") or {}
    value = forecast["reference_price"]["value"]
    return {
        "forecast_id": forecast["forecast_id"],
        "validation_id": validation["validation_id"],
        "symbol": forecast["symbol"],
        "decision_timestamp_utc": forecast["decision_timestamp_utc"],
        "label_available_at_utc": validation["label_available_at_utc"],
        "score_basis": "issued" if issued else "research" if research else None,
        "eligible": (issued or research is not None) and validation["status"] == "VALIDATED",
        "path_instability": scores.get("path_instability"),
        "stress_level": scores.get("stress_level"),
        "realized_max_excursion_bps": validation["realized_path"]["max_excursion_bps"],
        "reference_price": value,
        "price_bucket": _bucket(None if value is None else Decimal(value)),
        "adv_bucket": "UNKNOWN",
        "volatility_regime": "UNKNOWN",
        "coverage_regime": "COMPLETE"
        if forecast["coverage"]["coverage_ratio"] == "1"
        else "PARTIAL_OR_UNKNOWN",
        "corporate_action_proximity": "UNKNOWN",
    }


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


def _ranks(values: list[Decimal]) -> list[Decimal]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    result = [ZERO] * len(values)
    index = 0
    while index < len(indexed):
        end = index + 1
        while end < len(indexed) and indexed[end][1] == indexed[index][1]:
            end += 1
        rank = Decimal(index + end - 1) / 2 + 1
        for position in range(index, end):
            result[indexed[position][0]] = rank
        index = end
    return result


def _spearman(x: list[Decimal], y: list[Decimal]) -> Decimal | None:
    if len(x) < 3 or len(x) != len(y):
        return None
    rx, ry = _ranks(x), _ranks(y)
    mx, my = sum(rx, ZERO) / len(rx), sum(ry, ZERO) / len(ry)
    numerator = sum(((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True)), ZERO)
    denominator = (
        sum(((a - mx) ** 2 for a in rx), ZERO) * sum(((b - my) ** 2 for b in ry), ZERO)
    ).sqrt()
    return numerator / denominator if denominator else None


def _band(score: Decimal, cuts: dict[str, Decimal]) -> str:
    if score < cuts["p50"]:
        return "LOW"
    if score < cuts["p75"]:
        return "MODERATE"
    return "HIGH" if score < cuts["p90"] else "EXTREME"


def _split(records: list[dict[str, Any]], plan: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    bounds = {
        name: (_time(plan[f"{name}_start_utc"]), _time(plan[f"{name}_end_utc"]))
        for name in ("train", "validation", "test")
    }
    result: dict[str, list[dict[str, Any]]] = {name: [] for name in bounds}
    for record in records:
        decision = _time(record["decision_timestamp_utc"])
        label_at = _time(record["label_available_at_utc"])
        for name, (start, end) in bounds.items():
            # A label known only after the split ended would leak into it.
            if start <= decision <= end and label_at <= end:
                result[name].append(record)
                break
    return result


def _metrics(
    records: list[dict[str, Any]], cuts: dict[str, Decimal], threshold: Decimal, fields: list[str]
) -> dict[str, Any]:
    eligible = [
        r
        for r in records
        if r.get("eligible") is True
        and r.get("path_instability") is not None
        and r.get("realized_max_excursion_bps") is not None
    ]
    scores = [Decimal(r["path_instability"]) for r in eligible]
    outcomes = [Decimal(r["realized_max_excursion_bps"]) for r in eligible]
    by_band: dict[str, list[Decimal]] = {band: [] for band in BANDS}
    for score, outcome in zip(scores, outcomes, strict=True):
        by_band[_band(score, cuts)].append(outcome)

    def rate(values: list[Decimal]) -> Decimal | None:
        return Decimal(sum(1 for v in values if v >= threshold)) / len(values) if values else None

    band_stats = {
        band: {
            "count": len(values),
            "event_rate": _out(rate(values)),
            "median_excursion_bps": _out(_percentile(values, Decimal("0.5"))),
        }
        for band, values in by_band.items()
    }
    populated = [r for r in (rate(by_band[b]) for b in BANDS) if r is not None]
    monotonic = len(populated) >= 3 and all(
        a <= b for a, b in zip(populated, populated[1:], strict=False)
    )
    event_rate = rate(outcomes)
    decile = _percentile(scores, Decimal("0.9"))
    top = [o for s, o in zip(scores, outcomes, strict=True) if decile is not None and s >= decile]
    top_rate = rate(top)
    lift = top_rate / event_rate if top_rate is not None and event_rate else None
    subgroups: dict[str, Any] = {}
    for name in fields:
        grouped: dict[str, list[Decimal]] = defaultdict(list)
        for record, outcome in zip(eligible, outcomes, strict=True):
            grouped[str(record.get(name, "UNKNOWN"))].append(outcome)
        subgroups[name] = {
            key: {"count": len(values), "event_rate": _out(rate(values))}
            for key, values in sorted(grouped.items())
        }
    return {
        "total_records": len(records),
        "eligible_records": len(eligible),
        "unknown_rate": _out(1 - Decimal(len(eligible)) / len(records)) if records else None,
        "spearman": _out(_spearman(scores, outcomes)),
        "top_decile_lift": _out(lift),
        "band_stats": band_stats,
        "monotonic_band_event_rate": monotonic,
        "subgroups": subgroups,
    }


def report_sha256(report: dict[str, Any]) -> str:
    return canonical_sha256({k: v for k, v in report.items() if k != "report_sha256"})


def evaluate(records: list[dict[str, Any]], plan: dict[str, Any]) -> dict[str, Any]:
    with localcontext() as context:
        context.prec = 34
        splits = _split(records, plan)
        train = [
            Decimal(r["path_instability"])
            for r in splits["train"]
            if r.get("eligible") is True and r.get("path_instability") is not None
        ]
        found = {
            key: _percentile(train, Decimal(q))
            for key, q in (("p50", "0.50"), ("p75", "0.75"), ("p90", "0.90"))
        }
        if any(value is None for value in found.values()):
            raise ValueError("training split has insufficient eligible scores")
        cuts = {key: value for key, value in found.items() if value is not None}
        threshold = Decimal(str(plan["event_threshold_bps"]))
        fields = list(plan.get("subgroup_fields", []))
        metrics = {name: _metrics(rows, cuts, threshold, fields) for name, rows in splits.items()}
    test, criteria = metrics["test"], plan["approval_criteria"]

    def at_least(value: str | None, bound: Any) -> bool:
        return value is not None and Decimal(value) >= Decimal(str(bound))

    checks = {
        "minimum_test_records": test["eligible_records"] >= int(criteria["minimum_test_records"]),
        "maximum_unknown_rate": test["unknown_rate"] is not None
        and Decimal(test["unknown_rate"]) <= Decimal(str(criteria["maximum_unknown_rate"])),
        "minimum_spearman": at_least(test["spearman"], criteria["minimum_spearman"]),
        "minimum_top_decile_lift": at_least(
            test["top_decile_lift"], criteria["minimum_top_decile_lift"]
        ),
        "monotonic_band_event_rate": test["monotonic_band_event_rate"],
    }
    bases = Counter(str(r.get("score_basis")) for r in records)
    report: dict[str, Any] = {
        "schema_version": "2.0-siw.1",
        "evaluation_version": EVALUATION_VERSION,
        "plan_id": plan["plan_id"],
        "plan": plan,
        "record_count": len(records),
        "score_bases": dict(sorted(bases.items())),
        "split_counts": {key: len(value) for key, value in splits.items()},
        "candidate_band_cut_points": {key: _out(value) for key, value in cuts.items()},
        "metrics": metrics,
        "approval_checks": checks,
        "eligible_for_shadow_review": all(checks.values()),
        "automatic_approval": False,
    }
    report["report_sha256"] = report_sha256(report)
    return report


def audit_shadow(
    forecasts: list[dict[str, Any]], minimum_sessions: int = 20, maximum_unknown_rate: str = "0.25"
) -> dict[str, Any]:
    valid = [f for f in forecasts if f.get("forecast_sha256") == forecast_sha256(f)]
    sessions = sorted(
        {_time(f["decision_timestamp_utc"]).astimezone(NEW_YORK).date().isoformat() for f in valid}
    )
    issued = [f for f in valid if f.get("forecast_status") == "ISSUED"]
    unknown = [f for f in valid if f.get("eligibility", {}).get("status") != "PASS"]
    causes = Counter(c for f in unknown for c in f.get("eligibility", {}).get("hard_failures", []))
    unknown_rate = Decimal(len(unknown)) / len(valid) if valid else None
    checks = {
        "all_hashes_valid": len(valid) == len(forecasts),
        "minimum_distinct_sessions": len(sessions) >= minimum_sessions,
        "maximum_unknown_rate": unknown_rate is not None
        and unknown_rate <= Decimal(maximum_unknown_rate),
        "issued_forecasts_present": bool(issued),
    }
    report: dict[str, Any] = {
        "schema_version": "2.0-siw.1",
        "audit_version": AUDIT_VERSION,
        "forecast_count": len(forecasts),
        "valid_hash_count": len(valid),
        "distinct_sessions": len(sessions),
        "first_session": sessions[0] if sessions else None,
        "last_session": sessions[-1] if sessions else None,
        "issued_count": len(issued),
        "unknown_count": len(unknown),
        "unknown_rate": _out(unknown_rate),
        "unknown_causes": dict(sorted(causes.items())),
        "checks": checks,
        "eligible_for_production_review": all(checks.values()),
        "automatic_approval": False,
    }
    report["report_sha256"] = report_sha256(report)
    return report
