"""The broker-neutral adapter contract the runner uses (ADR 0037 §0)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Protocol


class OrderNotSupportedError(ValueError):
    """The adapter cannot express this order (venue, order type); nothing was sent."""


@dataclass(frozen=True)
class Capabilities:
    markets: frozenset[str]
    order_types: frozenset[str]


@dataclass(frozen=True)
class SubmitResult:
    """What one order or cancel request came back with.

    `unknown` means the request may have reached the broker but no answer was read
    (timeout, broken connection, a server error without a body). It is never retried.
    """

    status: Literal["accepted", "rejected", "unknown"]
    operation: str
    order_type_code: str
    broker_order_ref: str | None = None
    response_code: str | None = None
    response_message: str | None = None


@dataclass(frozen=True)
class BrokerOrder:
    """One of today's orders as the broker's paper server reports it."""

    broker_order_ref: str
    symbol: str
    side: Literal["buy", "sell"]
    order_type_code: str
    order_quantity: Decimal
    filled_quantity: Decimal
    fill_price: Decimal | None
    remaining_quantity: Decimal
    status_text: str
    ordered_at_local: str | None  # HH:MM:SS in the broker's clock (Seoul for Kiwoom)
    filled_at_local: str | None


class PaperBroker(Protocol):
    broker_id: Literal["kiwoom", "kis"]
    account_ref: str
    time_zone: str  # the clock of the broker's reported times

    def capabilities(self) -> Capabilities: ...

    def order_type_code(self, order_type: str) -> str: ...

    def submit(self, intent: dict[str, Any]) -> SubmitResult: ...

    def cancel(self, broker_order_ref: str, instrument_id: str, symbol: str) -> SubmitResult: ...

    def today_orders(self) -> list[BrokerOrder]: ...
