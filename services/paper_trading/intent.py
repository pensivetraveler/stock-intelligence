"""Order intents: a strategy signal recorded before any paper order is sent (ADR 0037 §2).

The intent ID is derived from the approval, strategy version, instrument, side and
signal bar, so the same signal processed twice (for example after a restart) gets the
same ID and can never be submitted twice.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from packages.contracts.validator import validate_document
from services.paper_trading.limits import (
    LimitResult,
    OrderAmount,
    PaperLimits,
    RunnerFacts,
    check_limits,
)
from services.paper_trading.paper_client import BrokerId


class NonFinalBarError(ValueError):
    """A signal came from a bar that is not final; no intent is made from it."""


@dataclass(frozen=True)
class StrategyRef:
    strategy_id: str
    strategy_version: int
    definition_sha256: str
    strategy_kind: Literal["rules", "python"]
    logs_sha256: str | None = None


@dataclass(frozen=True)
class SignalData:
    """The data a signal was computed from, pinned like a backtest's inputs (ADR 0021)."""

    data_as_of_utc: datetime
    signal_bar_start_utc: datetime
    signal_bar_end_utc: datetime
    signal_bar_final: bool
    timeframe: str
    source_id: str
    feed_scope: str
    dataset_revision: str
    adjustment: str
    session: str
    input_bar_count: int
    input_bars_sha256: str
    calendar_id: str
    calendar_version: str
    # How the signal bar counted as final: the source said so, or the plan's settle time
    # after the bar's end had passed (ADR 0037, per plan).
    final_by: Literal["source", "settle"] = "source"
    settle_seconds: int | None = None


@dataclass(frozen=True)
class Sizing:
    method: Literal["shares", "cash", "percent_equity"]
    input: Decimal
    reference_price: Decimal
    reference_price_source: str
    reference_price_as_of_utc: datetime
    fee_rate: Decimal = Decimal("0")
    equity_basis: Decimal | None = None
    cash_basis: Decimal | None = None
    basis_as_of_utc: datetime | None = None


@dataclass(frozen=True)
class OrderSpec:
    order_type: Literal["limit", "market"]
    quantity: Decimal
    limit_price: Decimal | None = None
    market_buffer_bps: Decimal | None = None

    def estimated_max_price(self, reference_price: Decimal) -> Decimal | None:
        """A market order's price ceiling for the amount limit: reference × (1 + buffer)."""
        if self.order_type != "market" or self.market_buffer_bps is None:
            return None
        return reference_price * (1 + self.market_buffer_bps / Decimal(10_000))


@dataclass(frozen=True)
class FxRate:
    krw_per_usd: Decimal
    source: str
    as_of_utc: datetime


def _utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")


def intent_id(
    approval_id: str,
    strategy: StrategyRef,
    instrument_id: str,
    side: str,
    signal_bar_start_utc: datetime,
) -> str:
    key = json.dumps(
        [
            approval_id,
            strategy.strategy_id,
            strategy.strategy_version,
            strategy.definition_sha256,
            instrument_id,
            side,
            _utc(signal_bar_start_utc),
        ],
        separators=(",", ":"),
    )
    return hashlib.sha256(key.encode()).hexdigest()


def market_of(instrument_id: str) -> str:
    return instrument_id.split(":", 1)[0]


def build_intent(
    *,
    broker_id: BrokerId,
    broker_account_ref: str,
    approval_id: str,
    strategy: StrategyRef,
    instrument_id: str,
    symbol: str,
    side: Literal["buy", "sell"],
    data: SignalData,
    signal_summary: str,
    operands: list[tuple[str, Decimal | None]],
    sizing: Sizing,
    order: OrderSpec,
    fx: FxRate | None,
    limits: PaperLimits,
    facts: RunnerFacts,
    created_at: datetime,
) -> tuple[dict[str, Any], LimitResult]:
    """The validated intent document and its limit check. Raises for a non-final bar."""
    if not data.signal_bar_final:
        raise NonFinalBarError("signals come only from final bars")
    currency: Literal["USD", "KRW"] = "KRW" if market_of(instrument_id) == "kr" else "USD"
    estimated_max = order.estimated_max_price(sizing.reference_price)
    result = check_limits(
        OrderAmount(
            instrument_id=instrument_id,
            side=side,
            market=market_of(instrument_id),
            currency=currency,
            order_type=order.order_type,
            quantity=order.quantity,
            limit_price=order.limit_price,
            estimated_max_price=estimated_max,
        ),
        limits,
        facts,
        None if fx is None else fx.krw_per_usd,
    )
    document: dict[str, Any] = {
        "kind": "paper_order_intent",
        "schema_version": "0.1",
        "intent_id": intent_id(
            approval_id, strategy, instrument_id, side, data.signal_bar_start_utc
        ),
        "created_at_utc": _utc(created_at),
        "broker_id": broker_id,
        "broker_account_ref": broker_account_ref,
        "environment": "demo",
        "approval_id": approval_id,
        "strategy": {
            "strategy_id": strategy.strategy_id,
            "strategy_version": strategy.strategy_version,
            "definition_sha256": strategy.definition_sha256,
            "strategy_kind": strategy.strategy_kind,
            "logs_sha256": strategy.logs_sha256,
        },
        "instrument_id": instrument_id,
        "symbol": symbol,
        "side": side,
        "currency": currency,
        "data": {
            "data_as_of_utc": _utc(data.data_as_of_utc),
            "signal_bar_start_utc": _utc(data.signal_bar_start_utc),
            "signal_bar_end_utc": _utc(data.signal_bar_end_utc),
            "signal_bar_final": True,
            "timeframe": data.timeframe,
            "source_id": data.source_id,
            "feed_scope": data.feed_scope,
            "dataset_revision": data.dataset_revision,
            "adjustment": data.adjustment,
            "session": data.session,
            "input_bar_count": data.input_bar_count,
            "input_bars_sha256": data.input_bars_sha256,
            "calendar_id": data.calendar_id,
            "calendar_version": data.calendar_version,
            "final_by": data.final_by,
            "settle_seconds": data.settle_seconds,
        },
        "signal": {
            "summary": signal_summary,
            "operands": [{"operand": name, "value": _decimal(value)} for name, value in operands],
        },
        "sizing": {
            "method": sizing.method,
            "input": _decimal(sizing.input),
            "equity_basis": _decimal(sizing.equity_basis),
            "cash_basis": _decimal(sizing.cash_basis),
            "basis_as_of_utc": None
            if sizing.basis_as_of_utc is None
            else _utc(sizing.basis_as_of_utc),
            "reference_price": _decimal(sizing.reference_price),
            "reference_price_source": sizing.reference_price_source,
            "reference_price_as_of_utc": _utc(sizing.reference_price_as_of_utc),
            "fee_rate": _decimal(sizing.fee_rate),
            "rounding": "floor_whole_shares",
        },
        "order": {
            "order_type": order.order_type,
            "quantity": _decimal(order.quantity),
            "limit_price": _decimal(order.limit_price),
            "market_buffer_bps": _decimal(order.market_buffer_bps),
            "estimated_max_price": _decimal(estimated_max),
        },
        "fx": None
        if fx is None
        else {
            "krw_per_usd": _decimal(fx.krw_per_usd),
            "source": fx.source,
            "as_of_utc": _utc(fx.as_of_utc),
        },
        "limit_check": {
            "passed": result.passed,
            "reasons": list(result.reasons),
            "order_amount_krw": _decimal(result.order_amount_krw),
            "limits": limits.document(),
        },
    }
    validate_document(document)
    return document, result
