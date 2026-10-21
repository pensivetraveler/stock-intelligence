"""Rules strategies with Python custom indicator operands (`py:<id>@<version>`, ADR 0023)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from packages.contracts.validator import validate_document
from services.api.main import create_app
from services.backtest.engine import Backtest, EngineConfig, RuleSignals
from services.backtest.jobs import BacktestRunner
from services.backtest.store import BacktestStore
from services.backtest.strategy import StrategyError, validate_definition
from services.custom_indicators import runner
from services.custom_indicators.store import CustomIndicatorStore
from tests.backtest.test_backtest_api import CONFIG, synthetic_bars
from tests.backtest.test_engine import bars, flat, instrument

NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)

# A simple moving average written by hand, and a "fast minus slow" difference.
MEAN = """
NAME = "Hand SMA"
INPUTS = {"length": 3}
OUTPUTS = ["mean", "gap"]
PANE = "price"

def update(ctx):
    length = int(ctx.params["length"])
    closes = ctx.closes
    if len(closes) < length:
        return None
    mean = sum(closes[-length:]) / length
    return {"mean": mean, "gap": ctx.bar.close - mean}
"""
BROKEN = """
NAME = "Breaks"
OUTPUTS = ["value"]

def update(ctx):
    if ctx.index == 4:
        raise RuntimeError("bad bar")
    return {"value": ctx.bar.close}
