"""Base-bar Fibonacci setup (ADR 0061): state, legs, ladders, breakeven, no look-ahead."""

from __future__ import annotations

import copy
from decimal import Decimal
from typing import Any

import pytest

from services.backtest.data import EngineBar, InstrumentInput
from services.backtest.engine import Backtest, EngineConfig, RuleSignals
from services.backtest.strategy import StrategyError, validate_definition

DAY = 86_400
START = 1_760_000_000

# Base bar 1 (low 10, high 12, +20 %), pullback, breakout bar 3 (close 13.5, high 14).
# Lines from 10 to 14: 0 = 14, 0.25 = 13, 0.5 = 12, 0.625 = 11.5, 0.75 = 11, 0.875 = 10.5,
# 1 = 10.
LEAD = [(10, 10, 10, 10), (10, 12, 10, 12), (12, 12, 11, 11), (11, 14, 11, 13.5)]


def bars(prices: list[tuple[float, ...]]) -> list[EngineBar]:
    out = []
    for i, row in enumerate(prices):
        o, h, low, c = row[:4]
        volume = row[4] if len(row) > 4 else 1000
        out.append(
            EngineBar(
                START + i * DAY,
                START + i * DAY + 3600,
                *(Decimal(str(p)) for p in (o, h, low, c)),
                Decimal(str(volume)),
                True,
            )
        )
    return out


def setup(**changes: Any) -> dict[str, Any]:
    definition: dict[str, Any] = {
        "type": "rules",
        "setup": {
            "kind": "base_fib",
            "base": {
                "op": "gte",
                "left": {"indicator": "roc", "params": {"length": 1}},
                "right": {"value": "15"},
            },
            "breakout": None,
            "breakout_within": 10,
            "entry_within": 10,
            "legs": [
                {"level": "0.5", "size_pct": "50", "targets": [{"level": "0", "sell_pct": "100"}]},
                {
                    "level": "0.75",
                    "size_pct": "50",
                    "targets": [{"level": "0.25", "sell_pct": "100"}],
                },
            ],
            "stop_level": "1",
            "breakeven_after_target": False,
        },
        "sizing": {"mode": "fixed_shares", "value": 10},
    }
    definition["setup"].update(changes.pop("setup", {}))
    definition.update(changes)
    return definition


ONE_POINT_875 = {
    "legs": [
        {
            "level": "0.875",
            "size_pct": "100",
            "targets": [{"level": "0.625", "sell_pct": "50"}, {"level": "0.25", "sell_pct": "100"}],
        }
    ],
    "breakeven_after_target": True,
}


def run(definition: dict[str, Any], prices: list[tuple[float, ...]], capital: str = "1000"):
    clean = validate_definition(definition)
    inputs = [InstrumentInput("a", "America/New_York", bars(prices), {"instrument_id": "a"})]
    source = RuleSignals(clean, inputs)
    return Backtest("r", clean, inputs, EngineConfig(Decimal(capital), "USD"), source).run()


