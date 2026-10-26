"""Group journal executions into trades (ADR 0042).

Fills are grouped per ledger, per account ("book"), per instrument and per assigned
strategy. Each group is one position stream, matched long-only FIFO like the review Agent:
a sell with no open lot is "unmatched" (usually history from before the data starts), not
a short. The strategy's rule then decides what one trade is:

- `flat`: from a flat position back to flat (scale-ins and partial exits included);
- `day`: every fill of one exchange session day;
- `fifo`: each buy, with the sells FIFO-matched to it;
- `manual`: groups the user made; a fill in no group is a trade of its own.

The ledgers (manual, imported, broker) are never mixed: they may describe the same real
trades. Amounts are Decimal and written as decimal text.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

Rule = Literal["flat", "day", "fifo", "manual"]
RULES: tuple[Rule, ...] = ("flat", "day", "fifo", "manual")
DEFAULT_RULE: Rule = "flat"
LEDGERS = ("manual", "imported", "broker")
ZERO = Decimal(0)


@dataclass(frozen=True)
class Fill:
    ledger: str
    execution_id: str
    book: str  # "broker:<account_ref>", "imported:<label>" or "manual"
    instrument_id: str
    side: str
    quantity: Decimal
    price: Decimal
    fees: Decimal | None  # None: the source did not report fees
    at: datetime | None  # the exact time when known
    day: date  # exchange session date
    tiebreak: tuple[str, ...]
    ref: str = field(init=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "ref", f"{self.ledger}:{self.execution_id}")

    def sort_key(self) -> tuple[Any, ...]:
        # Fills known only by their day go after the day's timed fills, buys first: the
        # order inside such a day is assumed, and the trade says so (`time_uncertain`).
        return (
            self.day,
            self.at is None,
            self.at or datetime.min.replace(tzinfo=timezone.utc),
            self.at is None and self.side != "buy",
            self.tiebreak,
        )


@dataclass
class Allocation:
    fill: Fill
    quantity: Decimal
    role: Literal["open", "close", "unmatched"]
    gross_pnl: Decimal = ZERO  # for closes: (exit - entry) * quantity
    entry_cost: Decimal = ZERO  # for closes: the matched lot's cost
    lot: str | None = None  # for closes: the ref of the buy it closes


@dataclass
class Trade:
    trade_id: str
    rule: Rule
    ledger: str
    book: str
    instrument_id: str
    strategy_id: str | None
    allocations: list[Allocation] = field(default_factory=list)
    open_quantity: Decimal = ZERO


NEW_YORK = ZoneInfo("America/New_York")
SEOUL = ZoneInfo("Asia/Seoul")


def _zone(instrument_id: str) -> ZoneInfo:
    # The exchange's zone, without loading its calendar (slow, and not needed here).
    return SEOUL if instrument_id.startswith("kr:") else NEW_YORK


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _book(ledger: str, document: Mapping[str, Any]) -> str:
    if ledger == "broker":
        return f"broker:{document['broker_account_ref']}"
    if ledger == "imported":
        return f"imported:{document['broker_label']}"
    return "manual"


def fill_of(ledger: str, document: Mapping[str, Any]) -> Fill | None:
    """A journal record as a fill, or None when not even its day is known."""
    instrument_id = str(document["instrument_id"])
    zone = _zone(instrument_id)
    executed = document.get("executed_at_utc")
    at: datetime | None = None
    if executed is not None and document.get("time_precision", "exact") in ("exact", "minute"):
        at = _parse(str(executed))
    if executed is not None:
        day = _parse(str(executed)).astimezone(zone).date()
    elif document.get("trade_date"):  # broker settlement lines: only the local day
        day = date.fromisoformat(str(document["trade_date"]))
    elif document.get("selected_interval_start_utc"):  # a manual record: the clicked candle
        day = _parse(str(document["selected_interval_start_utc"])).astimezone(zone).date()
    else:
        return None
    reported = document.get("fees") not in (None, "")
    source = document.get("source")
    if isinstance(source, dict):
        tiebreak: tuple[str, ...] = (str(source["batch_id"]), f"{int(source['row_number']):09d}")
    else:
        tiebreak = (
            str(document.get("ordered_at_utc") or document.get("asserted_at_utc") or ""),
            str(document["execution_id"]),
        )
    return Fill(
        ledger,
        str(document["execution_id"]),
        _book(ledger, document),
        instrument_id,
        str(document["side"]),
        Decimal(str(document["quantity"])),
        Decimal(str(document["price"])),
        Decimal(str(document["fees"])) if reported else None,
        at,
        day,
        tiebreak,
    )


def _match(fills: list[Fill]) -> tuple[list[list[Allocation]], dict[str, Decimal]]:
    """Long-only FIFO over one stream: each fill's pieces, and every buy's open remainder."""
    lots: deque[list[Any]] = deque()  # [buy ref, remaining quantity, price]
    remaining: dict[str, Decimal] = {}
    pieces: list[list[Allocation]] = []
    for fill in fills:
        if fill.side == "buy":
            lots.append([fill.ref, fill.quantity, fill.price])
            remaining[fill.ref] = fill.quantity
            pieces.append([Allocation(fill, fill.quantity, "open")])
            continue
        own: list[Allocation] = []
        left = fill.quantity
        while left > 0 and lots:
            ref, quantity, price = lots[0]
            take = min(left, quantity)
            own.append(
                Allocation(fill, take, "close", take * (fill.price - price), take * price, ref)
            )
            lots[0][1] -= take
            remaining[ref] -= take
            left -= take
            if lots[0][1] == 0:
                lots.popleft()
        if left > 0:
            own.append(Allocation(fill, left, "unmatched"))
        pieces.append(own)
    return pieces, remaining


def _stream_trades(
    fills: list[Fill],
    rule: Rule,
    strategy_id: str | None,
    groups: Mapping[str, str],
) -> list[Trade]:
    fills = sorted(fills, key=Fill.sort_key)
    pieces, remaining = _match(fills)
    first = fills[0]
    trades: dict[str, Trade] = {}
    order: list[str] = []

    def trade(key: str) -> Trade:
        if key not in trades:
            trades[key] = Trade(
                f"{rule}:{key}", rule, first.ledger, first.book, first.instrument_id, strategy_id
            )
            order.append(key)
        return trades[key]

    if rule == "flat":
        current: str | None = None
        position = ZERO
        for fill, own in zip(fills, pieces, strict=True):
            for piece in own:
                if piece.role == "unmatched":
                    trade(fill.ref).allocations.append(piece)
                    continue
                if current is None:
                    current = fill.ref
                trade(current).allocations.append(piece)
                position += piece.quantity if piece.role == "open" else -piece.quantity
            if position == 0:
                current = None
        if current is not None:
            trades[current].open_quantity = position
    elif rule == "day":
        days: dict[date, str] = {}
        for fill, own in zip(fills, pieces, strict=True):
            key = days.setdefault(fill.day, fill.ref)
            trade(key).allocations.extend(own)
    elif rule == "fifo":
        for fill, own in zip(fills, pieces, strict=True):
            for piece in own:
                key = piece.lot or fill.ref
                trade(key).allocations.append(piece)
    else:
        for fill, own in zip(fills, pieces, strict=True):
            group = groups.get(fill.ref)
            trade(f"group-{group}" if group else fill.ref).allocations.extend(own)
    if rule != "flat":
        for item in trades.values():
            item.open_quantity = sum(
                (remaining[p.fill.ref] for p in item.allocations if p.role == "open"), ZERO
            )
    return [trades[key] for key in order]


def fills_of(ledger: str, documents: Iterable[Mapping[str, Any]]) -> tuple[list[Fill], int]:
    """A ledger's records as fills, and how many had no date at all (left out)."""
    fills: list[Fill] = []
    undated = 0
    for document in documents:
        fill = fill_of(ledger, document)
        if fill is None:
            undated += 1
        else:
            fills.append(fill)
    return fills, undated


