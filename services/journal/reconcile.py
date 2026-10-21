"""Read-only reconciliation of the broker API ledger with manual and imported executions.

The result is a computed view. No ledger is merged, rewritten or annotated: running it
twice over the same records gives the same answer, and nothing is stored.

The broker reports one record per order (total quantity at the average price, with the
last fill's time), while manual and imported records may be single fills. Each of the
two user ledgers is compared with the broker ledger on its own, in four passes per
instrument and side:

1. Order ID link: an imported row whose broker execution ID is the broker's order ID
   belongs to that order, whatever its time. The linked rows are matched when their
   quantity sums to the order's and their average price is within the tolerance, and
   conflicting otherwise.
2. One-to-one: equal quantity, price within the tolerance, times compatible. Closest
   in time first, then pairs are shifted along augmenting paths where that pairs one
   more record, so the number of matches is the maximum. A record with an approximate
   or unknown time that fits several orders is left unmatched as ambiguous, not
   assigned by guess.
3. Aggregated fills: several exact- or minute-precision records inside one order's
   window [ordered at, last fill] whose quantities sum to the order's and whose
   average price is within the tolerance.
4. Conflicts: an order and a record of the same side at a compatible time that still
   differ in quantity or price. Records with an unknown time are never paired here,
   since nothing ties them to a particular order.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, cast

Ledger = Literal["broker_api", "manual_actual", "statement_imported"]
BROKER: Ledger = "broker_api"
MANUAL: Ledger = "manual_actual"
IMPORTED: Ledger = "statement_imported"

DEFAULT_PRICE_TOLERANCE_BPS = Decimal(10)
DEFAULT_TIME_TOLERANCE = timedelta(minutes=2)
# How far an "approximate" manual time may be from the real fill.
APPROXIMATE_WINDOW = timedelta(hours=3)
_BPS = Decimal(10000)


@dataclass(frozen=True)
class Parameters:
    price_tolerance_bps: Decimal = DEFAULT_PRICE_TOLERANCE_BPS
    time_tolerance: timedelta = DEFAULT_TIME_TOLERANCE
    approximate_window: timedelta = APPROXIMATE_WINDOW


@dataclass
class _Record:
    ledger: Ledger
    document: dict[str, Any]
    side: str
    quantity: Decimal
    price: Decimal
    time_basis: str
    # When the fill(s) can have happened; None is unbounded on that side.
    earliest: datetime | None
    latest: datetime | None
    links: list[dict[str, Any]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def execution_id(self) -> str:
        return cast(str, self.document["execution_id"])

    @property
    def claimed(self) -> bool:
        return bool(self.links)


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _text(value: Decimal) -> str:
    return "0" if value == 0 else format(value.normalize(), "f")


def _broker(document: dict[str, Any]) -> _Record:
    executed = document["executed_at_utc"]
    return _Record(
        BROKER,
        document,
        cast(str, document["side"]),
        Decimal(cast(str, document["quantity"])),
        Decimal(cast(str, document["price"])),
        "reported_last_fill",
        _parse(cast(str, document["ordered_at_utc"])),
        None if executed is None else _parse(cast(str, executed)),
    )


def _user(ledger: Ledger, document: dict[str, Any], parameters: Parameters) -> _Record:
    precision = cast(str, document["time_precision"])
    executed = document["executed_at_utc"]
    earliest: datetime | None
    latest: datetime | None
    if executed is None:
        # Unknown time: the fill happened before the user recorded it.
        earliest, latest = None, _parse(cast(str, document["asserted_at_utc"]))
    else:
        at = _parse(cast(str, executed))
        if precision == "minute":
            earliest, latest = at, at + timedelta(seconds=59)
        elif precision == "approximate":
            earliest = at - parameters.approximate_window
            latest = at + parameters.approximate_window
        else:
            earliest, latest = at, at
    return _Record(
        ledger,
        document,
        cast(str, document["side"]),
        Decimal(cast(str, document["quantity"])),
        Decimal(cast(str, document["price"])),
        precision,
        earliest,
        latest,
    )


def _gap(order: _Record, record: _Record) -> timedelta:
    """Time between the two windows; zero when they overlap."""
    if order.latest is not None and record.earliest is not None and record.earliest > order.latest:
        return record.earliest - order.latest
    if record.latest is not None and order.earliest is not None and order.earliest > record.latest:
        return order.earliest - record.latest
    return timedelta(0)


def _price_bps(order_price: Decimal, other: Decimal) -> Decimal:
    if order_price == 0:
        return Decimal(0) if other == 0 else _BPS
    return (other - order_price) / order_price * _BPS


def _average(records: list[_Record]) -> tuple[Decimal, Decimal]:
    quantity = sum((record.quantity for record in records), Decimal(0))
    if quantity == 0:
        return quantity, Decimal(0)
    notional = sum((record.quantity * record.price for record in records), Decimal(0))
    return quantity, notional / quantity


def _link(order: _Record, group: list[_Record], relation: str, reasons: list[str]) -> None:
    quantity, price = _average(group)
    difference = {
        "quantity_difference": _text(quantity - order.quantity),
        "price_difference_bps": _text(_price_bps(order.price, price).quantize(Decimal("0.01"))),
    }
    for record in group:
        order.links.append(
            {
                "ledger": record.ledger,
                "execution_id": record.execution_id,
                "relation": relation,
                **difference,
            }
        )
        record.links.append(
            {
                "ledger": BROKER,
                "execution_id": order.execution_id,
                "relation": relation,
                **difference,
            }
        )
        for reason in reasons:
            if reason not in record.reasons:
                record.reasons.append(reason)
    for reason in reasons:
        if reason not in order.reasons:
            order.reasons.append(reason)


def _differences(order: _Record, group: list[_Record], parameters: Parameters) -> list[str]:
    quantity, price = _average(group)
    reasons = []
    if quantity != order.quantity:
        reasons.append("quantity_differs")
    if abs(_price_bps(order.price, price)) > parameters.price_tolerance_bps:
        reasons.append("price_differs")
    return reasons


def _time_reasons(order: _Record, record: _Record) -> list[str]:
    reasons = []
    if record.time_basis == "unknown":
        reasons.append("time_unknown")
    elif record.time_basis == "approximate":
        reasons.append("time_approximate")
    if order.latest is None:
        reasons.append("broker_time_unknown")
    return reasons


def _augment(
    start: str,
    candidates: dict[str, list[_Record]],
    by_order: dict[str, str],
    by_record: dict[str, str],
) -> None:
    """Pair `start` by shifting earlier pairs along an alternating path, if one exists."""
    reached_from: dict[str, str] = {}  # order ID -> record ID that reached it
    queue = [start]
    while queue:
        record_id = queue.pop(0)
        for order in candidates[record_id]:
            order_id = order.execution_id
            if order_id in reached_from:
                continue
            reached_from[order_id] = record_id
            owner = by_order.get(order_id)
            if owner is not None:
                queue.append(owner)
                continue
            # A free order: flip every pair on the path back to `start`.
            while True:
                record_id = reached_from[order_id]
                previous = by_record.get(record_id)
                by_order[order_id], by_record[record_id] = record_id, order_id
                if record_id == start or previous is None:
                    return
                order_id = previous
    return


def _reconcile_ledger(
    ledger: Ledger, orders: list[_Record], records: list[_Record], parameters: Parameters
) -> None:
    tolerance = parameters.time_tolerance

    def compatible(order: _Record, record: _Record) -> bool:
        return order.side == record.side and _gap(order, record) <= tolerance

    def fits(order: _Record, record: _Record) -> bool:
        return (
            compatible(order, record)
            and record.quantity == order.quantity
            and not _differences(order, [record], parameters)
        )

    # 1. Order ID links (statement rows carrying the broker's order ID).
    for order in orders:
        linked = [
            record
            for record in records
            if record.document.get("broker_execution_id") == order.document["broker_order_id"]
            and record.side == order.side
            and not record.claimed
        ]
        if linked:
            differences = _differences(order, linked, parameters)
            reasons = ["order_id_link"] + (["aggregated_fills"] if len(linked) > 1 else [])
            _link(
                order,
                linked,
                "conflicting" if differences else "matched",
                reasons + differences,
            )

    def open_orders() -> list[_Record]:
        return [
            order for order in orders if not any(link["ledger"] == ledger for link in order.links)
        ]

    # 2. One-to-one, closest in time first; loose times must fit a single order.
    available = open_orders()
    candidates: dict[str, list[_Record]] = {}
    for record in records:
        if record.claimed:
            continue
        fitting = [order for order in available if fits(order, record)]
        if record.time_basis in ("unknown", "approximate") and len(fitting) > 1:
            record.reasons.append("ambiguous_candidates")
            continue
        candidates[record.execution_id] = fitting
    # Equally close pairs go to the side with fewer alternatives first, so a record
    # that fits two orders does not take the only order another record fits.
    order_degree: dict[str, int] = {}
    for fitting in candidates.values():
        for order in fitting:
            order_degree[order.execution_id] = order_degree.get(order.execution_id, 0) + 1
    pairs = [
        (
            _gap(order, record),
            abs(_price_bps(order.price, record.price)),
            min(len(candidates[record.execution_id]), order_degree[order.execution_id]),
            order.execution_id,
            record.execution_id,
            order,
            record,
        )
        for record in records
        if record.execution_id in candidates
        for order in candidates[record.execution_id]
    ]
    by_order: dict[str, str] = {}  # order ID -> record ID
    by_record: dict[str, str] = {}
    for _, _, _, order_id, record_id, _, _ in sorted(pairs, key=lambda pair: pair[:5]):
        if order_id not in by_order and record_id not in by_record:
            by_order[order_id], by_record[record_id] = record_id, order_id
    # Then reassign along augmenting paths where that pairs one more record, so the
    # number of matches is the maximum possible (Kuhn's algorithm, breadth first).
    for start in candidates:
        if start not in by_record:
            _augment(start, candidates, by_order, by_record)
    orders_by_id = {order.execution_id: order for order in available}
    for record in records:
        if record.execution_id in by_record:
            order = orders_by_id[by_record[record.execution_id]]
            _link(order, [record], "matched", _time_reasons(order, record))

    # 3. Several fills of one order.
    for order in open_orders():
        if order.latest is None or order.earliest is None:
            continue
        group = [
            record
            for record in records
            if not record.claimed
            and record.side == order.side
            and record.time_basis in ("exact", "minute")
            and record.earliest is not None
            and record.latest is not None
            and record.earliest >= order.earliest - tolerance
            and record.latest <= order.latest + tolerance + timedelta(seconds=59)
        ]
        if len(group) > 1 and not _differences(order, group, parameters):
            _link(order, group, "matched", ["aggregated_fills"])

    # 4. Same side and time, different quantity or price.
    conflicts = []
    for order in open_orders():
        for record in records:
            if record.claimed or record.time_basis == "unknown" or not compatible(order, record):
                continue
            conflicts.append(
                (
                    _gap(order, record),
                    abs(record.quantity - order.quantity),
                    order.execution_id,
                    record.execution_id,
                    order,
                    record,
                )
            )
    taken = set()
    for _, _, order_id, _, order, record in sorted(conflicts, key=lambda pair: pair[:4]):
        if order_id in taken or record.claimed:
            continue
        taken.add(order_id)
        _link(
            order,
            [record],
            "conflicting",
            _differences(order, [record], parameters) + _time_reasons(order, record),
        )


def _status(record: _Record) -> str:
    relations = {link["relation"] for link in record.links}
    if "conflicting" in relations:
        return "conflicting"
    if "matched" in relations:
        return "matched"
    return {BROKER: "broker_only", MANUAL: "manual_only", IMPORTED: "imported_only"}[record.ledger]


def _record_time(record: _Record) -> str | None:
    document = record.document
    if record.ledger == BROKER:
        return cast(str | None, document["executed_at_utc"]) or cast(
            str, document["ordered_at_utc"]
        )
    return cast(str | None, document["executed_at_utc"])


def _view(record: _Record) -> dict[str, Any]:
    if record.ledger == BROKER:
        for ledger, reason in (
            (MANUAL, "no_manual_counterpart"),
            (IMPORTED, "no_imported_counterpart"),
        ):
            if not any(link["ledger"] == ledger for link in record.links):
                record.reasons.append(reason)
    elif not record.links:
        record.reasons.append("no_broker_counterpart")
    document = record.document
    return {
        "ledger": record.ledger,
        "execution_id": record.execution_id,
        "instrument_id": document["instrument_id"],
        "side": record.side,
        "quantity": document["quantity"],
        "price": document["price"],
        "currency": document["currency"],
        "time_utc": _record_time(record),
        "time_basis": record.time_basis,
        "status": _status(record),
        "counterparts": sorted(
            record.links, key=lambda link: (link["ledger"], link["execution_id"])
        ),
        "reasons": record.reasons,
    }


def _since_time(record: _Record) -> datetime | None:
    if record.ledger == BROKER:
        return record.latest or record.earliest
    executed = record.document["executed_at_utc"]
    if executed is None:
        return _parse(cast(str, record.document["asserted_at_utc"]))
    return _parse(cast(str, executed))


def reconcile(
    broker: list[dict[str, Any]],
    manual: list[dict[str, Any]],
    imported: list[dict[str, Any]],
    *,
    since: datetime | None = None,
    parameters: Parameters | None = None,
) -> dict[str, Any]:
    """Classify every record of the three actual-fill ledgers; nothing is stored.

    `since` filters the result, not the input: records just before it still count as
    counterparts, so a match is never lost at the boundary.
    """
    parameters = parameters or Parameters()
    orders = [_broker(document) for document in broker]
    manual_records = [_user(MANUAL, document, parameters) for document in manual]
    imported_records = [_user(IMPORTED, document, parameters) for document in imported]
    groups: dict[tuple[str, Ledger], list[_Record]] = defaultdict(list)
    for record in orders + manual_records + imported_records:
        groups[(cast(str, record.document["instrument_id"]), record.ledger)].append(record)
    for instrument_id in sorted({instrument for instrument, _ in groups}):
        for ledger in (MANUAL, IMPORTED):
            records = groups.get((instrument_id, ledger), [])
            if records:
                _reconcile_ledger(
                    ledger, groups.get((instrument_id, BROKER), []), records, parameters
                )
    every = [
        record
        for record in orders + manual_records + imported_records
        if since is None or (moment := _since_time(record)) is None or moment >= since
    ]
    ledger_order = {BROKER: 0, MANUAL: 1, IMPORTED: 2}
    every.sort(
        key=lambda record: (
            cast(str, record.document["instrument_id"]),
            _record_time(record) or "9999",
            ledger_order[record.ledger],
            record.execution_id,
        )
    )
    views = [_view(record) for record in every]
    summary = {
        status: sum(1 for view in views if view["status"] == status)
        for status in ("matched", "conflicting", "broker_only", "manual_only", "imported_only")
    }
    return {
        "parameters": {
            "price_tolerance_bps": _text(parameters.price_tolerance_bps),
            "time_tolerance_seconds": int(parameters.time_tolerance.total_seconds()),
            "approximate_window_seconds": int(parameters.approximate_window.total_seconds()),
        },
        "summary": summary,
        "records": views,
    }
