"""Backtest strategies and runs through the API, on synthetic daily bars."""

from __future__ import annotations

import math
import threading
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from packages.contracts.validator import validate_document
from services.api.main import create_app
from services.backtest.jobs import BacktestRunner
from services.backtest.store import BacktestStore
from services.journal.store import JournalStore
from services.market_data.daily import day_bounds, sessions_for_span
from services.market_data.instruments import calendar_for
from services.market_data.provider import BarsQuery, BarsResult

NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)


def utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def synthetic_bars(query: BarsQuery) -> BarsResult:
    """One daily bar per session on a sine wave; deterministic for any instrument."""
    calendar = calendar_for(query.instrument_id)
    span = "all_day" if query.instrument_id.startswith("kr:") else "regular"
    windows = sessions_for_span(calendar, query.start_utc, query.end_utc, span)
    bars = []
    for i, window in enumerate(windows):
        start, end = day_bounds(window, span)
        close = 100 + 10 * math.sin(i / 4 + len(query.instrument_id))
        open_ = close - math.cos(i / 3)
        bars.append(
            {
                "interval_start_utc": utc(start),
                "interval_end_utc": utc(end),
                "open": f"{open_:.4f}",
                "high": f"{max(open_, close) + 1:.4f}",
                "low": f"{min(open_, close) - 1:.4f}",
                "close": f"{close:.4f}",
                "volume": "1000",
                "is_final": True,
            }
        )
    coverage = {
        "missing_intervals": [],
        "adjustment": "split_adjusted",
        "source_id": "synthetic",
        "source_feed_scope": "synthetic-daily",
        "dataset_revision": "synthetic-r1",
        "expected_interval_count": len(windows),
    }
    return BarsResult(tuple(bars), coverage)


RULES = {
    "type": "rules",
    "entry": {
        "op": "cross_above",
        "left": {"price": "close"},
        "right": {"indicator": "sma", "params": {"length": 5}},
    },
    "exit": {
        "op": "cross_below",
        "left": {"price": "close"},
        "right": {"indicator": "sma", "params": {"length": 5}},
    },
    "sizing": {"mode": "percent_equity", "value": 40},
    "max_positions": 2,
}
CONFIG = {
    "instrument_ids": ["us:aapl:xnas:2026", "us:msft:xnas:2026"],
    "timeframe": "1d",
    "start_date": "2026-01-02",
    "end_date": "2026-06-30",
    "initial_capital": "10000",
    "currency": "USD",
    "fee_rate": "0.0005",
    "slippage_bps": "5",
}


def adjusted_bars(query: BarsQuery, adjustment: str) -> BarsResult:
    """Synthetic bars labelled as adjusted, with a dated revision like Alpaca's."""
    raw = synthetic_bars(query)
    return BarsResult(
        tuple({**bar, "adjustment": adjustment} for bar in raw.bars),
        {
            **raw.coverage,
            "adjustment": adjustment,
            "dataset_revision": "synthetic-r1:1d:split:asof-2026-10-21",
        },
    )


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[tuple[TestClient, BacktestRunner, JournalStore]]:
    gate: list[Callable[[BarsQuery], BarsResult]] = [synthetic_bars]
    runner = BacktestRunner(
        BacktestStore(tmp_path / "backtests.sqlite3", lambda: NOW),
        lambda: gate[0],
        get_adjusted=lambda: adjusted_bars,
    )
    journal = JournalStore(tmp_path / "journal")
    app = create_app(journal_store=journal, backtest_runner=runner, clock=lambda: NOW)
    with TestClient(app) as test_client:
        yield test_client, runner, journal
    runner.stop()