"""


def saved(store: CustomIndicatorStore, code: str) -> dict[str, Any]:
    return store.save(code, runner.inspect(code), None)


def crossing(operand: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "rules",
        "entry": {"op": "cross_above", "left": {"price": "close"}, "right": operand},
        "exit": {"op": "cross_below", "left": {"price": "close"}, "right": operand},
        "sizing": {"mode": "fixed_shares", "value": 1},
    }


def run(store: CustomIndicatorStore, definition: dict[str, Any], prices: list[float]) -> Any:
    clean = validate_definition(definition, store.indicator)
    inputs = [instrument("a", bars(flat(*prices)))]
    source = RuleSignals(clean, inputs, store.indicator)
    results = Backtest("run1", clean, inputs, EngineConfig(Decimal(1000), "USD"), source).run()
    return results, source


@pytest.fixture
def store(tmp_path: Path) -> CustomIndicatorStore:
    return CustomIndicatorStore(tmp_path / "indicators.sqlite3", lambda: NOW)


PRICES = [10, 9, 8, 9, 11, 12, 10, 8, 9, 12, 13, 11, 9, 10, 14]


def test_validation_checks_the_saved_version_its_settings_and_outputs(
    store: CustomIndicatorStore,
) -> None:
    item = saved(store, MEAN)
    good = {"indicator": item["type"], "params": {"length": 4}, "output": "mean"}
    assert validate_definition(crossing(good), store.indicator)["entry"]["right"] == good
    bare = validate_definition(crossing({"indicator": item["type"]}), store.indicator)
    assert bare["entry"]["right"] == {"indicator": item["type"], "params": {}, "output": "mean"}
    problems = {
        f"py:{item['indicator_id']}@2": "not a saved Python indicator version",
        "py:ind-missing@1": "not a saved Python indicator version",
        "py:nonsense": "must look like py:",
    }
    for indicator, message in problems.items():
        with pytest.raises(StrategyError, match=message):
            validate_definition(crossing({"indicator": indicator}), store.indicator)
    with pytest.raises(StrategyError, match="no output 'slow'"):
        validate_definition(crossing({**good, "output": "slow"}), store.indicator)
    with pytest.raises(StrategyError, match="unknown settings: period"):
        validate_definition(crossing({**good, "params": {"period": 3}}), store.indicator)
    with pytest.raises(StrategyError, match="length must be a number"):
        validate_definition(crossing({**good, "params": {"length": "3"}}), store.indicator)
    with pytest.raises(StrategyError, match="indicator store"):
        validate_definition(crossing(good))


def test_a_python_indicator_gives_the_same_trades_as_the_built_in(
    store: CustomIndicatorStore,
) -> None:
    item = saved(store, MEAN)
    custom, source = run(store, crossing({"indicator": item["type"]}), PRICES)
    builtin, _ = run(store, crossing({"indicator": "sma", "params": {"length": 3}}), PRICES)
    assert custom["fills"] and custom["fills"] == builtin["fills"]
    assert custom["metrics"] == builtin["metrics"]
    assert source.custom_pins == [
        {
            "type": item["type"],
            "indicator_id": item["indicator_id"],
            "version": 1,
            "name": "Hand SMA",
            "code_sha256": hashlib.sha256(MEAN.encode()).hexdigest(),
        }
    ]


def test_reruns_are_identical_and_future_bars_never_change_earlier_signals(
    store: CustomIndicatorStore,
) -> None:
    item = saved(store, MEAN)
    gap = {"indicator": item["type"], "params": {"length": 3}, "output": "gap"}
    definition = {
        "type": "rules",
        "entry": {"op": "cross_above", "left": gap, "right": {"value": 0}},
        "exit": {"op": "cross_below", "left": gap, "right": {"value": 0}},
        "sizing": {"mode": "fixed_shares", "value": 1},
    }
    first, _ = run(store, definition, PRICES[:9])
    again, _ = run(store, definition, PRICES[:9])
    assert json.dumps(first, sort_keys=True) == json.dumps(again, sort_keys=True)
    later, source = run(store, definition, [*PRICES[:9], 50, 1, 50, 1])
    assert later["fills"][: len(first["fills"])] == first["fills"]
    assert later["equity_curve"][:9] == first["equity_curve"]
    early_series = RuleSignals(
        validate_definition(definition, store.indicator),
        [instrument("a", bars(flat(*PRICES[:9])))],
        store.indicator,
    ).evaluators["a"]
    later_series = source.evaluators["a"]
    for i in range(9):
        for rule in ("entry", "exit"):
            condition = validate_definition(definition, store.indicator)[rule]
            assert early_series.holds(condition, i) == later_series.holds(condition, i)


def test_indicator_code_errors_fail_with_the_indicator_and_the_reason(
    store: CustomIndicatorStore,
) -> None:
    item = saved(store, BROKEN)
    with pytest.raises(runner.IndicatorError, match=rf"{item['type']} failed on a: .*bad bar"):
        run(store, crossing({"indicator": item["type"]}), PRICES)


@pytest.fixture
def api(tmp_path: Path) -> Iterator[tuple[TestClient, BacktestRunner, CustomIndicatorStore]]:
    indicators = CustomIndicatorStore(tmp_path / "indicators.sqlite3", lambda: NOW)
    jobs = BacktestRunner(
        BacktestStore(tmp_path / "backtests.sqlite3", lambda: NOW),
        lambda: synthetic_bars,
        indicators=lambda: indicators,
    )
    app = create_app(backtest_runner=jobs, indicator_store=indicators, clock=lambda: NOW)
    with TestClient(app) as client:
        yield client, jobs, indicators
    jobs.stop()


def submit(client: TestClient, jobs: BacktestRunner, definition: dict[str, Any]) -> Any:
    response = client.post(
        "/v1/backtests/strategies", json={"name": "py rules", "definition": definition}
    )
    assert response.status_code == 201, response.text
    run_id = client.post(
        "/v1/backtests/runs",
        json={"strategy_id": response.json()["strategy_id"], "config": CONFIG},
    ).json()["run_id"]
    assert jobs.wait_idle()
    return client.get(f"/v1/backtests/runs/{run_id}").json()


def test_a_run_through_the_api_pins_the_indicator_code(api: Any) -> None:
    client, jobs, _indicators = api
    item = client.post("/v1/indicators/custom", json={"code": MEAN}).json()
    # A later version does not change what a strategy pinned to version 1 runs.
    newer = client.post(
        "/v1/indicators/custom",
        json={"code": MEAN + "\n# v2\n", "indicator_id": item["indicator_id"]},
    ).json()
    assert newer["version"] == 2
    operand = {"indicator": item["type"], "params": {"length": 5}}
    run = submit(client, jobs, crossing(operand))
    assert run["status"] == "succeeded", run["error"]
    results = client.get(f"/v1/backtests/runs/{run['run_id']}/results").json()
    validate_document(results)
    assert results["metrics"]["trade_count"] > 0
    assert results["custom_indicators"] == [
        {
            "type": item["type"],
            "indicator_id": item["indicator_id"],
            "version": 1,
            "name": "Hand SMA",
            "code_sha256": item["code_sha256"],
        }
    ]
    again = submit(client, jobs, crossing(operand))
    repeat = client.get(f"/v1/backtests/runs/{again['run_id']}/results").json()
    assert repeat["metrics"] == results["metrics"]
    assert repeat["custom_indicators"] == results["custom_indicators"]
    ids = ("fill_id", "run_id", "order_id")
    assert [{k: v for k, v in f.items() if k not in ids} for f in repeat["fills"]] == [
        {k: v for k, v in f.items() if k not in ids} for f in results["fills"]
    ]
    bad = client.post(
        "/v1/backtests/strategies",
        json={"name": "x", "definition": crossing({"indicator": "py:ind-missing@1"})},
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "invalid_strategy"


def test_a_failing_indicator_fails_the_run_with_the_reason(api: Any) -> None:
    client, jobs, _indicators = api
    item = client.post("/v1/indicators/custom", json={"code": BROKEN}).json()
    run = submit(client, jobs, crossing({"indicator": item["type"]}))
    assert run["status"] == "failed"
    assert item["type"] in run["error"] and "bad bar" in run["error"]
