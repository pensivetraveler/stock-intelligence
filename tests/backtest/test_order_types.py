"""Limit and stop orders with an expiry in bars (ADR 0031)."""

from __future__ import annotations

from typing import Any

import pytest

from services.backtest.strategy import StrategyError, fingerprint, validate_definition
from tests.backtest.test_engine import CROSS, bars, instrument, run

# Signals on bar 0's close (11): entry rule close > 10.
SIGNAL = (11, 11, 11, 11)


def entry(order: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {**CROSS, "exit": None, "entry_order": order, **extra}


def test_buy_limit_needs_a_trade_through_and_takes_no_slippage() -> None:
    limit = {"type": "limit", "offset_pct": -10, "expire_bars": 3}  # 9.9
    # Bar 1 touches 9.9 only (no fill), bar 2 trades through it.
    data = bars([SIGNAL, (10.5, 10.6, 9.9, 10), (10.2, 10.3, 9.5, 9.8)])
    result = run(entry(limit), [instrument("a", data)], slippage_bps="100")
    (fill,) = result["fills"]
    assert fill["filled_at_utc"].startswith("2025-10-11")  # bar 2
    assert fill["price"] == "9.9"
    assert fill["fill_reason"] == "limit_order"
    assert fill["fill_model_version"] == "next-open-slippage-v1+limit-stop-v1"
    (order,) = result["orders"]
    assert (order["order_type"], order["price"], order["expire_bars"]) == ("limit", "9.9", 3)
    assert result["fill_model_version"] == "next-open-slippage-v1+limit-stop-v1"


def test_a_gap_below_a_buy_limit_fills_at_the_better_open() -> None:
    data = bars([SIGNAL, (9, 9.5, 8.8, 9.2)])
    (fill,) = run(entry({"type": "limit", "offset_pct": -10}), [instrument("a", data)])["fills"]
    assert fill["price"] == "9"


def test_unfilled_orders_expire_after_their_bars() -> None:
    data = bars([SIGNAL, (11, 11, 10.5, 11), (11, 11, 10.5, 11), (11, 11, 10.5, 11)])
    result = run(
        entry({"type": "limit", "offset_pct": -10, "expire_bars": 2}), [instrument("a", data)]
    )
    assert result["fills"] == []
    assert result["orders"][0]["status"] == "expired"
    # The rules strategy may signal again once the order is gone.
    assert len(result["orders"]) == 2


def test_buy_stop_fills_at_the_stop_or_a_gapped_open_with_slippage() -> None:
    stop = {"type": "stop", "offset_pct": 10, "expire_bars": 5}  # 12.1
    inside = bars([SIGNAL, (11.5, 12.5, 11.4, 12.4)])
    (fill,) = run(entry(stop), [instrument("a", inside)], slippage_bps="100")["fills"]
    assert (fill["price"], fill["fill_reason"]) == ("12.221", "stop_order")
    gapped = bars([SIGNAL, (13, 13.5, 12.8, 13.2)])
    (fill,) = run(entry(stop), [instrument("a", gapped)])["fills"]
    assert fill["price"] == "13"


def test_an_intrabar_entry_flags_a_same_bar_stop_loss() -> None:
    stop = {"type": "stop", "offset_pct": 10}  # buy stop 12.1, stop loss 5% below fill
    data = bars([SIGNAL, (11.5, 12.5, 11, 11.2)])
    result = run(entry(stop, stop_loss_pct=5), [instrument("a", data)])
    _, exit_ = result["fills"]
    assert exit_["fill_reason"] == "stop_loss"
    assert "same_bar_as_entry" in exit_["notes"]


def test_exit_limit_sells_above_the_signal_close() -> None:
    definition = {
        **CROSS,
        "exit_order": {"type": "limit", "offset_pct": 5, "expire_bars": 2},
    }
    # Enter at bar 1's open (11); exit signal on bar 1's close (9.5): sell limit 9.975.
    data = bars([SIGNAL, (11, 11, 9.5, 9.5), (9.6, 10.2, 9.5, 10)])
    result = run(definition, [instrument("a", data)])
    _, exit_ = result["fills"]
    assert (exit_["side"], exit_["price"], exit_["fill_reason"]) == ("sell", "9.975", "limit_order")


def test_python_intents_take_absolute_prices_and_bad_terms_are_rejected() -> None:
    code = (
        "def on_bar(ctx):\n"
        "    index = len(ctx.history) - 1\n"
        "    if index == 0:\n"
        "        return [\n"
        "            {'side': 'buy', 'quantity': 1, 'order_type': 'limit', 'limit_price': 10.5},\n"
        "        ]\n"
        "    if index == 1:\n"
        "        return [{'side': 'sell', 'order_type': 'limit'}]\n"
    )
    data = bars([SIGNAL, (11, 11, 10, 10.4), (11, 11, 11, 11)])
    result = run({"type": "python", "code": code}, [instrument("a", data)])
    (fill,) = result["fills"]
    assert fill["price"] == "10.5"
    assert result["orders"][1]["reason"] == "invalid_order_price"


def test_rules_order_validation_and_unchanged_fingerprints() -> None:
    # Strategies saved before order types keep their definition (and fingerprint).
    before = validate_definition(CROSS)
    assert "entry_order" not in before and fingerprint(before) == fingerprint(
        validate_definition(CROSS)
    )
    for bad in (
        {"type": "limit"},
        {"type": "limit", "offset_pct": -5, "expire_bars": 0},
        {"type": "market", "offset_pct": 1},
        {"type": "iceberg", "offset_pct": 1},
    ):
        with pytest.raises(StrategyError):
            validate_definition({**CROSS, "entry_order": bad})
