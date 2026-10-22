"""Bar-count mode: the first N bars from a start date, in any past window."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from services.backtest.data import MAX_INTRADAY_SESSIONS, load_instrument
from services.market_data.provider import BarsQuery, BarsResult, InvalidBarsQueryError
from tests.backtest.test_backtest_api import (
    CONFIG,
    RULES,
    save,
    setup,  # noqa: F401 (a fixture)
    synthetic_bars,
    utc,
)

AAPL = "us:aapl:xnas:2026"


class FiveMinute:
    """78 five-minute bars per regular session; records each query."""

    def __init__(self) -> None:
        self.queries: list[BarsQuery] = []

    def __call__(self, query: BarsQuery) -> BarsResult:
        self.queries.append(query)
        bars = []
        start = query.start_utc
        while start < query.end_utc:
            end = start + timedelta(minutes=5)
            bars.append(
                {
                    "interval_start_utc": utc(start),
                    "interval_end_utc": utc(end),
                    "open": "10",
                    "high": "11",
                    "low": "9",
                    "close": "10.5",
                    "volume": "100",
                    "is_final": True,
                }
            )
            start = end
        coverage = {
            "missing_intervals": [],
            "adjustment": "raw",
            "source_id": "synthetic",
            "source_feed_scope": "synthetic-5m",
            "dataset_revision": "synthetic-r1",
            "expected_interval_count": len(bars),
        }
        return BarsResult(tuple(bars), coverage)


def test_intraday_reads_sessions_forward_only_until_it_has_the_bars() -> None:
    source = FiveMinute()
    loaded = load_instrument(
        source, AAPL, "5m", date(2024, 3, 4), date(2026, 10, 20), bar_count=200
    )
    assert len(loaded.bars) == 200
    assert len(source.queries) == 3  # 78 + 78 + 78 >= 200
    assert loaded.pin["bar_count"] == 200
    assert loaded.pin["bar_count_requested"] == 200
    assert loaded.pin["expected_interval_count"] == 200
    assert loaded.pin["requested_end_utc"] == utc(
        source.queries[2].start_utc + timedelta(minutes=5 * (200 - 156))
    )
    assert loaded.pin["requested_start_utc"].startswith("2024-03-04")


def test_bar_count_mode_is_not_held_to_the_session_limit() -> None:
    source = FiveMinute()
    with pytest.raises(InvalidBarsQueryError, match="sessions"):
        load_instrument(source, AAPL, "5m", date(2024, 1, 2), date(2024, 6, 28))
    loaded = load_instrument(
        source, AAPL, "5m", date(2024, 1, 2), date(2026, 10, 20), bar_count=5000
    )
    assert len(loaded.bars) == 5000
    assert len(source.queries) > MAX_INTRADAY_SESSIONS


def test_fewer_bars_than_asked_are_flagged() -> None:
    loaded = load_instrument(
        synthetic_bars, AAPL, "1d", date(2026, 10, 1), date(2026, 10, 20), bar_count=100
    )
    assert 0 < len(loaded.bars) < 100
    codes = [detail["code"] for detail in loaded.warning_details]
    assert "fewer_bars" in codes


def test_a_daily_run_from_a_past_start_date(setup: Any) -> None:  # noqa: F811
    client, runner, _journal = setup
    strategy = save(client, RULES)
    config = {**CONFIG, "start_date": "2015-01-02", "end_date": "2026-10-20", "bar_count": 120}
    submitted = client.post(
        "/v1/backtests/runs", json={"strategy_id": strategy["strategy_id"], "config": config}
    )
    assert submitted.status_code == 202, submitted.text
    assert submitted.json()["config"]["bar_count"] == 120
    run_id = submitted.json()["run_id"]
    assert runner.wait_idle()
    run = client.get(f"/v1/backtests/runs/{run_id}").json()
    assert run["status"] == "succeeded", run["error"]
    results = client.get(f"/v1/backtests/runs/{run_id}/results").json()
    for pin in results["inputs"]:
        assert pin["bar_count"] == 120
        assert pin["requested_start_utc"].startswith("2015-01-02")
        assert pin["requested_end_utc"] < "2015-07-01"
    for bad in (0, 5001, "100", True):
        refused = client.post(
            "/v1/backtests/runs",
            json={"strategy_id": strategy["strategy_id"], "config": {**config, "bar_count": bad}},
        )
        assert refused.status_code == 422
        assert "bar_count" in refused.json()["error"]["message"]