def save(client: TestClient, definition: dict[str, Any], **extra: Any) -> dict[str, Any]:
    response = client.post(
        "/v1/backtests/strategies", json={"name": "SMA cross", "definition": definition, **extra}
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_strategies_are_versioned_and_immutable(setup: Any) -> None:
    client, _runner, _journal = setup
    first = save(client, RULES)
    assert first["version"] == 1 and first["versions"] == [1]
    same = save(client, RULES, strategy_id=first["strategy_id"])
    assert same["version"] == 1
    changed = save(
        client,
        {**RULES, "max_positions": 1},
        strategy_id=first["strategy_id"],
    )
    assert changed["version"] == 2 and changed["versions"] == [1, 2]
    assert changed["definition_sha256"] != first["definition_sha256"]
    old = client.get(f"/v1/backtests/strategies/{first['strategy_id']}", params={"version": 1})
    assert old.json()["definition"]["max_positions"] == 2
    listed = client.get("/v1/backtests/strategies").json()
    validate_document(listed)
    assert [s["version"] for s in listed["strategies"]] == [2]
    bad = client.post(
        "/v1/backtests/strategies",
        json={"name": "x", "definition": {"type": "python", "code": "import os\nos.("}},
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "invalid_strategy"


def test_a_run_produces_validated_results_and_never_touches_the_journal(setup: Any) -> None:
    client, runner, journal = setup
    strategy = save(client, RULES)
    submitted = client.post(
        "/v1/backtests/runs", json={"strategy_id": strategy["strategy_id"], "config": CONFIG}
    )
    assert submitted.status_code == 202, submitted.text
    run_id = submitted.json()["run_id"]
    assert runner.wait_idle()
    run = client.get(f"/v1/backtests/runs/{run_id}").json()
    assert run["status"] == "succeeded", run["error"]
    assert run["strategy_sha256"] == strategy["definition_sha256"]
    results = client.get(f"/v1/backtests/runs/{run_id}/results").json()
    validate_document(results)
    assert results["metrics"]["trade_count"] > 0
    assert {pin["instrument_id"] for pin in results["inputs"]} == set(CONFIG["instrument_ids"])
    assert all(len(pin["bars_sha256"]) == 64 for pin in results["inputs"])
    assert any("survivorship" in warning for warning in results["warnings"])
    assert [d["code"] for d in results["warning_details"]] == ["survivorship"]
    assert run["summary"]["final_equity"] == results["final_equity"]
    # Same inputs, same strategy: byte-identical results apart from the run ID.
    again = client.post(
        "/v1/backtests/runs", json={"strategy_id": strategy["strategy_id"], "config": CONFIG}
    ).json()["run_id"]
    assert runner.wait_idle()
    repeat = client.get(f"/v1/backtests/runs/{again}/results").json()
    assert repeat["metrics"] == results["metrics"]
    assert repeat["inputs"] == results["inputs"]
    assert len(repeat["fills"]) == len(results["fills"])
    # Backtest fills live only in backtests.sqlite3.
    assert journal.manual_executions() == []
    assert journal.broker_executions() == []
    assert journal.imported_executions() == []
    assert journal.scenarios() == []
    listed = client.get("/v1/backtests/runs").json()
    validate_document(listed)
    assert [r["run_id"] for r in listed["runs"]] == [again, run_id]


def test_korean_runs_use_krw_and_the_korean_calendar(setup: Any) -> None:
    client, runner, _journal = setup
    strategy = save(client, RULES)
    config = {
        **CONFIG,
        "instrument_ids": ["kr:005930:xkrx:2026"],
        "currency": "KRW",
        "initial_capital": "10000000",
    }
    run_id = client.post(
        "/v1/backtests/runs", json={"strategy_id": strategy["strategy_id"], "config": config}
    ).json()["run_id"]
    assert runner.wait_idle()
    results = client.get(f"/v1/backtests/runs/{run_id}/results").json()
    assert results["currency"] == "KRW"
    assert all(fill["currency"] == "KRW" for fill in results["fills"])
    assert results["inputs"][0]["calendar_id"] == "XKRX"


def test_invalid_configs_are_refused(setup: Any) -> None:
    client, _runner, _journal = setup
    strategy = save(client, RULES)
    for change, message in [
        ({"currency": "KRW"}, "trades in USD"),
        ({"instrument_ids": []}, "instrument_ids"),
        ({"end_date": "2025-01-01"}, "end_date"),
        ({"timeframe": "1y"}, "timeframe"),
        ({"slippage_bps": "-1"}, "slippage_bps"),
    ]:
        response = client.post(
            "/v1/backtests/runs",
            json={"strategy_id": strategy["strategy_id"], "config": {**CONFIG, **change}},
        )
        assert response.status_code == 422
        assert message in response.json()["error"]["message"]
    missing = client.post("/v1/backtests/runs", json={"strategy_id": "nope", "config": CONFIG})
    assert missing.status_code == 404
    assert client.get("/v1/backtests/runs/nope/results").status_code == 404


def test_cancel_queued_and_running_runs(tmp_path: Path) -> None:
    release = threading.Event()
    started = threading.Event()

    def blocking(query: BarsQuery) -> BarsResult:
        started.set()
        release.wait(10)
        return synthetic_bars(query)

    runner = BacktestRunner(
        BacktestStore(tmp_path / "backtests.sqlite3", lambda: NOW), lambda: blocking
    )
    try:
        strategy = runner.store.save_strategy("s", RULES)
        first = runner.submit(strategy["strategy_id"], None, CONFIG)
        second = runner.submit(strategy["strategy_id"], None, CONFIG)
        assert started.wait(5)
        assert runner.cancel(second["run_id"])["status"] == "cancelled"
        runner.cancel(first["run_id"])
        release.set()
        assert runner.wait_idle()
        assert runner.store.run(first["run_id"])["status"] == "cancelled"
        assert runner.store.results(first["run_id"]) is None
    finally:
        release.set()
        runner.stop()


def test_failing_python_strategies_fail_the_run_with_the_reason(setup: Any) -> None:
    client, runner, _journal = setup
    strategy = save(
        client,
        {"type": "python", "code": "def on_bar(ctx):\n    import socket\n"},
    )
    run_id = client.post(
        "/v1/backtests/runs", json={"strategy_id": strategy["strategy_id"], "config": CONFIG}
    ).json()["run_id"]
    assert runner.wait_idle()
    run = client.get(f"/v1/backtests/runs/{run_id}").json()
    assert run["status"] == "failed"
    assert "may not import socket" in run["error"]
    unavailable = client.get(f"/v1/backtests/runs/{run_id}/results")
    assert unavailable.status_code == 409


def test_without_a_data_directory_nothing_is_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SIW_DATA_DIR", raising=False)
    with TestClient(create_app()) as client:
        response = client.get("/v1/backtests/runs")
    assert response.status_code == 503


def test_adjusted_prices_are_requested_pinned_and_flagged(setup: Any) -> None:
    client, runner, _journal = setup
    strategy = save(client, RULES)
    for timeframe in ("1d",):  # weekly adjusted aggregation: tests/market_data/test_daily.py
        run_id = client.post(
            "/v1/backtests/runs",
            json={
                "strategy_id": strategy["strategy_id"],
                "config": {**CONFIG, "timeframe": timeframe, "adjustment": "split_adjusted"},
            },
        ).json()["run_id"]
        assert runner.wait_idle()
        run = client.get(f"/v1/backtests/runs/{run_id}").json()
        assert run["status"] == "succeeded", run["error"]
        assert run["config"]["adjustment"] == "split_adjusted"
        results = client.get(f"/v1/backtests/runs/{run_id}/results").json()
        validate_document(results)
        assert {pin["adjustment_requested"] for pin in results["inputs"]} == {"split_adjusted"}
        assert {tuple(pin["adjustments"]) for pin in results["inputs"]} == {("split_adjusted",)}
        adjusted = [d for d in results["warning_details"] if d["code"] == "adjusted_prices"]
        assert [d["dates"] for d in adjusted] == [["2026-10-21"], ["2026-10-21"]]  # 2 symbols
        assert not any(d["code"] == "raw_prices" for d in results["warning_details"])
    intraday = client.post(
        "/v1/backtests/runs",
        json={
            "strategy_id": strategy["strategy_id"],
            "config": {**CONFIG, "timeframe": "5m", "adjustment": "fully_adjusted"},
        },
    )
    assert intraday.status_code == 422
    assert "daily and weekly" in intraday.json()["error"]["message"]
