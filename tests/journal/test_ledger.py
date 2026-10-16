from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any

import pytest

from services.journal.ledger import (
    ExchangeTimeError,
    exchange_local_to_utc,
    manual_positions,
    scenario_pnl,
)


def execution(
    execution_id: str, side: str, quantity: str, price: str, at: str | None, fees: str = "0"
) -> dict[str, Any]:
    return {
        "execution_id": execution_id,
        "instrument_id": "us:synth:xnas:2026",
        "side": side,
        "quantity": quantity,
        "price": price,
        "fees": fees,
        "executed_at_utc": at,
        "asserted_at_utc": "2026-10-12T00:00:00Z",
    }


def test_fifo_realized_pnl_with_partial_close_and_fees() -> None:
    [position] = manual_positions(
        [
            execution("b2", "buy", "10", "51.00", "2026-10-09T15:00:00Z", fees="1"),
            execution("b1", "buy", "10", "50.00", "2026-10-09T14:00:00Z", fees="1"),
            execution("s1", "sell", "15", "52.00", "2026-10-09T16:00:00Z", fees="1"),
        ]
    )

    # FIFO: 10 @ 50 -> +20, then 5 @ 51 -> +5, minus 3 in fees.
    assert position["realized_pnl"] == "22.00"
    assert position["open_quantity"] == "5"
    assert position["average_cost"] == "51.00"
    assert position["fees"] == "3"


def test_short_position_and_flip_through_zero() -> None:
    [position] = manual_positions(
        [
            execution("s1", "sell", "5", "52", "2026-10-09T14:00:00Z"),
            execution("b1", "buy", "8", "50", "2026-10-09T15:00:00Z"),
        ]
    )

    assert position["realized_pnl"] == "10"
    assert position["open_quantity"] == "3"
    assert position["average_cost"] == "50"


def test_unknown_time_executions_are_left_out_not_guessed() -> None:
    [position] = manual_positions(
        [
            execution("b1", "buy", "10", "50", "2026-10-09T14:00:00Z"),
            execution("x1", "sell", "10", "60", None),
        ]
    )

    assert position["unordered_execution_ids"] == ["x1"]
    assert position["open_quantity"] == "10"
    assert position["realized_pnl"] == "0"


@pytest.mark.parametrize(
    ("direction", "net"),
    [("long", "47.00"), ("short", "-53.00")],
)
def test_scenario_pnl_applies_slippage_at_both_ends(direction: str, net: str) -> None:
    result = scenario_pnl(
        {
            "direction": direction,
            "quantity": "10",
            "entry_price": "50.00",
            "exit_price": "55.00",
            "slippage_assumption": "0.10",
            "fee_assumption": "1.00",
        }
    )

    assert result["costs"] == "3.00"
    assert result["net_pnl"] == net


def test_exchange_local_time_converts_across_dst() -> None:
    assert exchange_local_to_utc(date(2026, 10, 9), time(9, 30)) == datetime(
        2026, 10, 9, 13, 30, tzinfo=timezone.utc
    )
    assert exchange_local_to_utc(date(2026, 12, 1), time(9, 30)) == datetime(
        2026, 12, 1, 14, 30, tzinfo=timezone.utc
    )


@pytest.mark.parametrize(
    ("day", "clock", "message"),
    [
        (date(2026, 3, 8), time(2, 30), "does not exist"),
        (date(2026, 11, 1), time(1, 30), "ambiguous"),
    ],
)
def test_exchange_local_time_rejects_dst_gaps_and_overlaps(
    day: date, clock: time, message: str
) -> None:
    with pytest.raises(ExchangeTimeError, match=message):
        exchange_local_to_utc(day, clock)
