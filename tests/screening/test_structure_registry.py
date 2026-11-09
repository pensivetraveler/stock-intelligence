"""Session cache, registry, post-hoc validation, walk-forward evaluation and shadow audit
of the structure model (ADR 0053)."""

from __future__ import annotations

import copy
import sqlite3
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from services.screening.structure.evaluation import (
    audit_shadow,
    build_validation,
    evaluate,
    evaluation_record,
)
from services.screening.structure.registry import RegistryError
from services.screening.structure.service import (
    StructureStore,
    open_structure,
    research_baseline,
    validation_policy,
)
from services.screening.structure.sessions import (
    SessionReadError,
    canonical_sha256,
    read_session,
)
from tests.screening.structure_fixtures import (
    CALENDAR,
    NOW,
    FakeTrades,
    approved,
    trade,
    windows,
)
from tests.screening.test_structure_model import run


@pytest.fixture
def store(tmp_path: Path) -> StructureStore:
    return open_structure(tmp_path / "screening_structure.sqlite3", lambda: NOW)


def test_session_levels_split_off_exchange_and_lit() -> None:
    window = windows(1)[0]  # Thursday 2026-11-05
    trades = FakeTrades(
        {
            "2026-11-05": [
                trade("2026-11-05T15:00:00Z", "1.90", 400),
                trade("2026-11-05T15:00:01Z", "1.90", 100, x="Q"),
                trade("2026-11-05T15:00:02Z", "1.95", 9_000, u="canceled"),
                trade("2026-11-05T15:00:03Z", "1.50", 1_000, c=("@", "4")),
                trade("2026-11-05T20:00:00Z", "1.97", 5_000, x="Q", c=("@", "M")),
                trade("2026-11-05T20:30:00Z", "1.92", 50, x="K"),
            ]
        }
    )
    document = read_session(trades, "STAK", window, now=NOW, max_pages=10, max_seconds=60)

    assert document["levels"] == [["1.9", "400", "100"], ["1.92", "0", "50"]]
    assert document["total_volume"] == "1550"
    assert document["off_exchange_volume"] == "1400"
    assert document["off_market_price_volume"] == "1000"
    assert document["removed_trades"] == 1
    assert document["last_price"] == "1.92"
    body = {k: v for k, v in document.items() if k != "sha256"}
    assert document["sha256"] == canonical_sha256(body)


def test_open_or_cut_sessions_are_not_read() -> None:
    today = CALENDAR.window_for_date(NOW.date())
    with pytest.raises(SessionReadError, match="not closed"):
        read_session(FakeTrades({}), "STAK", today, now=NOW, max_pages=10, max_seconds=60)
    window = windows(1)[0]
    trades = FakeTrades({"2026-11-05": [trade("2026-11-05T15:00:00Z", "1.90", 400)]})
    with pytest.raises(SessionReadError, match="stopped"):
        read_session(trades, "STAK", window, now=NOW, max_pages=1, max_seconds=60)


def test_cache_checks_hashes(store: StructureStore, tmp_path: Path) -> None:
    window = windows(1)[0]
    trades = FakeTrades({"2026-11-05": [trade("2026-11-05T15:00:00Z", "1.90", 400)]})
    store.cache.put(read_session(trades, "STAK", window, now=NOW, max_pages=10, max_seconds=60))
    assert store.cache.get("STAK", window.session_date) is not None
    raw = sqlite3.connect(tmp_path / "screening_structure.sqlite3")
    raw.execute("UPDATE session_levels SET document = replace(document, '400', '401')")
    raw.commit()
    with pytest.raises(ValueError, match="hash mismatch"):
        store.cache.get("STAK", window.session_date)


def test_registry_rows_are_immutable(store: StructureStore, tmp_path: Path) -> None:
    forecast, research = run()
    store.registry.add_forecast(forecast, research)
    assert store.registry.forecast(forecast["forecast_id"]) == forecast
    assert store.registry.research(forecast["forecast_id"]) == research
    raw = sqlite3.connect(tmp_path / "screening_structure.sqlite3")
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        raw.execute("UPDATE forecasts SET forecast_status = 'ISSUED'")
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        raw.execute("DELETE FROM parameter_sets")


def test_forecast_needs_the_registered_parameter_set(store: StructureStore) -> None:
    edited = copy.deepcopy(research_baseline())
    edited["approval_status"] = "PRODUCTION_APPROVED"  # a JSON edit is not an approval
    forecast, _ = run(parameters=edited)
    with pytest.raises(RegistryError):
        store.registry.add_forecast(forecast, None)


def test_approval_needs_registered_evidence(store: StructureStore) -> None:
    with pytest.raises(RegistryError, match="calibration report"):
        store.registry.register_parameters(approved(), "2026-11-09T00:00:00Z")
    lacking = approved()
    lacking["training_window"] = None
    with pytest.raises(RegistryError, match="validation metadata"):
        store.registry.register_parameters(lacking, "2026-11-09T00:00:00Z")
    report = {"report_sha256": "b" * 64, "eligible_for_shadow_review": True}
    store.registry.add_evaluation("e1", None, report)
    store.registry.register_parameters(approved("b" * 64), "2026-11-09T00:00:00Z")
    assert store.parameters()["parameter_set_id"] == "test-approved/2.0.0"
    production = approved("b" * 64)
    production.update(
        {
            "parameter_set_id": "test-production/2.0.0",
            "approval_status": "PRODUCTION_APPROVED",
            "shadow_audit_sha256": "c" * 64,
        }
    )
    with pytest.raises(RegistryError, match="shadow audit"):
        store.registry.register_parameters(production, "2026-11-09T00:00:00Z")


