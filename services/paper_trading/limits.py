"""Pre-submission limit checks for paper orders (ADR 0037 §5).

Checked in code before any HTTP request. A limit set to None means "no limit for now"
(the user's 2026-10-23 decision for daily orders, daily loss and positions); it is still
recorded with every intent so a later value applies without a schema change.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

OrderType = Literal["limit", "market"]


@dataclass(frozen=True)
class PaperLimits:
    allowed_instrument_ids: tuple[str, ...]
    max_order_amount_krw: Decimal | None = None
    max_daily_orders: int | None = None
    max_daily_loss_rate: Decimal | None = None
    max_position_amount_krw: Decimal | None = None

    def document(self) -> dict[str, Any]:
        def text(value: Decimal | None) -> str | None:
            return None if value is None else format(value, "f")

        return {
            "max_order_amount_krw": text(self.max_order_amount_krw),
            "max_daily_orders": self.max_daily_orders,
            "max_daily_loss_rate": text(self.max_daily_loss_rate),
            "max_position_amount_krw": text(self.max_position_amount_krw),
            "allowed_instrument_ids": list(self.allowed_instrument_ids),
        }


@dataclass(frozen=True)
class OrderAmount:
    instrument_id: str
    side: Literal["buy", "sell"]
    market: str  # "us" or "kr"
    currency: Literal["USD", "KRW"]
    order_type: OrderType
    quantity: Decimal
    limit_price: Decimal | None
    estimated_max_price: Decimal | None


@dataclass(frozen=True)
class RunnerFacts:
    """What the runner knows when it checks one intent."""

    supported_markets: frozenset[str]
    unknown_submissions: int
    orders_today: int
    # Loss since the start of the day as a positive rate (0.02 = 2%), or None if unknown.
    daily_loss_rate: Decimal | None
    position_amount_krw: Decimal | None


@dataclass(frozen=True)
class LimitResult:
    passed: bool
    reasons: tuple[str, ...]
    order_amount_krw: Decimal | None


def order_price(order: OrderAmount) -> Decimal | None:
    """Price the amount limit uses: the limit price, or a market order's estimated maximum."""
    return order.limit_price if order.order_type == "limit" else order.estimated_max_price


def check_limits(
    order: OrderAmount,
    limits: PaperLimits,
    facts: RunnerFacts,
    krw_per_usd: Decimal | None,
) -> LimitResult:
    reasons: list[str] = []
    if order.instrument_id not in limits.allowed_instrument_ids:
        reasons.append("symbol_not_allowed")
    if order.market not in facts.supported_markets:
        reasons.append("market_not_supported")
    if facts.unknown_submissions > 0:
        reasons.append("unknown_submission_pending")
    if order.quantity <= 0:
        reasons.append("invalid_quantity")

    amount_krw: Decimal | None = None
    price = order_price(order)
    if price is None or price <= 0:
        reasons.append("price_unavailable")
    elif order.currency == "KRW":
        amount_krw = order.quantity * price
    elif krw_per_usd is not None and krw_per_usd > 0:
        amount_krw = order.quantity * price * krw_per_usd

    if limits.max_order_amount_krw is not None:
        if amount_krw is None:
            if "price_unavailable" not in reasons:
                reasons.append("fx_unavailable")
        elif amount_krw > limits.max_order_amount_krw:
            reasons.append("order_amount_exceeded")
    if limits.max_daily_orders is not None and facts.orders_today >= limits.max_daily_orders:
        reasons.append("daily_order_count_exceeded")
    if limits.max_daily_loss_rate is not None:
        if facts.daily_loss_rate is None:
            reasons.append("daily_loss_unknown")
        elif facts.daily_loss_rate >= limits.max_daily_loss_rate:
            reasons.append("daily_loss_exceeded")
    if limits.max_position_amount_krw is not None and order.side == "buy":
        if facts.position_amount_krw is None or amount_krw is None:
            reasons.append("position_unknown")
        elif facts.position_amount_krw + amount_krw > limits.max_position_amount_krw:
            reasons.append("position_amount_exceeded")
    return LimitResult(passed=not reasons, reasons=tuple(reasons), order_amount_krw=amount_krw)