def fills(result: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    return [(f["side"], f["quantity"], f["price"], f["fill_reason"]) for f in result["fills"]]


def test_two_punch_buys_both_legs_and_sells_at_the_deeper_legs_line() -> None:
    result = run(
        setup(),
        [*LEAD, (13.5, 14, 11.9, 12.5), (12.5, 12.6, 10.9, 11.2), (11.2, 13.2, 11.1, 13.1)],
    )
    assert fills(result) == [
        ("buy", "5", "12", "limit_order"),
        ("buy", "5", "11", "limit_order"),
        ("sell", "10", "13", "take_profit"),
    ]
    (trade,) = result["trades"]
    assert trade["entry_price"] == "11.5"
    assert trade["quantity"] == "10"
    assert trade["exit_reason"] == "take_profit"


def test_only_the_first_leg_filled_sells_at_the_zero_line() -> None:
    result = run(setup(), [*LEAD, (13.5, 14, 11.9, 12.5), (12.5, 14.2, 12.4, 14)])
    assert fills(result) == [("buy", "5", "12", "limit_order"), ("sell", "5", "14", "take_profit")]
    # The deeper leg was cancelled once a target filled.
    assert [o for o in result["orders"] if o["status"] == "cancelled"]


def test_targets_wait_a_bar_after_a_fill_inside_the_bar() -> None:
    # Bar 4 trades through 12 and up through 14: the high may have come before the fill.
    result = run(setup(), [*LEAD, (13.5, 14.5, 11.9, 12.5)])
    assert fills(result) == [("buy", "5", "12", "limit_order")]


def test_stop_at_the_base_bars_low_and_a_gap_fills_at_the_open() -> None:
    result = run(setup(), [*LEAD, (13.5, 14, 11.9, 12.5), (12, 12.2, 9.9, 10.1)])
    assert fills(result)[-1] == ("sell", "10", "10", "stop_loss")
    gapped = run(setup(), [*LEAD, (13.5, 14, 11.9, 12.5), (9.5, 9.8, 9, 9.2)])
    # A gap below both lines buys the second leg at the open, then stops out at that open.
    assert fills(gapped)[-2:] == [
        ("buy", "5", "9.5", "limit_order"),
        ("sell", "10", "9.5", "stop_loss"),
    ]


def test_point_875_sells_half_then_the_rest_stops_at_breakeven() -> None:
    result = run(
        setup(setup=ONE_POINT_875),
        [
            *LEAD,
            (13.5, 13.6, 10.4, 10.8),
            (10.8, 11.6, 10.7, 11.4),
            (11.4, 11.45, 10.4, 10.6),
        ],
    )
    assert fills(result) == [
        ("buy", "10", "10.5", "limit_order"),
        ("sell", "5", "11.5", "take_profit"),
        ("sell", "5", "10.5", "breakeven_stop"),
    ]
    assert result["trades"][0]["exit_reason"] == "breakeven_stop"


def test_breakeven_starts_the_bar_after_the_target() -> None:
    # The target bar itself falls to 10.45, under the entry: the stop was still at 10.
    result = run(
        setup(setup=ONE_POINT_875),
        [*LEAD, (13.5, 13.6, 10.4, 10.8), (10.8, 11.6, 10.45, 11.4)],
    )
    assert fills(result)[-1] == ("sell", "5", "11.5", "take_profit")
    assert result["trades"][0]["open_quantity"] == "5"


def test_the_high_moves_the_lines_until_the_first_fill_then_freezes() -> None:
    # Bar 4 makes a new high (16) without a fill: the 0.5 line moves from 12 to 13.
    moved = run(setup(), [*LEAD, (13.5, 16, 13.1, 15), (15, 15, 12.9, 13.2)])
    assert fills(moved)[0] == ("buy", "5", "13", "limit_order")
    # The fill bar's own high (15) is not used: the zero line stays at 14.
    frozen = run(setup(), [*LEAD, (13.5, 15, 11.9, 12.5), (12.5, 14.5, 12.4, 14.2)])
    assert fills(frozen)[-1] == ("sell", "5", "14", "take_profit")


def test_appending_bars_never_changes_earlier_fills() -> None:
    prices = [*LEAD, (13.5, 14, 11.9, 12.5), (12.5, 12.6, 10.9, 11.2), (11.2, 13.2, 11.1, 13.1)]
    full = run(setup(), prices)["fills"]
    for cut in range(len(LEAD), len(prices)):
        early = run(setup(), prices[:cut])["fills"]
        assert full[: len(early)] == early


def test_expiry_and_a_broken_structure_reset_the_setup() -> None:
    expired = run(
        setup(setup={"entry_within": 1}),
        [*LEAD, (13.5, 14, 13, 13.5), (13.5, 14, 13, 13.5), (13, 13, 11.5, 12)],
    )
    assert fills(expired) == []
    assert {o["status"] for o in expired["orders"]} == {"expired"}
    broken = run(setup(), [*LEAD[:3], (11, 11.5, 9.5, 10), (10, 13, 10, 12.9)])
    assert fills(broken) == [] and broken["orders"] == []


def test_breakout_reads_the_base_bar_value() -> None:
    rule = {"op": "gt", "left": {"indicator": "tv"}, "right": {"base": "value"}}
    weak = [(10, 10, 10, 10), (10, 12, 10, 12, 1000), (12, 12, 11, 11), (11, 14, 11, 13.5, 800)]
    strong = [*weak[:3], (11, 14, 11, 13.5, 1000)]  # 13.5 x 1000 > 12 x 1000
    after = [(13.5, 14, 11.9, 12.5)]
    assert fills(run(setup(setup={"breakout": rule}), weak + after)) == []
    assert fills(run(setup(setup={"breakout": rule}), strong + after))[0][0] == "buy"


def test_base_can_require_a_short_upper_wick() -> None:
    # Original One-Punch book 2 base bar: upper wick at most 30 % of the bar's range.
    base = {"all": [
        {"op": "gte", "left": {"indicator": "roc", "params": {"length": 1}},
         "right": {"value": "15"}},
        {"op": "lte", "left": {"indicator": "upper_wick_pct"}, "right": {"value": "30"}},
    ]}  # fmt: skip
    rest = [(12, 12, 11, 11), (11, 15, 11, 14.5), (14.5, 14.5, 10.9, 11.5)]
    clean = [(10, 10, 10, 10), (10, 12, 10, 12), *rest]  # no wick
    wicked = [(10, 10, 10, 10), (10, 14, 10, 12), *rest]  # wick (14 - 12) / 4 = 50 %
    assert fills(run(setup(setup={"base": base}), clean))
    assert fills(run(setup(setup={"base": base}), wicked)) == []


def test_risk_pct_sizes_by_the_distance_to_the_stop() -> None:
    one_punch = {"legs": [{"level": "0.75", "size_pct": "100",
                            "targets": [{"level": "0.25", "sell_pct": "100"}]}]}  # fmt: skip
    result = run(
        setup(setup=one_punch, sizing={"mode": "risk_pct", "value": "1"}),
        [*LEAD, (13.5, 14, 10.9, 11.5)],
    )
    # 1 % of 1000 over (11 - 10) per share.
    assert fills(result) == [("buy", "10", "11", "limit_order")]


def _legs(size: str, line: str, sell: str) -> dict[str, Any]:
    targets = [{"level": line, "sell_pct": sell}]
    return {"setup": {"legs": [{"level": "0.5", "size_pct": size, "targets": targets}]}}


ANY_ENTRY = {"op": "gt", "left": {"price": "close"}, "right": {"value": 1}}
BASE_IN_BASE = {"op": "gt", "left": {"base": "high"}, "right": {"value": 1}}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"entry": ANY_ENTRY}, "does not take"),
        ({"setup": {"stop_level": "0.6"}}, "above the stop line"),
        (_legs("60", "0", "100"), "add up to 100"),
        (_legs("100", "0", "50"), "must sell 100"),
        (_legs("100", "0.6", "100"), "above the one before"),
        ({"setup": {"base": BASE_IN_BASE}}, "only for a setup's breakout"),
    ],
)
def test_invalid_setups_are_refused(change: dict[str, Any], message: str) -> None:
    with pytest.raises(StrategyError, match=message):
        validate_definition(setup(**copy.deepcopy(change)))


