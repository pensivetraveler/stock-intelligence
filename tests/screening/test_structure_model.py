"""The structure model (ADR 0053): darkpool-stress/2.0.0 gates and features on Alpaca SIP
session levels, in Decimal."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

from services.screening.structure.model import (
    NOT_APPROVED,
    build_forecast,
    forecast_sha256,
    stress_band,
    tick_for,
)
from services.screening.structure.service import research_baseline
from tests.screening.structure_fixtures import (
    CUTOFF,
    NOW,
    PASS,
    approved,
    daily_bars,
    profile_sessions,
    reference,
    windows,
)


def run(**changes: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    values: dict[str, Any] = {
        "symbol": "STAK",
        "instrument_id": "us:stak:xnas:2026",
        "decision": CUTOFF,
        "created_at": NOW,
        "required_dates": [w.session_date for w in windows()],
        "sessions": profile_sessions(),
        "bars": daily_bars(),
        "reference": reference(),
        "corporate_actions": PASS,
        "parameters": research_baseline(),
        "forecast_id": "00000000-0000-4000-8000-000000000001",
    }
    values.update(changes)
    return build_forecast(**values)


def test_research_parameters_never_issue_but_keep_research_scores() -> None:
    forecast, research = run()

    assert forecast["forecast_status"] == "NOT_ISSUED"
    assert forecast["eligibility"]["hard_failures"] == [NOT_APPROVED]
    assert forecast["scores"]["stress_level"] == "UNKNOWN"
    assert forecast["scores"]["path_instability"] is None
    assert forecast["expected_responses"] == []
    # The structure itself is still computed.
    zones = forecast["active_zones"]
    assert zones and all(Decimal(z["low"]) < Decimal(z["high"]) for z in zones)
    assert any(Decimal(z["low"]) <= Decimal("1.80") < Decimal(z["high"]) for z in zones)
    assert any(Decimal(z["low"]) <= Decimal("2.40") < Decimal(z["high"]) for z in zones)
    assert forecast["gaps"]["up"]["gap_is_open_ended"] is False
    assert forecast["gaps"]["down"]["gap_is_open_ended"] is False
    assert forecast["coverage"]["available_complete_sessions"] == 20
    # Research scores are kept apart, linked by hash.
    assert research is not None
    assert research["forecast_sha256"] == forecast["forecast_sha256"]
    assert research["scores"]["path_instability"] is not None
    assert research["scores"]["stress_level"] == "UNKNOWN"  # no cut points yet


def test_forecast_is_deterministic_and_hashed() -> None:
    first, _ = run()
    second, _ = run()
    assert first["forecast_sha256"] == second["forecast_sha256"] == forecast_sha256(first)
    # Every number is a string: no float in the document.
    assert all(not isinstance(v, float) for v in _leaves(first))


def _leaves(value: Any) -> list[Any]:
    if isinstance(value, dict):
        return [leaf for item in value.values() for leaf in _leaves(item)]
    if isinstance(value, list):
        return [leaf for item in value for leaf in _leaves(item)]
    return [value]


def test_missing_sessions_are_unknown_not_low() -> None:
    forecast, research = run(sessions=profile_sessions()[-9:])
    assert "H07_INSUFFICIENT_COMPLETE_SESSION_COVERAGE" in forecast["eligibility"]["hard_failures"]
    assert forecast["scores"]["stress_level"] == "UNKNOWN"
    assert research is None
    assert len(forecast["coverage"]["missing_sessions"]) == 11


def test_split_in_window_and_unresolved_actions_block() -> None:
    inside, research = run(
        corporate_actions={"status": "IN_WINDOW", "splits": [{}], "reason": None}
    )
    assert "H04_CORPORATE_ACTION_IN_WINDOW" in inside["eligibility"]["hard_failures"]
    assert research is None
    unknown, _ = run(corporate_actions={"status": "UNRESOLVED", "splits": [], "reason": "x"})
    assert "H04_CORPORATE_ACTION_UNRESOLVED" in unknown["eligibility"]["hard_failures"]


def test_reference_price_gates() -> None:
    missing, _ = run(reference={"value": None, "timestamp_utc": None, "session": None})
    assert "H01_REFERENCE_PRICE_MISSING" in missing["eligibility"]["hard_failures"]
    later = reference()
    later["timestamp_utc"] = "2026-11-06T15:00:01Z"
    leaked, research = run(reference=later)
    assert "H03_POST_DECISION_FEATURE_ROW" in leaked["eligibility"]["hard_failures"]
    assert research is None


def test_session_after_decision_and_tampered_session_block() -> None:
    sessions = profile_sessions()
    future = dict(sessions[-1])
    future["range_utc"] = [future["range_utc"][0], "2026-11-07T01:00:00Z"]
    forecast, _ = run(sessions=[*sessions[:-1], future])
    hard = forecast["eligibility"]["hard_failures"]
    assert "H03_POST_DECISION_FEATURE_ROW" in hard
    assert "H09_INPUT_HASH_MISMATCH" in hard


def test_created_before_decision_blocks() -> None:
    forecast, _ = run(created_at=CUTOFF - timedelta(seconds=1))
    assert "H12_FORECAST_CREATED_BEFORE_DECISION" in forecast["eligibility"]["hard_failures"]


def test_approved_parameters_issue_a_band() -> None:
    forecast, research = run(parameters=approved())
    assert forecast["forecast_status"] == "ISSUED"
    assert forecast["eligibility"]["status"] == "PASS"
    assert forecast["scores"]["stress_level"] in ("LOW", "MODERATE", "HIGH", "EXTREME")
    assert forecast["expected_responses"]
    assert forecast["deployment_stage"] == "SHADOW_APPROVED"
    assert research is None


def test_reconstructed_sessions_are_flagged() -> None:
    sessions = [dict(s) for s in profile_sessions()]
    from services.screening.structure.sessions import canonical_sha256

    for item in sessions:
        item["read_at_utc"] = "2026-11-08T00:00:00Z"
        item.pop("sha256")
        item["sha256"] = canonical_sha256(item)
    forecast, research = run(sessions=sessions)
    assert "S09_RECONSTRUCTED_AFTER_CUTOFF" in forecast["eligibility"]["soft_failures"]
    assert research is not None  # soft only


def test_bands_and_ticks() -> None:
    cuts = {"p50": "20", "p75": "40", "p90": "60"}
    assert stress_band(None, cuts) == "UNKNOWN"
    assert stress_band(Decimal("10"), None) == "UNKNOWN"
    assert stress_band(Decimal("10"), cuts) == "LOW"
    assert stress_band(Decimal("45"), cuts) == "HIGH"
    assert stress_band(Decimal("60"), cuts) == "EXTREME"
    assert tick_for(Decimal("0.42"), Decimal("0.01")) == Decimal("0.0001")
    assert tick_for(Decimal("4.20"), Decimal("0.01")) == Decimal("0.01")


def test_sub_dollar_bins_use_sub_penny_ticks() -> None:
    sessions = []
    from tests.screening.structure_fixtures import session

    for window in windows():
        sessions.append(
            session(
                window.session_date,
                [("0.4012", "900", "10"), ("0.4300", "50", "5"), ("0.4611", "800", "20")],
                last_price="0.43",
            )
        )
    forecast, _ = run(sessions=sessions, reference=reference("0.43"), bars=[])
    assert forecast["binning"]["tick"] == "0.0001"
    assert forecast["binning"]["fine_width"] == "0.0011"
    assert "S08_ATR20_UNAVAILABLE_PRICE_ONLY_BIN" in forecast["eligibility"]["soft_failures"]
