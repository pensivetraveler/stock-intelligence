"""Portfolio engine: fill timing, costs, stops, rejections, determinism, no look-ahead."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest

from services.backtest.data import EngineBar, InstrumentInput
from services.backtest.engine import Backtest, Cancelled, EngineConfig, PythonSignals, RuleSignals
from services.backtest.strategy import StrategyError, fingerprint, validate_definition

DAY = 86_400
START = 1_760_000_000


def bars(prices: list[tuple[float, float, float, float]], final: bool = True) -> list[EngineBar]:
    return [
        EngineBar(
            START + i * DAY,
            START + i * DAY + 3600,
            *(Decimal(str(p)) for p in ohlc),
            Decimal(1000),
            final,
        )
        for i, ohlc in enumerate(prices)
    ]


def flat(*closes: float) -> list[tuple[float, float, float, float]]:
    return [(c, c, c, c) for c in closes]


def instrument(instrument_id: str, data: list[EngineBar]) -> InstrumentInput:
    return InstrumentInput(
        instrument_id, "America/New_York", data, {"instrument_id": instrument_id}
    )


CROSS = {
    "type": "rules",
    "entry": {"op": "gt", "left": {"price": "close"}, "right": {"value": 10}},
    "exit": {"op": "lt", "left": {"price": "close"}, "right": {"value": 10}},
    "sizing": {"mode": "fixed_shares", "value": 10},
}


def run(
    definition: dict[str, Any],
    inputs: list[InstrumentInput],
    capital: str = "1000",
    **costs: str,
) -> dict[str, Any]:
    clean = validate_definition(definition)
    config = EngineConfig(Decimal(capital), "USD", **{k: Decimal(v) for k, v in costs.items()})
    source = (
        RuleSignals(clean, inputs) if clean["type"] == "rules" else PythonSignals(clean, inputs)
    )
    return Backtest("run1", clean, inputs, config, source).run()


def test_signal_at_close_fills_at_next_open_with_costs() -> None:
    data = bars([(9, 9, 9, 9), (9, 11, 9, 11), (12, 12, 12, 12), (13, 13, 13, 13)])
    result = run(
        CROSS, [instrument("a", data)], fee_rate="0.001", fee_per_order="1", slippage_bps="100"
    )
    (fill,) = result["fills"]
    # Signal on bar 1's close (11); filled at bar 2's open (12) plus 1% slippage.
    assert fill["filled_at_utc"].startswith("2025-10-11")
    assert fill["price"] == "12.12"
    assert fill["fee"] == "1.12"  # 121.2 x 0.1% + 1
    assert result["final_equity"] == "1007.68"  # 1000 - 121.2 - 1.1212 + 10 x 13
    assert result["metrics"]["trade_count"] == 0
    assert result["trades"][0]["exit_reason"] == "open_at_end"


def test_never_fills_on_the_signalling_bar_and_ignores_non_final_bars() -> None:
    data = bars(flat(9, 11)) + [
        EngineBar(START + 2 * DAY, START + 2 * DAY + 3600, *[Decimal(12)] * 4, Decimal(1), False)
    ]
    assert len(run(CROSS, [instrument("a", data)])["fills"]) == 1
    only_signal = bars(flat(9, 11))
    result = run(CROSS, [instrument("a", only_signal)])
    assert result["fills"] == []
    assert result["orders"][0]["status"] == "unfilled_at_end"
    non_final = bars(flat(9, 11, 12), final=False)
    assert run(CROSS, [instrument("a", non_final)])["orders"] == []


def test_stop_and_target_in_one_bar_assume_the_stop() -> None:
    definition = {**CROSS, "exit": None, "stop_loss_pct": 5, "take_profit_pct": 5}
    data = bars([(11, 11, 11, 11), (10, 10, 10, 10), (10, 11, 9, 10)])
    result = run(definition, [instrument("a", data)])
    entry, exit_ = result["fills"]
    assert entry["price"] == "10"
    assert exit_["fill_reason"] == "stop_loss"
    assert exit_["price"] == "9.5"
    assert exit_["notes"] == ["ambiguous_stop_target"]
    assert result["trades"][0]["net_pnl"] == "-5.00"


def test_gap_through_the_stop_fills_at_the_open() -> None:
    definition = {**CROSS, "exit": None, "stop_loss_pct": 5}
    data = bars([(11, 11, 11, 11), (10, 10, 10, 10), (8, 8.5, 7, 8)])
    (_, exit_) = run(definition, [instrument("a", data)])["fills"]
    assert exit_["price"] == "8"


def test_rejections_are_recorded_with_reasons() -> None:
    expensive = {**CROSS, "sizing": {"mode": "fixed_shares", "value": 1000}}
    result = run(expensive, [instrument("a", bars(flat(11, 12)))])
    assert result["orders"][0]["status"] == "rejected"
    assert result["orders"][0]["reason"] == "insufficient_cash"
    limited = {**CROSS, "max_positions": 1}
    result = run(
        limited, [instrument("a", bars(flat(11, 12))), instrument("b", bars(flat(11, 12)))]
    )
    assert [(o["instrument_id"], o["status"], o["reason"]) for o in result["orders"]] == [
        ("a", "filled", None),
        ("b", "rejected", "position_limit"),
        ("b", "rejected", "position_limit"),  # signals again on the next close
    ]


def test_percent_equity_is_sized_at_the_fill_open_and_capped_by_cash() -> None:
    definition = {**CROSS, "sizing": {"mode": "percent_equity", "value": 100}}
    result = run(definition, [instrument("a", bars(flat(11, 12)))], fee_per_order="1")
    assert result["fills"][0]["quantity"] == "83"  # (1000 - 1) / 12
    assert result["fills"][0].get("notes") is None


def test_round_trip_metrics() -> None:
    data = bars(flat(9, 11, 12, 9, 8, 11, 10, 9.5, 9))
    result = run(CROSS, [instrument("a", data)])
    metrics = result["metrics"]
    assert metrics["trade_count"] == 2
    assert [t["net_pnl"] for t in result["trades"]] == [
        "-40.00",
        "-10.00",
    ]  # 12->8 and 10->9, 10 shares
    assert metrics["win_rate"] == "0.000000"
    assert metrics["profit_factor"] == "0.000000"
    assert metrics["benchmark_return"] == "0.000000"


def test_identical_inputs_give_identical_results() -> None:
    inputs = [
        instrument("a", bars(flat(9, 11, 12, 9, 8, 11, 10))),
        instrument("b", bars(flat(12, 9, 11, 13))),
    ]
    first = json.dumps(run(CROSS, inputs), sort_keys=True)
    assert first == json.dumps(run(CROSS, inputs), sort_keys=True)


def test_future_bars_never_change_earlier_decisions() -> None:
    definition = {
        "type": "rules",
        "entry": {
            "op": "cross_above",
            "left": {"price": "close"},
            "right": {"indicator": "sma", "params": {"length": 3}},
        },
        "exit": {
            "op": "cross_below",
            "left": {"price": "close"},
            "right": {"indicator": "sma", "params": {"length": 3}},
        },
        "sizing": {"mode": "fixed_shares", "value": 1},
    }
    prices = [10, 9, 8, 9, 11, 12, 10, 8, 9, 12, 13, 11, 9, 10, 14]
    early = run(definition, [instrument("a", bars(flat(*prices[:9])))])
    later = run(definition, [instrument("a", bars(flat(*prices[:9], 50, 1, 50, 1)))])
    assert later["fills"][: len(early["fills"])] == early["fills"]
    assert later["equity_curve"][:9] == early["equity_curve"]


def test_python_strategy_runs_in_the_sandbox() -> None:
    definition = {
        "type": "python",
        "params": {"level": 10},
        "code": (
            "def on_bar(ctx):\n"
            "    print(ctx.time, ctx.bar.close, len(ctx.history))\n"
            "    if ctx.position is None and ctx.bar.close > ctx.params['level']:\n"
            "        return ctx.buy(quantity=10)\n"
            "    if ctx.position and ctx.bar.close < ctx.params['level']:\n"
            "        return ctx.sell()\n"
        ),
    }
    data = bars(flat(9, 11, 12, 9, 8))
    python = run(definition, [instrument("a", data)])
    rules = run(CROSS, [instrument("a", data)])
    assert python["fills"] == rules["fills"]
    assert python["metrics"] == rules["metrics"]


def test_python_strategy_errors_fail_the_run() -> None:
    definition = {"type": "python", "code": "def on_bar(ctx):\n    return 1 / 0\n"}
    with pytest.raises(RuntimeError, match="ZeroDivisionError"):
        run(definition, [instrument("a", bars(flat(9, 11)))])


def test_cancellation_stops_the_run() -> None:
    clean = validate_definition(CROSS)
    inputs = [instrument("a", bars(flat(9, 11, 12)))]
    backtest = Backtest(
        "r",
        clean,
        inputs,
        EngineConfig(Decimal(1000), "USD"),
        RuleSignals(clean, inputs),
        cancelled=lambda: True,
    )
    with pytest.raises(Cancelled):
        backtest.run()


def test_definitions_are_validated_and_fingerprinted() -> None:
    clean = validate_definition(CROSS)
    assert fingerprint(clean) == fingerprint(validate_definition(dict(reversed(CROSS.items()))))
    for broken, message in [
        ({**CROSS, "entry": {"op": "near"}}, "comparison"),
        (
            {**CROSS, "entry": {"op": "gt", "left": {"indicator": "nope"}, "right": {"value": 1}}},
            "unknown indicator",
        ),
        ({**CROSS, "sizing": {"mode": "percent_equity", "value": 150}}, "between"),
        ({"type": "python", "code": "def on_bar(ctx) pass"}, "does not compile"),
        ({"type": "shell"}, "type must be"),
    ]:
        with pytest.raises(StrategyError, match=message):
            validate_definition(broken)


def test_unadjusted_split_like_gaps_are_reported() -> None:
    from services.backtest.data import suspected_splits

    data = bars([(100, 101, 99, 100), (10.02, 10.5, 9.9, 10.1), (10, 10, 10, 10), (13, 13, 13, 13)])
    assert suspected_splits(data) == ["2025-10-10"]
    assert suspected_splits(bars(flat(100, 150, 90, 100))) == []
