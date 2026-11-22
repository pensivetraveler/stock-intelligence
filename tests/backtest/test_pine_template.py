"""Rules strategy -> Pine v6 template (ADR 0056 ⑤)."""

from __future__ import annotations

from typing import Any

from services.backtest.pine_template import to_pine
from services.backtest.strategy import validate_definition
from tests.backtest.test_backtest_api import save, setup  # noqa: F401 - fixture

SEYKOTA = {
    "type": "rules",
    "entry": {"all": [{"op": "gt", "left": {"price": "close"},
                       "right": {"indicator": "dc", "params": {"length": 20}, "output": "upper",
                                 "offset": 1}}]},
    "exit": {"any": [{"op": "lt", "left": {"price": "close"},
                      "right": {"indicator": "dc", "params": {"length": 10}, "output": "lower",
                                "offset": 1}}]},
    "sizing": {"mode": "percent_equity", "value": "10"},
}  # fmt: skip


def test_a_breakout_strategy_becomes_pine() -> None:
    result = to_pine(validate_definition(SEYKOTA), "Seykota")
    source = result["source"]
    assert result["unsupported"] == []
    assert source.startswith("//@version=6\n")
    assert 'strategy("Seykota", overlay=true, pyramiding=1' in source
    assert "default_qty_type=strategy.percent_of_equity, default_qty_value=10" in source
    assert "s1 = ta.highest(high, 20)" in source and "s2 = ta.lowest(low, 10)" in source
    assert "entrySignal = ((close > s1[1]))" in source
    assert "exitSignal = ((close < s2[1]))" in source
    assert 'strategy.entry("L", strategy.long)' in source
    assert 'strategy.close("L")' in source
    assert "strategy.exit" not in source  # no protective exit when none is set


def test_protective_exits_and_indicators_follow_the_engine() -> None:
    definition = validate_definition(
        {
            "type": "rules",
            "entry": {"all": [
                {"op": "cross_above", "left": {"indicator": "macd", "output": "macd"},
                 "right": {"indicator": "macd", "output": "signal"}},
                {"op": "gt", "left": {"indicator": "dmi", "output": "adx"}, "right": {"value": 25}},
                {"op": "lt", "left": {"indicator": "bb", "output": "lower"},
                 "right": {"price": "close"}},
                {"time": {"from": "09:30", "to": "11:00"}},
            ]},
            "exit": None,
            "stop_loss_atr": {"length": 14, "mult": "2"},
            "take_profit_pct": "10",
            "trailing_stop": {"mode": "pct", "value": "5"},
            "sizing": {"mode": "fixed_shares", "value": "100"},
        }
    )  # fmt: skip
    source = to_pine(definition, "x")["source"]
    assert "ta.crossover(s1, s2)" in source
    assert "ta.ema(close, 12) - ta.ema(close, 26)" in source
    assert "[dmiPlus14_14, dmiMinus14_14, adx14_14] = ta.dmi(14, 14)" in source
    assert "ta.stdev(close, 20)" in source  # population, like the engine
    assert 'time(timeframe.period, "0930-1100", syminfo.timezone)' in source
    assert "default_qty_type=strategy.fixed, default_qty_value=100" in source
    assert "stopAtr = ta.atr(14)" in source and "entryAtr := stopAtr[1]" in source
    assert "trailHigh * (1 - 5 / 100)" in source
    assert (
        'strategy.exit("P", "L", stop=math.max(strategy.position_avg_price - 2 * entryAtr, '
        "trailStop), limit=strategy.position_avg_price * (1 + 10 / 100))"
    ) in source
    # Every ta.* call is at the top level, never inside an if block.
    for line in source.splitlines():
        if line.startswith(" ") and "ta." in line:
            raise AssertionError(f"ta.* inside a block: {line}")


def test_what_has_no_pine_form_is_false_and_listed() -> None:
    definition = validate_definition(
        {**SEYKOTA, "exit": {"op": "lt", "left": {"indicator": "supertrend", "output": "up"},
                             "right": {"price": "close"}}}
    )  # fmt: skip
    result = to_pine(definition, "x")
    assert result["unsupported"] == ["/exit/left: indicator supertrend has no Pine form here"]
    assert "exitSignal = false" in result["source"]
    assert "NOT CONVERTED" in result["source"]


def test_a_limit_entry_expires_like_the_engine() -> None:
    definition = validate_definition(
        {**SEYKOTA, "entry_order": {"type": "limit", "offset_pct": "-1", "expire_bars": 2}}
    )
    source = to_pine(definition, "x")["source"]
    assert 'strategy.entry("L", strategy.long, limit=close * (1 + -1 / 100))' in source
    assert "bar_index - entryAt >= 2" in source


def test_the_api_returns_the_template(setup: Any) -> None:  # noqa: F811
    client, _runner, _journal = setup
    saved = save(client, SEYKOTA)
    found = client.get(f"/v1/backtests/strategies/{saved['strategy_id']}/pine").json()
    assert found["kind"] == "backtest_strategy_pine"
    assert found["version"] == saved["version"] and found["unsupported"] == []
    assert "ta.highest(high, 20)" in found["source"]
    assert client.get("/v1/backtests/strategies/nope/pine").status_code == 404
