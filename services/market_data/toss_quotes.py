"""A point-in-time quote from Toss: last price, order book, recent trades and price limits.

Read on demand through the key's shared `TossSession`; nothing here is cached or stored.
Prices stay exact decimal strings and every timestamp is converted to UTC. Toss quotes US
stocks from some venues only (not the NBBO), so the quote says which source it came from.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, TypeGuard

from packages.contracts.validator import validate_document
from services.market_data.provider import InvalidBarsQueryError, ProviderDataError
from services.market_data.toss_session import TossSession

SOURCE_ID = "toss-us"
TRADE_COUNT = 30
CURRENCIES = frozenset({"USD", "KRW"})


def _utc(value: object, field: str) -> str:
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProviderDataError(
            f"Toss returned an unreadable {field} time {str(value)[:40]!r}"
        ) from exc
    if stamp.tzinfo is None:
        raise ProviderDataError(f"Toss returned a {field} time without a UTC offset")
    return stamp.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _decimal(value: object, *, non_negative: bool = False) -> str:
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except InvalidOperation as exc:
        raise ProviderDataError("Toss returned a quote with an invalid number") from exc
    if not number.is_finite() or (non_negative and number < 0):
        raise ProviderDataError("Toss returned a quote with an invalid number")
    return format(number, "f")


def _stamped(item: object) -> TypeGuard[dict[str, Any]]:
    """Whether Toss gave this item a time at all.

    A stock with no trade yet in the session (a quiet day-market name such as PARA)
    comes back with an empty or null time: that is "no trade", not broken data.
    """
    return isinstance(item, dict) and item.get("timestamp") not in (None, "")


def _levels(items: object) -> list[dict[str, str]]:
    return [
        {"price": _decimal(item["price"]), "volume": _decimal(item["volume"], non_negative=True)}
        for item in (items if isinstance(items, list) else [])
        if isinstance(item, dict)
    ]


def market_quote(
    session: TossSession,
    instrument_id: str,
    symbol: str,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    source_id: str = SOURCE_ID,
) -> dict[str, Any]:
    prices = session.get("/api/v1/prices", params={"symbols": symbol})
    price = next(
        (
            item
            for item in (prices if isinstance(prices, list) else [])
            if isinstance(item, dict) and str(item.get("symbol", "")).upper() == symbol
        ),
        None,
    )
    book = session.get("/api/v1/orderbook", params={"symbol": symbol})
    trades = session.get("/api/v1/trades", params={"symbol": symbol, "count": str(TRADE_COUNT)})
    try:
        limits = session.get("/api/v1/price-limits", params={"symbol": symbol})
    except InvalidBarsQueryError:
        limits = None  # markets without daily price limits (such as US stocks)

    currencies = {
        str(item.get("currency"))
        for item in (price, book, limits, *(trades if isinstance(trades, list) else []))
        if isinstance(item, dict) and item.get("currency") is not None
    }
    if len(currencies) != 1 or not currencies <= CURRENCIES:
        raise ProviderDataError(f"Toss returned an unexpected currency set {sorted(currencies)}")

    document: dict[str, Any] = {
        "kind": "market_quote",
        "schema_version": "0.1",
        "instrument_id": instrument_id,
        "symbol": symbol,
        "source_id": source_id,
        "currency": currencies.pop(),
        "observed_at_utc": now()
        .astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        # No last price without its trade time: a price we cannot place in time is not
        # shown as the latest trade.
        "last": (
            {
                "price": _decimal(price["lastPrice"]),
                "at_utc": _utc(price["timestamp"], "last trade"),
            }
            if _stamped(price) and price.get("lastPrice") not in (None, "")
            else None
        ),
        "orderbook": (
            {
                "at_utc": _utc(book["timestamp"], "order book"),
                "asks": _levels(book.get("asks")),
                "bids": _levels(book.get("bids")),
            }
            if _stamped(book)
            else None
        ),
        "trades": [
            {
                "price": _decimal(item["price"]),
                "volume": _decimal(item["volume"], non_negative=True),
                "at_utc": _utc(item["timestamp"], "trade"),
            }
            for item in (trades if isinstance(trades, list) else [])
            if _stamped(item)  # a trade with no time cannot be placed in the list
        ],
        "price_limits": (
            {
                "upper": _decimal(limits["upperLimitPrice"]),
                "lower": _decimal(limits["lowerLimitPrice"]),
                "at_utc": _utc(limits["timestamp"], "price limit"),
            }
            if _stamped(limits) and limits.get("upperLimitPrice") is not None
            else None
        ),
    }
    try:
        validate_document(document)
    except ValueError as exc:
        raise ProviderDataError(f"Toss quote violates the contract: {exc}") from exc
    return document