def horizon(days: int = 5, close: str = "2.00", high: str = "2.05", low: str = "1.95") -> list:
    later = []
    day = NOW.date()
    while len(later) < days:
        day += timedelta(days=1)
        if CALENDAR.window_for_date(day) is not None:
            later.append(
                {
                    "date": day.isoformat(),
                    "high": Decimal(high),
                    "low": Decimal(low),
                    "close": Decimal(close),
                }
            )
    return later


def test_validation_keeps_original_zones_and_uses_later_bars_only() -> None:
    forecast, research = run()
    earlier = [{"date": "2026-11-05", "high": Decimal(9), "low": Decimal(0), "close": Decimal(5)}]
    bars = earlier + horizon(high="2.45", close="2.41")
    validation = build_validation(
        forecast,
        research,
        bars,
        validation_policy(),
        label_available_at_utc="2026-11-14T00:00:00Z",
        bars_sha256=None,
    )
    assert validation["status"] == "VALIDATED"
    assert validation["score_basis"] == "research"
    assert "2026-11-05" not in validation["evaluated_session_dates"]
    assert len(validation["evaluated_session_dates"]) == 5
    zones = {z["zone_id"]: z for z in forecast["active_zones"]}
    for outcome in validation["zone_outcomes"]:
        assert outcome["original_low"] == zones[outcome["zone_id"]]["low"]
    assert validation["realized_path"]["up_excursion_bps"] == "2250"

    with pytest.raises(ValueError, match="label availability"):
        build_validation(
            forecast,
            research,
            bars,
            validation_policy(),
            label_available_at_utc=forecast["decision_timestamp_utc"],
            bars_sha256=None,
        )


def test_unscored_forecasts_and_short_horizons_are_unverifiable() -> None:
    forecast, _ = run(sessions=[])
    validation = build_validation(
        forecast,
        None,
        horizon(),
        validation_policy(),
        label_available_at_utc="2026-11-14T00:00:00Z",
        bars_sha256=None,
    )
    assert validation["status"] == "UNVERIFIABLE"
    assert validation["unverifiable_reason"] == "FORECAST_NOT_ISSUED"
    scored, research = run()
    short = build_validation(
        scored,
        research,
        horizon(days=3),
        validation_policy(),
        label_available_at_utc="2026-11-14T00:00:00Z",
        bars_sha256=None,
    )
    assert short["unverifiable_reason"] == "PRICE_DATA_UNAVAILABLE"


def record(decision: str, label: str, score: str, excursion: str) -> dict[str, Any]:
    return {
        "decision_timestamp_utc": decision,
        "label_available_at_utc": label,
        "eligible": True,
        "score_basis": "research",
        "path_instability": score,
        "realized_max_excursion_bps": excursion,
        "price_bucket": "1_TO_5",
    }


def plan() -> dict[str, Any]:
    return {
        "plan_id": "test",
        "train_start_utc": "2025-01-01T00:00:00Z",
        "train_end_utc": "2025-06-30T23:59:59Z",
        "validation_start_utc": "2025-07-15T00:00:00Z",
        "validation_end_utc": "2025-09-30T23:59:59Z",
        "test_start_utc": "2025-10-15T00:00:00Z",
        "test_end_utc": "2025-12-31T23:59:59Z",
        "event_threshold_bps": "500",
        "subgroup_fields": ["price_bucket"],
        "approval_criteria": {
            "minimum_test_records": 3,
            "maximum_unknown_rate": "0.25",
            "minimum_spearman": "0.15",
            "minimum_top_decile_lift": "1.0",
        },
    }


def test_walk_forward_drops_labels_known_after_the_split() -> None:
    records = [
        record("2025-02-01T15:00:00Z", "2025-02-10T00:00:00Z", str(10 + i), str(100 * i))
        for i in range(10)
    ]
    # Decided in train, labelled after train ended: not usable for train.
    records.append(record("2025-06-30T15:00:00Z", "2025-07-08T00:00:00Z", "99", "999"))
    records += [
        record("2025-11-01T15:00:00Z", "2025-11-10T00:00:00Z", str(10 + 8 * i), str(200 * i))
        for i in range(6)
    ]
    report = evaluate(records, plan())
    assert report["split_counts"] == {"train": 10, "validation": 0, "test": 6}
    assert report["automatic_approval"] is False
    assert report["score_bases"] == {"research": 17}
    assert report["eligible_for_shadow_review"] is True
    assert set(report["candidate_band_cut_points"]) == {"p50", "p75", "p90"}

    with pytest.raises(ValueError, match="insufficient"):
        evaluate(records[-6:], plan())


def test_evaluation_record_links_research_scores() -> None:
    forecast, research = run()
    validation = build_validation(
        forecast,
        research,
        horizon(),
        validation_policy(),
        label_available_at_utc="2026-11-14T00:00:00Z",
        bars_sha256=None,
    )
    entry = evaluation_record(forecast, validation, research)
    assert entry["score_basis"] == "research"
    assert entry["eligible"] is True
    assert entry["path_instability"] == research["scores"]["path_instability"]


def test_shadow_audit_needs_twenty_sessions() -> None:
    forecast, _ = run(parameters=approved())
    report = audit_shadow([forecast])
    assert report["checks"]["minimum_distinct_sessions"] is False
    assert report["eligible_for_production_review"] is False
    assert report["automatic_approval"] is False
