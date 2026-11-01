"""Rule builder extensions (ADR 0042): earlier bars, time of day, ATR and trailing stops."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from services.backtest.data import EngineBar
from services.backtest.engine import _indicator_bars
from services.backtest.jobs import BacktestRunner, RunConfigError
from services.backtest.store import BacktestStore
from services.backtest.strategy import (
    RuleEvaluator,
    StrategyError,
    fingerprint,
    validate_definition,
)
from services.backtest.walkforward import variants
from services.paper_trading.signals import rule_signals
from tests.backtest.test_engine import CROSS, bars, instrument, run

TZ = "America/New_York"
CLOSE = {"price": "close"}
PRIOR_CLOSE = {"price": "close", "offset": 1}
RISING = {"op": "gt", "left": CLOSE, "right": PRIOR_CLOSE}
SIGNAL = (10.0, 11.0, 9.0, 11.0)  # close above 10: CROSS enters on the next open
BASE = (10.0, 11.0, 9.0, 10.0)  # a true range of 2 and no signal


def intraday(start: datetime, closes: list[float], minutes: int = 5) -> list[EngineBar]:
    found = []
    for i, close in enumerate(closes):
        at = int((start + timedelta(minutes=i * minutes)).timestamp())
        price = Decimal(str(close))
        found.append(
            EngineBar(at, at + minutes * 60, price, price, price, price, Decimal(100), True)
        )
    return found


def stops(*rows: tuple[float, float, float, float], **extra: Any) -> dict[str, Any]:
    definition = {**CROSS, "exit": None, **extra}
    return run(definition, [instrument("a", bars(list(rows)))])


# --- earlier bars ---------------------------------------------------------------------


def test_an_earlier_bar_is_read_and_nothing_before_the_first_bar_exists() -> None:
    data = bars([(c, c, c, c) for c in (10.0, 10.0, 11.0, 11.0)])
    definition = {**CROSS, "entry": RISING, "exit": None}
    result = run(definition, [instrument("a", data)], capital="1000")
    (fill,) = result["fills"]
    # Rising only on bar 2 (11 > 10): filled at bar 3's open. Bar 0 has no earlier bar.
    assert fill["filled_at_utc"].startswith("2025-10-12")
    assert Decimal(fill["price"]) == Decimal(11)


def test_a_cross_compares_both_sides_one_bar_further_back() -> None:
    clean = validate_definition(
        {
            **CROSS,
            "entry": {
                "op": "cross_above",
                "left": PRIOR_CLOSE,
                "right": {"value": 10},
            },
        }
    )
    data = bars([(c, c, c, c) for c in (9.0, 11.0, 12.0)])
    evaluator = RuleEvaluator(clean, _indicator_bars(data), TZ)
    # The cross happened on bar 1, so with one bar back it shows on bar 2.
    assert [evaluator.holds(clean["entry"], i) for i in range(3)] == [False, False, True]


def test_an_earlier_indicator_value_is_the_same_series_shifted() -> None:
    sma = {"indicator": "sma", "params": {"length": 2}, "output": "value"}
    clean = validate_definition(
        {**CROSS, "entry": {"op": "gt", "left": {**sma, "offset": 1}, "right": {"value": 10}}}
    )
    data = bars([(c, c, c, c) for c in (10.0, 12.0, 8.0, 8.0)])
    evaluator = RuleEvaluator(clean, _indicator_bars(data), TZ)
    # SMA(2): -, 11, 10, 8. One bar back: -, -, 11, 10.
    assert [evaluator.holds(clean["entry"], i) for i in range(4)] == [False, False, True, False]


@pytest.mark.parametrize("offset", [0, -1, 501, 1.5, True, "1"])
def test_bad_offsets_are_refused(offset: object) -> None:
    with pytest.raises(StrategyError, match="offset"):
        validate_definition(
            {
                **CROSS,
                "entry": {"op": "gt", "left": {"price": "close", "offset": offset}, "right": CLOSE},
            }
        )


def test_a_fixed_value_has_no_earlier_bar() -> None:
    with pytest.raises(StrategyError, match="no earlier bar"):
        validate_definition(
            {**CROSS, "entry": {"op": "gt", "left": CLOSE, "right": {"value": 1, "offset": 1}}}
        )


def test_new_fields_left_out_keep_old_fingerprints() -> None:
    clean = validate_definition(CROSS)
    assert not {"stop_loss_atr", "trailing_stop"} & set(clean)
    assert "offset" not in clean["entry"]["left"]
    assert fingerprint(validate_definition({**CROSS, "stop_loss_atr": None})) == fingerprint(clean)
    assert fingerprint(validate_definition({**CROSS, "trailing_stop": None})) == fingerprint(clean)


# --- time of day ----------------------------------------------------------------------


def _window_holds(start: datetime, count: int = 3) -> list[bool]:
    clean = validate_definition({**CROSS, "entry": {"time": {"from": "09:35", "to": "09:40"}}})
    evaluator = RuleEvaluator(clean, _indicator_bars(intraday(start, [10.0] * count)), TZ)
    return [evaluator.holds(clean["entry"], i) for i in range(count)]


def test_the_window_uses_exchange_time_through_daylight_saving() -> None:
    # 09:30 New York is 13:30 UTC in July and 14:30 UTC in December. From is included,
    # until is not: of the bars at 09:30, 09:35 and 09:40 only 09:35 holds.
    assert _window_holds(datetime(2026, 7, 1, 13, 30, tzinfo=timezone.utc)) == [False, True, False]
    assert _window_holds(datetime(2026, 12, 1, 14, 30, tzinfo=timezone.utc)) == [False, True, False]


def test_time_conditions_combine_with_comparisons() -> None:
    clean = validate_definition(
        {
            **CROSS,
            "entry": {
                "all": [
                    {"time": {"from": "09:35", "to": "16:00"}},
                    {"op": "gt", "left": CLOSE, "right": {"value": 10}},
                ]
            },
        }
    )
    start = datetime(2026, 7, 1, 13, 30, tzinfo=timezone.utc)
    evaluator = RuleEvaluator(clean, _indicator_bars(intraday(start, [11.0, 9.0, 11.0])), TZ)
    assert [evaluator.holds(clean["entry"], i) for i in range(3)] == [False, False, True]


@pytest.mark.parametrize(
    "time",
    [
        {"from": "16:00", "to": "09:30"},
        {"from": "09:30", "to": "09:30"},
        {"from": "9:30", "to": "16:00"},
        {"from": "25:00", "to": "26:00"},
        {"from": "09:30"},
        {"from": 930, "to": 1600},
        "09:30",
    ],
)
def test_bad_time_windows_are_refused(time: object) -> None:
    with pytest.raises(StrategyError, match="time condition"):
        validate_definition({**CROSS, "entry": {"time": time}})


def test_a_time_condition_takes_no_other_fields() -> None:
    with pytest.raises(StrategyError, match="only field"):
        validate_definition(
            {**CROSS, "entry": {"time": {"from": "09:30", "to": "16:00"}, "op": "gt"}}
        )


@pytest.mark.parametrize("timeframe", ["1d", "1w"])
def test_daily_runs_refuse_time_conditions(tmp_path: Path, timeframe: str) -> None:
    store = BacktestStore(tmp_path / "backtests.sqlite3", lambda: datetime.now(timezone.utc))
    runner = BacktestRunner(store, lambda: None)  # type: ignore[arg-type,return-value]
    strategy = store.save_strategy(
        "windowed", {**CROSS, "entry": {"any": [{"time": {"from": "09:30", "to": "16:00"}}]}}
    )
    request = {
        "instrument_ids": ["us:aapl:xnas:2026"],
        "timeframe": timeframe,
        "start_date": "2026-01-02",
        "end_date": "2026-02-02",
        "initial_capital": "1000",
        "currency": "USD",
    }
    with pytest.raises(RunConfigError, match="intraday"):
        runner.submit(strategy["strategy_id"], None, request)


# --- ATR stop -------------------------------------------------------------------------


def test_the_atr_stop_uses_the_atr_known_before_the_fill() -> None:
    result = stops(
        BASE,
        BASE,
        SIGNAL,
        (12, 30, 11, 12),  # a huge range on the fill bar must not widen this stop
        (12, 12, 8.5, 9),
        stop_loss_atr={"length": 2, "mult": "1.5"},
    )
    entry, exit_ = result["fills"]
    assert Decimal(entry["price"]) == Decimal(12)
    assert exit_["fill_reason"] == "stop_loss"
    assert Decimal(exit_["price"]) == Decimal(9)  # 12 - 1.5 x ATR 2


def test_a_gap_through_the_atr_stop_fills_at_the_open() -> None:
    result = stops(
        BASE, BASE, SIGNAL, (12, 12, 11, 12), (7, 8, 6, 7), stop_loss_atr={"length": 2, "mult": 1.5}
    )
    assert Decimal(result["fills"][1]["price"]) == Decimal(7)


def test_no_entry_without_the_stop_the_strategy_asked_for() -> None:
    result = stops(BASE, SIGNAL, (12, 12, 11, 12), stop_loss_atr={"length": 50, "mult": 2})
    assert result["fills"] == []
    assert result["orders"][0]["reason"] == "stop_unavailable"


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ({"stop_loss_pct": 5, "stop_loss_atr": {"length": 14, "mult": 2}}, "not both"),
        ({"stop_loss_atr": {"length": 14}}, "exactly length and mult"),
        ({"stop_loss_atr": {"length": 0, "mult": 2}}, "length"),
        ({"stop_loss_atr": {"length": 14, "mult": 0}}, "mult"),
        ({"trailing_stop": {"mode": "pct"}}, "exactly mode and value"),
        ({"trailing_stop": {"mode": "pct", "value": 0}}, "value"),
        ({"trailing_stop": {"mode": "atr", "mult": 2}}, "exactly mode, length and mult"),
        ({"trailing_stop": {"mode": "other", "value": 5}}, "pct or atr"),
        ({"trailing_stop": 5}, "pct or atr"),
    ],
)
def test_bad_protective_stops_are_refused(extra: dict[str, Any], message: str) -> None:
    with pytest.raises(StrategyError, match=message):
        validate_definition({**CROSS, **extra})


def test_python_strategies_do_not_take_these_stops() -> None:
    with pytest.raises(StrategyError, match="unexpected fields"):
        validate_definition(
            {"type": "python", "code": "def on_bar(ctx): pass", "trailing_stop": None}
        )


# --- trailing stop --------------------------------------------------------------------

TRAIL_PCT = {"mode": "pct", "value": 10}


def test_the_trailing_stop_follows_earlier_highs_and_never_falls() -> None:
    result = stops(
        SIGNAL,
        (20, 25, 19, 24),  # entry at 20, stop 18; afterwards the high of 25 lifts it to 22.5
        (24, 24, 23, 23.5),  # a lower high leaves it at 22.5
        (23, 23.5, 22, 22.2),
        trailing_stop=TRAIL_PCT,
    )
    _, exit_ = result["fills"]
    assert exit_["fill_reason"] == "trailing_stop"
    assert Decimal(exit_["price"]) == Decimal("22.5")
    assert result["trades"][0]["exit_reason"] == "trailing_stop"


def test_a_bars_high_cannot_lift_the_stop_inside_that_bar() -> None:
    # If the 30 counted at once, the stop would be 27 and the low of 19 would hit it.
    result = stops(SIGNAL, (20, 20, 20, 20), (20, 30, 19, 25), trailing_stop=TRAIL_PCT)
    assert len(result["fills"]) == 1


def test_an_intrabar_entry_does_not_follow_its_own_bar() -> None:
    # Bought by a stop at 12.1: the bar's high of 40 may have come before the entry.
    result = stops(
        SIGNAL,
        (11.5, 40, 11.5, 12),
        (12, 12, 11, 12),
        entry_order={"type": "stop", "offset_pct": 10},
        trailing_stop=TRAIL_PCT,
    )
    assert len(result["fills"]) == 1  # the stop stayed at 10.89, under the low of 11


def test_the_atr_trailing_stop_uses_only_final_bars_so_far() -> None:
    result = stops(
        BASE,
        BASE,
        SIGNAL,
        (20, 20, 20, 20),  # entry at 20: 20 - 2 x ATR 2 = 16, then the bar widens the ATR
        (19, 19, 15, 15),
        trailing_stop={"mode": "atr", "length": 2, "mult": 2},
    )
    _, exit_ = result["fills"]
    assert exit_["fill_reason"] == "trailing_stop"
    # Bar 3's true range is 9 (close 11 to 20), so ATR (2 + 9) / 2 = 5.5 and 20 - 11 = 9
    # is below the 16 it already had: the stop stays at 16.
    assert Decimal(exit_["price"]) == Decimal(16)


def test_the_tighter_of_fixed_and_trailing_stops_decides() -> None:
    fixed = stops(SIGNAL, (20, 20, 19, 19), stop_loss_pct=1, trailing_stop=TRAIL_PCT)
    assert fixed["fills"][1]["fill_reason"] == "stop_loss"
    assert Decimal(fixed["fills"][1]["price"]) == Decimal("19.8")


def test_walk_forward_can_vary_the_stop_multiples() -> None:
    definition = validate_definition(
        {**CROSS, "exit": None, "trailing_stop": {"mode": "atr", "length": 14, "mult": 2}}
    )
    spec = {"grid": [{"path": "/trailing_stop/mult", "values": [2, 3]}]}
    found = variants(definition, spec)
    assert [item["trailing_stop"]["mult"] for _, item in found] == ["2", "3"]


# --- the paper runner -----------------------------------------------------------------


@pytest.mark.parametrize(
    "extra",
    [
        {"stop_loss_atr": {"length": 14, "mult": 2}},
        {"trailing_stop": TRAIL_PCT},
        {"trailing_stop": {"mode": "atr", "length": 14, "mult": 2}},
    ],
)
def test_the_paper_runner_refuses_stops_it_cannot_run(extra: dict[str, Any]) -> None:
    # Ignoring them would run the strategy without the protection it was tested with.
    with pytest.raises(StrategyError, match="not offered"):
        rule_signals({**CROSS, **extra}, "a", TZ, [], False)


def test_paper_signals_match_the_backtest_bar_by_bar() -> None:
    definition = {
        **CROSS,
        "entry": {
            "all": [
                {"time": {"from": "09:45", "to": "15:30"}},
                {"op": "gt", "left": CLOSE, "right": PRIOR_CLOSE},
                {"op": "gt", "left": PRIOR_CLOSE, "right": {"price": "close", "offset": 2}},
            ]
        },
        "exit": {
            "any": [
                {"op": "lt", "left": CLOSE, "right": PRIOR_CLOSE},
                {"time": {"from": "15:30", "to": "16:00"}},
            ]
        },
    }
    closes = [10, 11, 12, 13, 14, 15, 14, 15, 16, 17, 16, 15, 16, 17, 18, 19, 18, 19, 20, 21.0]
    data = intraday(datetime(2026, 7, 1, 13, 30, tzinfo=timezone.utc), closes, minutes=30)
    # Prices are flat inside each bar, so the next open is that bar's close.
    result = run(definition, [instrument("a", data)], capital="100000")
    filled = [(fill["filled_at_utc"], fill["side"]) for fill in result["fills"]]
    assert {side for _, side in filled} == {"buy", "sell"}  # the rules really fired

    paper = []
    holding = False
    for i in range(len(data) - 1):
        for signal in rule_signals(definition, "a", TZ, data[: i + 1], holding):
            at = datetime.fromtimestamp(data[i + 1].time, timezone.utc)
            paper.append((at.isoformat().replace("+00:00", "Z"), signal["side"]))
            holding = signal["side"] == "buy"
    assert paper == filled
