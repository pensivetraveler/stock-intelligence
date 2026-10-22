"""Walk-forward runs (ADR 0032): parameters chosen in-sample, traded out-of-sample."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from packages.contracts.validator import validate_document
from services.backtest.engine import EngineConfig, RuleSignals
from services.backtest.strategy import validate_definition
from services.backtest.walkforward import WalkForward, WalkForwardError, clean_spec, windows
from tests.backtest.test_backtest_api import (
    CONFIG,
    RULES,
    save,
    setup,  # noqa: F401 (a fixture)
)
from tests.backtest.test_engine import bars, instrument

THRESHOLD = {
    "type": "rules",
    "entry": {"op": "gt", "left": {"price": "close"}, "right": {"value": 50}},
    "exit": None,
    "sizing": {"mode": "fixed_shares", "value": 10},
}
SPEC = {
    "in_sample_bars": 20,
    "out_of_sample_bars": 10,
    "objective": "net_return",
    "grid": [{"path": "/entry/right/value", "values": [50, 5]}],
}


def rising(count: int) -> list[Any]:
    return bars([(10 + i, 10.5 + i, 9.5 + i, 10 + i) for i in range(count)])


def walk(spec: dict[str, Any], count: int = 60) -> dict[str, Any]:
    inputs = [instrument("a", rising(count))]
    definition = validate_definition(THRESHOLD)
    return WalkForward(
        "wf1",
        definition,
        clean_spec(spec),
        inputs,
        EngineConfig(Decimal(100000), "USD"),
        lambda chosen: RuleSignals(chosen, inputs),
    ).run()


def test_windows_roll_by_the_out_of_sample_stretch() -> None:
    times = list(range(60))
    assert windows(times, clean_spec(SPEC)) == [
        (0, 20, 30, 29),
        (10, 30, 40, 39),
        (20, 40, 50, 49),
        (30, 50, 60, 59),
    ]
    with pytest.raises(WalkForwardError):
        windows(list(range(20)), clean_spec(SPEC))


def test_the_in_sample_winner_trades_only_out_of_sample() -> None:
    result = walk(SPEC)  # the full document is validated in the API test below
    section = result["walk_forward"]
    assert section["combinations"] == 2
    assert len(section["windows"]) == 4
    # A threshold of 50 never trades on prices from 10 to 69; 5 buys and gains.
    assert {w["chosen"][0]["value"] for w in section["windows"]} == {"5"}
    first_out = section["windows"][0]["out_of_sample_start_utc"]
    assert result["fills"] and all(f["filled_at_utc"] >= first_out for f in result["fills"])
    assert result["equity_curve"][0]["time_utc"] == first_out


def test_ties_keep_the_first_combination_in_grid_order() -> None:
    flat_spec = {**SPEC, "grid": [{"path": "/entry/right/value", "values": [500, 1000]}]}
    section = walk(flat_spec)["walk_forward"]
    assert {w["chosen"][0]["value"] for w in section["windows"]} == {"500"}
    assert {w["in_sample_score"] for w in section["windows"]} == {"0"}


def test_settings_and_paths_are_checked() -> None:
    for bad in (
        {**SPEC, "in_sample_bars": 5},
        {**SPEC, "objective": "luck"},
        {**SPEC, "grid": []},
        {**SPEC, "grid": [{"path": "entry", "values": [1]}]},
        {**SPEC, "grid": [{"path": "/entry/right/value", "values": [1, 1]}]},
    ):
        with pytest.raises(WalkForwardError):
            clean_spec(bad)
    with pytest.raises(WalkForwardError, match="not a number"):
        walk({**SPEC, "grid": [{"path": "/entry/missing", "values": [1]}]})
    with pytest.raises(WalkForwardError, match="exceed"):
        walk(
            {
                **SPEC,
                "out_of_sample_bars": 5,
                "grid": [
                    {"path": "/entry/right/value", "values": list(range(1, 21))},
                    {"path": "/sizing/value", "values": list(range(1, 4))},
                ],
            },
            count=200,
        )


def test_a_walk_forward_run_through_the_api(setup: Any) -> None:  # noqa: F811
    client, runner, _journal = setup
    strategy = save(client, RULES)
    config = {
        **CONFIG,
        "walk_forward": {
            "in_sample_bars": 40,
            "out_of_sample_bars": 20,
            "objective": "return_over_drawdown",
            "grid": [{"path": "/entry/right/params/length", "values": [3, 5]}],
        },
    }
    submitted = client.post(
        "/v1/backtests/runs", json={"strategy_id": strategy["strategy_id"], "config": config}
    )
    assert submitted.status_code == 202, submitted.text
    assert runner.wait_idle()
    run_id = submitted.json()["run_id"]
    run = client.get(f"/v1/backtests/runs/{run_id}").json()
    assert run["status"] == "succeeded", run["error"]
    results = client.get(f"/v1/backtests/runs/{run_id}/results").json()
    validate_document(results)
    assert results["walk_forward"]["objective"] == "return_over_drawdown"
    assert len(results["walk_forward"]["windows"]) >= 3
    refused = client.post(
        "/v1/backtests/runs",
        json={
            "strategy_id": strategy["strategy_id"],
            "config": {**config, "walk_forward": {**config["walk_forward"], "grid": []}},
        },
    )
    assert refused.status_code == 422
