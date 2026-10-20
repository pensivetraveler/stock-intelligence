"""Toss account data (read-only): an on-demand account view and a sync of closed orders'
executions into the broker ledger.

Toss reports executions per order, not per fill: total filled quantity, average fill
price, commission, tax and one `filledAt`. Each closed order with fills becomes one
`broker_order_execution` marked `order_aggregate`, its time marked as the reported last
fill; nothing finer is invented. Only US orders are stored for now (Korean instrument IDs
come with the Korean-market stage). Account numbers are reduced to their last four digits
and never stored or returned whole. Order placing endpoints are not reachable (see
toss_session.py).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from packages.contracts.validator import validate_document
from services.journal.store import JournalStore
from services.market_data.provider import MarketDataError, ProviderDataError
from services.market_data.toss_session import TossSession

BROKER_ID = "toss"
ORDER_PAGE = 100
MAX_ORDER_PAGES = 200  # 20,000 closed orders per account
SIDES = {"BUY": "buy", "SELL": "sell"}


def _utc(value: object) -> str | None:
    if value in (None, ""):
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProviderDataError("Toss returned an invalid time") from exc
    if stamp.tzinfo is None:
        raise ProviderDataError("Toss returned a time without an offset")
    return stamp.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _now_text(now: Callable[[], datetime]) -> str:
    return now().astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _dec(value: object, *, non_negative: bool = False) -> str | None:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ProviderDataError("Toss returned an invalid number") from exc
    if not number.is_finite() or (non_negative and number < 0):
        raise ProviderDataError("Toss returned an invalid number")
    text = format(number, "f")
    return "0" if number == 0 else text


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: object, limit: int) -> str | None:
    return None if value in (None, "") else str(value)[:limit]


def account_ref(session: TossSession, account_seq: int) -> str:
    return f"toss-{session.key_ref}-{account_seq}"


def accounts(session: TossSession) -> list[dict[str, Any]]:
    result = session.get("/api/v1/accounts")
    return [item for item in (result if isinstance(result, list) else []) if isinstance(item, dict)]


def _money(value: object) -> dict[str, str | None]:
    data = value if isinstance(value, dict) else {}
    return {"krw": _dec(data.get("krw")), "usd": _dec(data.get("usd"))}


def _holdings(result: object) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if not isinstance(result, dict):
        return None, []
    pnl = _as_dict(result.get("profitLoss"))
    daily = _as_dict(result.get("dailyProfitLoss"))
    market = _as_dict(result.get("marketValue"))
    summary = {
        "purchase_amount": _money(result.get("totalPurchaseAmount")),
        "market_value": _money(market.get("amount")),
        "profit_loss": _money(pnl.get("amount")),
        "profit_loss_rate": _dec(pnl.get("rate")),
        "daily_profit_loss": _money(daily.get("amount")),
        "daily_profit_loss_rate": _dec(daily.get("rate")),
    }
    items: list[dict[str, Any]] = []
    for item in result.get("items") or []:
        if not isinstance(item, dict) or not item.get("symbol"):
            continue
        value = _as_dict(item.get("marketValue"))
        loss = _as_dict(item.get("profitLoss"))
        day = _as_dict(item.get("dailyProfitLoss"))
        cost = _as_dict(item.get("cost"))
        items.append(
            {
                "symbol": str(item["symbol"])[:32],
                "name": _text(item.get("name"), 200),
                "market_country": _text(item.get("marketCountry"), 4),
                "currency": _text(item.get("currency"), 3),
                "quantity": _dec(item.get("quantity"), non_negative=True),
                "last_price": _dec(item.get("lastPrice")),
                "average_purchase_price": _dec(item.get("averagePurchasePrice")),
                "purchase_amount": _dec(value.get("purchaseAmount")),
                "market_value": _dec(value.get("amount")),
                "profit_loss": _dec(loss.get("amount")),
                "profit_loss_rate": _dec(loss.get("rate")),
                "daily_profit_loss": _dec(day.get("amount")),
                "commission": _dec(cost.get("commission")),
                "tax": _dec(cost.get("tax")),
            }
        )
    return summary, items


def account_snapshot(session: TossSession, now: Callable[[], datetime]) -> dict[str, Any]:
    """Every account's holdings, buying power, commission rates and open orders.

    A part that fails is reported in the account's `errors` and the rest is shown.
    """
    views = [_account_view(session, account) for account in accounts(session)]
    document = {
        "kind": "broker_account_snapshot",
        "schema_version": "0.1",
        "broker_id": BROKER_ID,
        "observed_at_utc": _now_text(now),
        "accounts": views,
    }
    try:
        validate_document(document)
    except ValueError as exc:
        raise ProviderDataError(f"Toss account data violates the contract: {exc}") from exc
    return document


def _account_view(session: TossSession, account: dict[str, Any]) -> dict[str, Any]:
    seq = int(account.get("accountSeq", 0))
    number = "".join(ch for ch in str(account.get("accountNo") or "") if ch.isdigit())
    errors: list[str] = []

    def read(label: str, call: Callable[[], Any]) -> Any:
        try:
            return call()
        except MarketDataError as exc:
            errors.append(f"{label}: {str(exc)[:200]}")
            return None

    def get(path: str, **params: str) -> Callable[[], Any]:
        return lambda: session.get(path, params=params, account=seq)

    summary, holdings = _holdings(read("holdings", get("/api/v1/holdings")))
    power = {
        currency: _dec(
            _as_dict(
                read(f"buying power {currency}", get("/api/v1/buying-power", currency=currency))
            ).get("cashBuyingPower")
        )
        for currency in ("USD", "KRW")
    }
    rates = read("commissions", get("/api/v1/commissions"))
    orders = _as_dict(read("open orders", get("/api/v1/orders", status="OPEN")))
    conditional = _as_dict(
        read("open conditional orders", get("/api/v1/conditional-orders", status="OPEN"))
    )
    return {
        "account_ref": account_ref(session, seq),
        "account_seq": seq,
        "account_suffix": number[-4:] if len(number) >= 4 else None,
        "account_type": str(account.get("accountType") or "UNKNOWN")[:40],
        "summary": summary,
        "holdings": holdings,
        "buying_power": {"usd": power["USD"], "krw": power["KRW"]},
        "commission_rates": [
            {
                "market_country": str(item.get("marketCountry"))[:4],
                "rate": _dec(item.get("commissionRate")),
                "start_date": _text(item.get("startDate"), 10),
                "end_date": _text(item.get("endDate"), 10),
            }
            for item in (rates if isinstance(rates, list) else [])
            if isinstance(item, dict) and item.get("marketCountry")
        ],
        "open_orders": [
            _open_order(item)
            for item in orders.get("orders") or []
            if isinstance(item, dict) and item.get("orderId")
        ],
        "open_conditional_orders": [
            {
                "conditional_order_id": str(item["conditionalOrderId"])[:200],
                "type": str(item.get("type") or "UNKNOWN")[:40],
                "status": str(item.get("status") or "UNKNOWN")[:40],
                "symbol": str(item.get("symbol") or "?")[:32],
                "quantity": _dec(item.get("quantity"), non_negative=True),
                "expire_date": _text(item.get("expireDate"), 10),
                "created_at_utc": _utc(item.get("createdAt")),
            }
            for item in conditional.get("conditionalOrders") or []
            if isinstance(item, dict) and item.get("conditionalOrderId")
        ],
        "errors": errors,
    }


def _open_order(item: dict[str, Any]) -> dict[str, Any]:
    execution = _as_dict(item.get("execution"))
    return {
        "broker_order_id": str(item["orderId"])[:200],
        "symbol": str(item.get("symbol") or "?")[:32],
        "side": str(item.get("side") or "?")[:10],
        "order_type": _text(item.get("orderType"), 40),
        "status": str(item.get("status") or "UNKNOWN")[:40],
        "price": _dec(item.get("price")),
        "quantity": _dec(item.get("quantity"), non_negative=True),
        "filled_quantity": _dec(execution.get("filledQuantity"), non_negative=True),
        "currency": _text(item.get("currency"), 3),
        "ordered_at_utc": _utc(item.get("orderedAt")),
    }


def sellable_quantity(
    session: TossSession, account_seq: int, symbol: str, now: Callable[[], datetime]
) -> dict[str, Any]:
    result = _as_dict(
        session.get("/api/v1/sellable-quantity", params={"symbol": symbol}, account=account_seq)
    )
    document = {
        "kind": "broker_sellable_quantity",
        "schema_version": "0.1",
        "broker_id": BROKER_ID,
        "account_ref": account_ref(session, account_seq),
        "symbol": symbol,
        "sellable_quantity": _dec(result.get("sellableQuantity"), non_negative=True) or "0",
        "observed_at_utc": _now_text(now),
    }
    validate_document(document)
    return document


def sync_executions(
    session: TossSession,
    store: JournalStore,
    instrument_for: Callable[[str], str | None],
    now: Callable[[], datetime],
) -> dict[str, Any]:
    """Store the executions of every closed, filled US order in every account, once."""
    synced_at = _now_text(now)
    counts = {"orders_seen": 0, "stored_new": 0, "already_stored": 0}
    skipped = {"not_filled": 0, "not_us": 0, "unknown_instrument": 0}
    conflicts: list[str] = []
    account_list = accounts(session)
    for account in account_list:
        seq = int(account.get("accountSeq", 0))
        ref = account_ref(session, seq)
        cursor: str | None = None
        for _page in range(MAX_ORDER_PAGES):
            params = {"status": "CLOSED", "limit": str(ORDER_PAGE)}
            if cursor:
                params["cursor"] = cursor
            page = session.get("/api/v1/orders", params=params, account=seq)
            orders = page.get("orders") if isinstance(page, dict) else None
            for order in orders if isinstance(orders, list) else []:
                if not isinstance(order, dict) or not order.get("orderId"):
                    continue
                counts["orders_seen"] += 1
                document = _execution(order, ref, synced_at, instrument_for, skipped)
                if document is None:
                    continue
                outcome = store.record_broker_execution(document)
                if outcome == "new":
                    counts["stored_new"] += 1
                elif outcome == "same":
                    counts["already_stored"] += 1
                else:
                    conflicts.append(document["broker_order_id"])
            if not isinstance(page, dict) or not page.get("hasNext") or not page.get("nextCursor"):
                break
            cursor = str(page["nextCursor"])
    result = {
        "kind": "broker_sync_result",
        "schema_version": "0.1",
        "broker_id": BROKER_ID,
        "synced_at_utc": synced_at,
        "accounts": len(account_list),
        **counts,
        "skipped": skipped,
        "conflicts": conflicts,
    }
    validate_document(result)
    return result


def _execution(
    order: dict[str, Any],
    ref: str,
    synced_at: str,
    instrument_for: Callable[[str], str | None],
    skipped: dict[str, int],
) -> dict[str, Any] | None:
    execution = _as_dict(order.get("execution"))
    quantity = _dec(execution.get("filledQuantity"), non_negative=True)
    price = _dec(execution.get("averageFilledPrice"), non_negative=True)
    if quantity in (None, "0") or price is None:
        skipped["not_filled"] += 1
        return None
    if order.get("currency") != "USD":
        skipped["not_us"] += 1
        return None
    symbol = str(order.get("symbol") or "").upper()
    instrument_id = instrument_for(symbol)
    side = SIDES.get(str(order.get("side")))
    if instrument_id is None or side is None:
        skipped["unknown_instrument"] += 1
        return None
    commission = _dec(execution.get("commission"), non_negative=True) or "0"
    tax = _dec(execution.get("tax"), non_negative=True) or "0"
    order_id = str(order["orderId"])[:200]
    ordered_at = _utc(order.get("orderedAt"))
    if ordered_at is None:
        raise ProviderDataError("Toss returned a closed order without orderedAt")
    return {
        "kind": "broker_order_execution",
        "schema_version": "0.1",
        "execution_id": "toss-" + hashlib.sha256(f"{ref}\0{order_id}".encode()).hexdigest()[:32],
        "instrument_id": instrument_id,
        "symbol": symbol[:32],
        "side": side,
        "quantity": quantity,
        "price": price,
        "fees": _dec(Decimal(commission) + Decimal(tax)) or "0",
        "commission": commission,
        "tax": tax,
        "currency": "USD",
        "executed_at_utc": _utc(execution.get("filledAt")),
        "time_meaning": "reported_last_fill",
        "granularity": "order_aggregate",
        "order_type": str(order.get("orderType") or "UNKNOWN")[:40],
        "order_status": str(order.get("status") or "UNKNOWN")[:40],
        "ordered_at_utc": ordered_at,
        "settlement_date": _text(execution.get("settlementDate"), 10),
        "broker_id": BROKER_ID,
        "broker_account_ref": ref,
        "broker_order_id": order_id,
        "verification_status": "broker_api_reported",
        "synced_at_utc": synced_at,
    }
