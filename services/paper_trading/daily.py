"""A paper account's day from its fills: loss for the daily limit, and the day's summary.

ADR 0057 stage 7 (2026-11-25): the daily loss limit counts realized plus unrealized loss in
dollars, and after the close the user reads a summary per instrument (entries, exits,
profit and loss, why each exit happened). Both are read from the paper ledger only; no
broker call. Positions use the average cost of their buys. Paper fills carry no fees
(Kiwoom ust21510 reports none), so none are taken off.

A fill belongs to the day of its reported fill time, or of the time it was synced when the
broker gave no time (`time_unresolved`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from services.paper_trading.store import PaperOrderStore

ZERO = Decimal(0)


def fill_time(fill: Mapping[str, Any]) -> datetime:
    text = fill.get("executed_at_utc") or fill["synced_at_utc"]
    return datetime.fromisoformat(str(text).replace("Z", "+00:00"))


@dataclass
class Book:
    """One instrument's position at average cost, and what was realized on one day."""

    instrument_id: str
    symbol: str
    currency: str
    quantity: Decimal = ZERO
    cost: Decimal = ZERO  # total cost of the shares held
    realized_today: Decimal = ZERO
    bought_today: Decimal = ZERO  # shares
    bought_cost_today: Decimal = ZERO
    sold_today: Decimal = ZERO  # shares
    sold_value_today: Decimal = ZERO
    sells_today: list[dict[str, Any]] = field(default_factory=list)

    @property
    def average_cost(self) -> Decimal | None:
        return None if self.quantity <= 0 else self.cost / self.quantity


def books(fills: Iterable[Mapping[str, Any]], day: date, time_zone: str) -> dict[str, Book]:
    """Replay one account's fills in time order up to the end of `day` (local)."""
    zone = ZoneInfo(time_zone)
    result: dict[str, Book] = {}
    for fill in sorted(fills, key=lambda item: (fill_time(item), item["execution_id"])):
        local_day = fill_time(fill).astimezone(zone).date()
        if local_day > day:
            continue
        book = result.setdefault(
            fill["instrument_id"],
            Book(fill["instrument_id"], fill["symbol"], fill.get("currency", "USD")),
        )
        quantity = Decimal(fill["quantity"])
        price = Decimal(fill["price"])
        today = local_day == day
        if fill["side"] == "buy":
            book.quantity += quantity
            book.cost += quantity * price
            if today:
                book.bought_today += quantity
                book.bought_cost_today += quantity * price
            continue
        sold = min(quantity, book.quantity)
        average = book.average_cost or price
        realized = (price - average) * sold
        book.cost -= average * sold
        book.quantity -= sold
        if book.quantity <= 0:
            book.quantity, book.cost = ZERO, ZERO
        if today:
            book.realized_today += realized
            book.sold_today += quantity
            book.sold_value_today += quantity * price
            book.sells_today.append(
                {
                    "intent_id": fill.get("intent_id"),
                    "quantity": quantity,
                    "price": price,
                    "realized": realized,
                    "at_utc": fill_time(fill),
                }
            )
    return result


def daily_loss(
    fills: Iterable[Mapping[str, Any]],
    day: date,
    time_zone: str,
    prices: Mapping[str, Decimal],
    currency: str = "USD",
) -> Decimal | None:
    """Today's loss in `currency` as a positive number (0 when in profit).

    Realized profit and loss of today's sells plus the unrealized profit and loss of every
    share still held, against average cost, at `prices` (the latest final bar close).
    None when a held instrument has no price: the limit then cannot be judged.
    """
    total = ZERO
    for book in books(fills, day, time_zone).values():
        if book.currency != currency:
            continue
        total += book.realized_today
        if book.quantity > 0:
            price = prices.get(book.instrument_id)
            if price is None:
                return None
            total += (price - book.cost / book.quantity) * book.quantity
    return max(ZERO, -total)


def _text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.quantize(Decimal("0.0001")).normalize(), "f")


def day_summary(
    fills: Iterable[Mapping[str, Any]],
    intents: Mapping[str, Mapping[str, Any]],
    day: date,
    time_zone: str,
) -> dict[str, Any]:
    """The day's summary per instrument traded that day (ADR 0057 stage 7 "결과 보기").

    `intents` maps intent IDs to intents, for the exit reason of each sell. The strategy
    frame (one of the eight stage-6 frames) is not in the runner yet: `frame` is null until
    stage-6 scripts record it.
    """
    rows = []
    realized_total = ZERO
    for book in sorted(books(fills, day, time_zone).values(), key=lambda b: b.symbol):
        if book.bought_today <= 0 and book.sold_today <= 0:
            continue
        exits = []
        for sell in book.sells_today:
            intent = intents.get(str(sell["intent_id"])) or {}
            summary = str(intent.get("signal", {}).get("summary", ""))
            reason = (
                summary.removeprefix("protective exit: ")
                if summary.startswith("protective exit: ")
                else "signal"
            )
            exits.append(
                {
                    "quantity": _text(sell["quantity"]),
                    "price": _text(sell["price"]),
                    "realized": _text(sell["realized"]),
                    "reason": reason,
                    "at_utc": sell["at_utc"].isoformat().replace("+00:00", "Z"),
                }
            )
        realized_total += book.realized_today
        rows.append(
            {
                "instrument_id": book.instrument_id,
                "symbol": book.symbol,
                "currency": book.currency,
                "frame": None,
                "bought_quantity": _text(book.bought_today),
                "average_buy_price": _text(
                    book.bought_cost_today / book.bought_today if book.bought_today > 0 else None
                ),
                "sold_quantity": _text(book.sold_today),
                "average_sell_price": _text(
                    book.sold_value_today / book.sold_today if book.sold_today > 0 else None
                ),
                "realized": _text(book.realized_today),
                "held_quantity": _text(book.quantity),
                "held_average_cost": _text(book.average_cost),
                "exits": exits,
            }
        )
    return {
        "kind": "paper_day_summary",
        "day": day.isoformat(),
        "time_zone": time_zone,
        "instruments": rows,
        "realized_total": _text(realized_total),
        "note": "Average cost, no fees (paper fills report none). Unrealized P&L is not "
        "included: it needs a price at the close.",
    }


def store_day_summary(store: PaperOrderStore, day: date, time_zone: str) -> dict[str, Any]:
    """`day_summary` of the account of the runner's current approval (none: empty)."""
    approval_id = store.status()["approval_id"]
    approval = store.approval(approval_id) if approval_id else None
    fills = [] if approval is None else store.account_executions(approval["broker_account_ref"])
    intents = {
        str(fill["intent_id"]): store.intent(str(fill["intent_id"])) or {}
        for fill in fills
        if fill.get("intent_id")
    }
    return day_summary(fills, intents, day, time_zone)