def build_trades(
    fills: Iterable[Fill],
    assignments: Mapping[str, str],
    rules: Mapping[str, Rule],
    groups: Mapping[str, str],
) -> list[Trade]:
    """Every trade of the fills.

    `assignments` maps an execution ref to a strategy id, `rules` a strategy id to its
    rule (strategies without one and unassigned fills use `flat`), `groups` an execution
    ref to a manual group id.
    """
    streams: dict[tuple[str, str, str, str | None], list[Fill]] = defaultdict(list)
    for fill in fills:
        strategy = assignments.get(fill.ref)
        streams[(fill.ledger, fill.book, fill.instrument_id, strategy)].append(fill)
    trades: list[Trade] = []
    for (_ledger, _book, _instrument, strategy), stream in streams.items():
        rule = rules.get(strategy, DEFAULT_RULE) if strategy else DEFAULT_RULE
        trades.extend(_stream_trades(stream, rule, strategy, groups))
    return trades


def _text(value: Decimal) -> str:
    return "0" if value == 0 else format(value.normalize(), "f")


def _utc(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _average(total: Decimal, quantity: Decimal) -> str | None:
    if quantity == 0:
        return None
    return _text((total / quantity).quantize(Decimal("0.000001")))


def status_of(item: Trade) -> str:
    """`incomplete` when a sell had no open lot, `open` while shares remain, else `closed`."""
    if any(p.role == "unmatched" for p in item.allocations):
        return "incomplete"
    return "open" if item.open_quantity > 0 else "closed"


def last_key(item: Trade) -> tuple[Any, ...]:
    """Sort key for newest-first lists: the trade's last fill."""
    return max(p.fill.sort_key() for p in item.allocations)


def trade_document(item: Trade) -> dict[str, Any]:
    """A trade as a `review_trade` contract document (without its annotation)."""
    opens = [p for p in item.allocations if p.role == "open"]
    closes = [p for p in item.allocations if p.role == "close"]
    unmatched = sum((p.quantity for p in item.allocations if p.role == "unmatched"), ZERO)
    entry_quantity = sum((p.quantity for p in opens), ZERO)
    exit_quantity = sum((p.quantity for p in closes), ZERO)
    gross = sum((p.gross_pnl for p in closes), ZERO)
    entry_value = sum((p.quantity * p.fill.price for p in opens), ZERO)
    exit_value = sum((p.quantity * p.fill.price for p in closes), ZERO)
    fees = ZERO
    unreported: set[str] = set()
    # A fill split over two trades pays its fees in proportion to the quantity.
    for piece in item.allocations:
        if piece.fill.fees is None:
            unreported.add(piece.fill.ref)
        else:
            fees += piece.fill.fees * piece.quantity / piece.fill.quantity
    fees = fees.quantize(Decimal("0.0001"))
    fills = sorted({p.fill for p in item.allocations}, key=Fill.sort_key)
    days = [f.day for f in fills]
    status = status_of(item)
    closed_on = days[-1] if status == "closed" else None
    shared_days = {d for d in days if days.count(d) > 1}
    time_uncertain = any(f.at is None and f.day in shared_days for f in fills)
    if item.ledger == "broker":
        account_ref: str | None = item.book.removeprefix("broker:")
    elif item.ledger == "imported":
        account_ref = item.book.removeprefix("imported:")
    else:
        account_ref = None
    return {
        "trade_id": item.trade_id,
        "rule": item.rule,
        "ledger": item.ledger,
        "account_ref": account_ref,
        "instrument_id": item.instrument_id,
        "strategy_id": item.strategy_id,
        "status": status,
        "opened_on": days[0].isoformat(),
        "opened_at_utc": _utc(fills[0].at),
        "closed_on": None if closed_on is None else closed_on.isoformat(),
        "closed_at_utc": _utc(fills[-1].at) if status == "closed" else None,
        "holding_days": None if closed_on is None else (closed_on - days[0]).days,
        "entry_quantity": _text(entry_quantity),
        "average_entry_price": _average(entry_value, entry_quantity),
        "exit_quantity": _text(exit_quantity),
        "average_exit_price": _average(exit_value, exit_quantity),
        "matched_entry_cost": _text(sum((p.entry_cost for p in closes), ZERO)),
        "open_quantity": _text(item.open_quantity),
        "unmatched_quantity": _text(unmatched),
        "gross_pnl": _text(gross),
        "fees": _text(fees),
        "net_pnl": _text(gross - fees),
        "fees_unreported_count": len(unreported),
        "time_uncertain": time_uncertain,
        "executions": [
            {
                "ledger": fill.ledger,
                "execution_id": fill.execution_id,
                "side": fill.side,
                "role": role,
                "quantity": _text(quantity),
                "fill_quantity": _text(fill.quantity),
                "price": _text(fill.price),
                "executed_at_utc": _utc(fill.at),
                "session_date": fill.day.isoformat(),
            }
            for (fill, role), quantity in _pieces(item).items()
        ],
    }


def _pieces(item: Trade) -> dict[tuple[Fill, str], Decimal]:
    """Each fill's quantity in the trade by role, in time order: a sell that closes two
    lots is one row, not two."""
    merged: dict[tuple[Fill, str], Decimal] = {}
    for piece in sorted(item.allocations, key=lambda p: p.fill.sort_key()):
        key = (piece.fill, piece.role)
        merged[key] = merged.get(key, ZERO) + piece.quantity
    return merged


def cast_rule(value: str) -> Rule:
    for rule in RULES:
        if value == rule:
            return rule
    raise ValueError(f"unknown rule {value!r}")