def test_risk_pct_is_only_for_setups() -> None:
    with pytest.raises(StrategyError, match="sizing mode"):
        validate_definition(
            {
                "type": "rules",
                "entry": {"op": "gt", "left": {"price": "close"}, "right": {"value": 1}},
                "sizing": {"mode": "risk_pct", "value": "1"},
            }
        )


def test_orders_and_trades_fit_the_results_contract() -> None:
    from jsonschema import Draft202012Validator, FormatChecker

    from packages.contracts.validator import load_schema

    schema = load_schema()
    results = schema["$defs"]["BacktestResults"]["properties"]
    run_result = run(
        setup(setup=ONE_POINT_875),
        [*LEAD, (13.5, 13.6, 10.4, 10.8), (10.8, 11.6, 10.7, 11.4), (11.4, 11.45, 10.4, 10.6)],
    )
    two_punch = run(setup(), [*LEAD, (13.5, 14, 11.9, 12.5), (12.5, 14.2, 12.4, 14)])
    for name in ("orders", "trades", "fills"):
        check = Draft202012Validator(
            {"$defs": schema["$defs"], **results[name]}, format_checker=FormatChecker()
        )
        for result in (run_result, two_punch):
            check.validate(result[name])


def test_setups_are_backtest_only_and_have_no_pine_template() -> None:
    from services.backtest.pine_template import to_pine
    from services.paper_trading.intent import StrategyRef
    from services.paper_trading.limits import PaperLimits
    from services.paper_trading.runner import InstrumentPlan, RunPlan
    from services.paper_trading.signals import rule_signals

    clean = validate_definition(setup())
    pine = to_pine(clean, "s")
    assert pine["source"] == "" and pine["unsupported"][0].startswith("/setup")
    with pytest.raises(ValueError, match="backtests only"):
        RunPlan(
            strategy=StrategyRef("s", 1, "c" * 64, "rules"),
            definition=clean,
            instruments=(InstrumentPlan("a", "A", "America/New_York"),),
            limits=PaperLimits(("a",), max_order_amount_krw=Decimal("1000000")),
            broker_id="kiwoom",
            broker_account_ref="acct",
        )
    with pytest.raises(StrategyError, match="backtests only"):
        rule_signals(clean, "a", "America/New_York", bars(LEAD), holding=False)
