"""P&L for the manual (actual) and hypothetical ledgers, kept strictly apart."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any, cast
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
ZERO = Decimal(0)


class ExchangeTimeError(ValueError):
    """A US exchange-local time that does not exist or is ambiguous (DST)."""


def exchange_local_to_utc(
    session_date: date, local_time: time, zone: ZoneInfo = NEW_YORK
) -> datetime:
    """Convert an exchange wall-clock time on a date to UTC (New York by default).

    Rejects the skipped hour when clocks spring forward and the repeated hour when
    they fall back, instead of silently picking one.
    """
    naive = datetime.combine(session_date, local_time)
    first = naive.replace(tzinfo=zone, fold=0)
    as_utc = first.astimezone(timezone.utc)
    # Check the gap first: inside it, fold=0 and fold=1 also disagree on the offset.
    # (Korea has no daylight saving time, so neither case arises for KST.)
    if as_utc.astimezone(zone).replace(tzinfo=None) != naive:
        raise ExchangeTimeError(f"{naive.isoformat()} ET does not exist during the DST change")
    if first.utcoffset() != naive.replace(tzinfo=zone, fold=1).utcoffset():
        raise ExchangeTimeError(f"{naive.isoformat()} ET is ambiguous during the DST change")
    return as_utc


def _text(value: Decimal) -> str:
    # Any zero, including Decimal("-0.00"), is written as "0".
    return "0" if value == 0 else format(value, "f")


@dataclass
class _Lot:
    quantity: Decimal  # positive long, negative short
    price: Decimal


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _tiebreak(item: dict[str, Any]) -> tuple[str, int, str]:
    """Order fills at the same time by when they were recorded or their statement row."""
    source = item.get("source")
    if isinstance(source, dict):
        return (cast(str, source["batch_id"]), cast(int, source["row_number"]), "")
    return (cast(str, item.get("asserted_at_utc", "")), 0, cast(str, item["execution_id"]))


def manual_positions(executions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """FIFO positions and realized P&L per instrument for one actual-fill ledger.

    Used separately for manual and for imported executions; ledgers are never combined.

    Executions with an unknown time cannot be ordered against the others, so they
    are listed in `unordered_execution_ids` and left out of the calculation rather
    than placed at a guessed time. Executions whose broker reported no fees (Kiwoom)
    count without a fee and are listed in `fees_unreported_execution_ids`.
    """
    by_instrument: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for execution in executions:
        by_instrument[cast(str, execution["instrument_id"])].append(execution)
    positions: list[dict[str, Any]] = []
    for instrument_id in sorted(by_instrument):
        items = by_instrument[instrument_id]
        unordered = sorted(
            cast(str, item["execution_id"]) for item in items if item["executed_at_utc"] is None
        )
        timed = sorted(
            (item for item in items if item["executed_at_utc"] is not None),
            key=lambda item: (_parse(cast(str, item["executed_at_utc"])), _tiebreak(item)),
        )
        lots: list[_Lot] = []
        realized = ZERO
        fees = ZERO
        fees_unreported: list[str] = []
        for item in timed:
            price = Decimal(cast(str, item["price"]))
            remaining = Decimal(cast(str, item["quantity"])) * (1 if item["side"] == "buy" else -1)
            if item["fees"] is None:
                fees_unreported.append(cast(str, item["execution_id"]))
            fee = ZERO if item["fees"] is None else Decimal(cast(str, item["fees"]))
            fees += fee
            realized -= fee
            while remaining != 0 and lots and (lots[0].quantity > 0) != (remaining > 0):
                lot = lots[0]
                matched = min(abs(remaining), abs(lot.quantity))
                direction = 1 if lot.quantity > 0 else -1
                realized += matched * (price - lot.price) * direction
                lot.quantity -= matched * direction
                remaining += matched * direction
                if lot.quantity == 0:
                    lots.pop(0)
            if remaining != 0:
                lots.append(_Lot(remaining, price))
        open_quantity = sum((lot.quantity for lot in lots), ZERO)
        open_size = sum((abs(lot.quantity) for lot in lots), ZERO)
        average_cost = (
            sum((abs(lot.quantity) * lot.price for lot in lots), ZERO) / open_size
            if open_size
            else None
        )
        position: dict[str, Any] = {
            "instrument_id": instrument_id,
            "open_quantity": _text(open_quantity),
            "average_cost": None if average_cost is None else _text(average_cost),
            "realized_pnl": _text(realized),
            "fees": _text(fees),
            "unordered_execution_ids": unordered,
        }
        if fees_unreported:
            position["fees_unreported_execution_ids"] = sorted(fees_unreported)
        positions.append(position)
    return positions


def account_positions(executions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Broker-ledger positions per broker account and instrument.

    Each account is its own FIFO book: a sell in one account never closes a buy made in
    another (for example a Kiwoom sell against a Toss buy). Positions carry `account_ref`.
    """
    by_account: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for execution in executions:
        by_account[cast(str, execution["broker_account_ref"])].append(execution)
    return [
        {"account_ref": account, **position}
        for account in sorted(by_account)
        for position in manual_positions(by_account[account])
    ]


def scenario_pnl(scenario: dict[str, Any]) -> dict[str, str]:
    """Hypothetical round-trip P&L: slippage is adverse at both entry and exit."""
    quantity = Decimal(cast(str, scenario["quantity"]))
    entry = Decimal(cast(str, scenario["entry_price"]))
    exit_price = Decimal(cast(str, scenario["exit_price"]))
    slippage = Decimal(cast(str, scenario["slippage_assumption"]))
    fees = Decimal(cast(str, scenario["fee_assumption"]))
    direction = 1 if scenario["direction"] == "long" else -1
    gross = (exit_price - entry) * quantity * direction
    costs = slippage * 2 * quantity + fees
    return {"gross_pnl": _text(gross), "costs": _text(costs), "net_pnl": _text(gross - costs)}
