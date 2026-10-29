"""메리츠증권 US stock account (read-only): filled orders into the broker ledger, and a
snapshot of holdings, cash and open orders.

Spec: openapi.imeritz.com (generated 2026-10-26). APIs used, all GET:
- `/trading/v1/overseas/orders/history` (TR obtro_oder_201r, 해외주식 주문체결내역): one row
  per order over a date range, 30 rows a page. Corrections and cancellations are rows of
  their own with their own order numbers. A row carries the filled quantity
  (`cntg_dqty`), the fill price (`cntg_crnc_prc`), one fill time (`cntg_ymdh`) and the
  order time (`oder_exec_ymdh`), but no fee;
- `/trading/v1/overseas/orders/today` (TR obtro_oder_103r): today's orders, for the
  open ones;
- `/accounts/v1/me/overseas/holdings`, `.../overseas/currencies`, `/accounts/v1/me/valuation`
  and `/accounts/v1/me/deposit` for the snapshot.

What this module decides, and why:
- One row becomes one `broker_order_execution` marked `order_aggregate`; its single fill
  time is `reported_fill_time` (the spec does not say whether it is the first or the last).
- Fees are null, never zero: the history reports none per order.
- The spec does not say which clock `cntg_ymdh` uses. It is read as Korean time
  (HISTORY_ZONE) until a live check settles it (ADR 0043). A time that lands outside every
  US session (04:00-20:00 New York on a trading day, or the day market before it, ADR 0030)
  is skipped as `time_unresolved` rather than dated by guess.
- The token is bound to one account; the account number is never read or stored, and the
  account is referred to by a hash of the app key.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Callable
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from packages.contracts.validator import validate_document
from services.journal.store import JournalStore
from services.market_data.calendar import UsEquityCalendar
from services.market_data.meritz_session import (
    CURRENCIES_PATH,
    DEPOSIT_PATH,
    HISTORY_PATH,
    HOLDINGS_PATH,
    TODAY_PATH,
    VALUATION_PATH,
    MeritzSession,
)
from services.market_data.overnight import overnight_session_of
from services.market_data.provider import (
    InvalidBarsQueryError,
    MarketDataError,
    ProviderDataError,
)
from services.market_data.symbol_history import resolution_of

BROKER_ID = "meritz"
SEOUL = ZoneInfo("Asia/Seoul")
NEW_YORK = ZoneInfo("America/New_York")
# The clock of `cntg_ymdh` / `oder_exec_ymdh`: not documented; to be confirmed live.
HISTORY_ZONE = SEOUL
SESSION_START, SESSION_END = time(4, 0), time(20, 0)
US_MARKET = "01"  # ovrs_stck_mrkt_tr_cls_code
SIDES = {"1": "sell", "2": "buy"}
ORDER_TYPES = {
    "1": "MARKET",
    "2": "LIMIT",
    "4": "LOO",
    "5": "LOC",
    "6": "MOO",
    "7": "MOC",
    "8": "LIMIT_VWAP",
    "9": "LIMIT_TWAP",
    "A": "VWAP",
    "B": "TWAP",
}
STATUSES = {
    "0": "ACCEPTED",
    "1": "REJECTED",
    "2": "AUTO_REJECTED",
    "3": "AUTO_CANCELED_AFTER_FILL",
    "4": "NOT_RECEIVED",
}
MAX_HISTORY_DAYS = 366 * 5
US_CALENDAR = UsEquityCalendar("XNAS")


def account_ref(session: MeritzSession) -> str:
    return f"meritz-{session.key_ref}"


def _utc_text(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _number(value: object, what: str = "number") -> Decimal:
    text = str(value if value is not None else "0").replace(",", "").strip() or "0"
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ProviderDataError(f"Meritz returned an invalid {what}") from exc
    if not number.is_finite():
        raise ProviderDataError(f"Meritz returned an invalid {what}")
    return number


def _text(number: Decimal) -> str:
    return "0" if number == 0 else format(number.normalize(), "f")


def _optional(value: object) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return _text(_number(value))
    except ProviderDataError:
        return None


def _stamp(value: object) -> datetime | None:
    """A "YYYY-MM-DD HH:MM:SS" history time in HISTORY_ZONE; None when empty or unreadable."""
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=HISTORY_ZONE
        )
    except ValueError:
        return None


def us_session_date(moment: datetime) -> date | None:
    """The US trading date whose session (04:00-20:00 New York, or the day market before
    it) holds `moment`; None when it lies in no session."""
    local = moment.astimezone(NEW_YORK)
    if (
        US_CALENDAR.window_for_date(local.date()) is not None
        and SESSION_START <= local.time() < SESSION_END
    ):
        return local.date()
    return overnight_session_of(US_CALENDAR, moment)


def symbol_of(row: dict[str, Any]) -> str:
    """ "PLTR.OQ" -> "PLTR"; the history has no suffix-free symbol field."""
    code = str(row.get("ovrs_stck_symb_iscd") or row.get("iscd") or "").strip().upper()
    return code.rsplit(".", 1)[0] if "." in code else code


def sync_executions(
    session: MeritzSession,
    store: JournalStore,
    instrument_for: Callable[..., str | None],
    now: Callable[[], datetime],
    since: date,
    until: date | None = None,
) -> dict[str, Any]:
    """Store every filled US order with an order date in [since, until], once.

    Dates are the broker's order dates (Korean). `until` defaults to today. The range is
    read in one paged query; it may start at most MAX_HISTORY_DAYS ago.
    """
    current = now()
    today = current.astimezone(SEOUL).date()
    last = min(until or today, today)
    if since > last:
        raise InvalidBarsQueryError("since must not be after until or today")
    if (today - since).days > MAX_HISTORY_DAYS:
        raise InvalidBarsQueryError(f"since can be at most {MAX_HISTORY_DAYS} days ago")
    synced_at = _utc_text(current)
    ref = account_ref(session)
    counts = {"orders_seen": 0, "stored_new": 0, "already_stored": 0}
    skipped = {
        "not_filled": 0,
        "unsupported_currency": 0,
        "unknown_instrument": 0,
        "time_unresolved": 0,
    }
    conflicts: list[str] = []
    unknown: Counter[str] = Counter()
    params = {
        "oder_sta_date": since.strftime("%Y%m%d"),
        "oder_fin_date": last.strftime("%Y%m%d"),
        "seln_byov_clcd": "0",
        "cntg_cls_code": "1",  # filled orders
    }
    for row in session.rows(HISTORY_PATH, params):
        if not str(row.get("oder_no") or "").strip():
            continue
        counts["orders_seen"] += 1
        before = skipped["unknown_instrument"]
        document = _execution(row, ref, synced_at, instrument_for, skipped)
        if skipped["unknown_instrument"] > before:
            unknown[symbol_of(row)[:32] or "?"] += 1
        if document is None:
            continue
        outcome = store.record_broker_execution(document)
        if outcome == "new":
            counts["stored_new"] += 1
        elif outcome == "same":
            counts["already_stored"] += 1
        else:
            conflicts.append(document["broker_order_id"])
    result = {
        "kind": "broker_sync_result",
        "schema_version": "0.1",
        "broker_id": BROKER_ID,
        "synced_at_utc": synced_at,
        "accounts": 1,
        **counts,
        "skipped": skipped,
        "conflicts": conflicts,
        "unknown_symbols": [
            {"symbol": symbol, "orders": count}
            for symbol, count in sorted(unknown.items(), key=lambda item: (-item[1], item[0]))[
                :1000
            ]
        ],
    }
    validate_document(result)
    return result


def _execution(
    row: dict[str, Any],
    ref: str,
    synced_at: str,
    instrument_for: Callable[..., str | None],
    skipped: dict[str, int],
) -> dict[str, Any] | None:
    quantity = _number(row.get("cntg_dqty"), "quantity")
    price = _number(row.get("cntg_crnc_prc"), "price")
    if quantity <= 0 or price <= 0:
        skipped["not_filled"] += 1
        return None
    if (
        str(row.get("crcd") or "").strip() != "USD"
        or str(row.get("ovrs_stck_mrkt_tr_cls_code") or US_MARKET).strip() != US_MARKET
    ):
        skipped["unsupported_currency"] += 1
        return None
    filled_at, ordered_at = _stamp(row.get("cntg_ymdh")), _stamp(row.get("oder_exec_ymdh"))
    trade_date = us_session_date(filled_at) if filled_at is not None else None
    if filled_at is None or trade_date is None:
        skipped["time_unresolved"] += 1
        return None
    symbol = symbol_of(row)
    side = SIDES.get(str(row.get("seln_byov_clcd") or "").strip())
    # The trade date lets ticker changes and delistings be followed (ADR 0033).
    instrument_id = instrument_for(symbol, "USD", trade_date) if symbol else None
    if instrument_id is None or side is None:
        skipped["unknown_instrument"] += 1
        return None
    order_id = f"{str(row.get('oder_date') or '').strip()[:8]}-{str(row['oder_no']).strip()[:20]}"
    status = STATUSES.get(str(row.get("ovrs_stck_oder_stat_cls_code") or "").strip(), "UNKNOWN")
    correction = str(row.get("oder_rctf_cncl_cls_code") or "0").strip()
    if correction == "1":
        status = f"{status}_AMENDED"  # a correction row: an order of its own
    return {
        "kind": "broker_order_execution",
        "schema_version": "0.1",
        "execution_id": "meritz-" + hashlib.sha256(f"{ref}\0{order_id}".encode()).hexdigest()[:32],
        "instrument_id": instrument_id,
        **(
            {}
            if (resolution := resolution_of(symbol, instrument_id)) is None
            else {"instrument_resolution": resolution}
        ),
        "symbol": symbol[:32],
        "side": side,
        "quantity": _text(quantity),
        "price": _text(price),
        "fees": None,
        "commission": None,
        "tax": None,
        "currency": "USD",
        "executed_at_utc": _utc_text(filled_at),
        "time_meaning": "reported_fill_time",
        "granularity": "order_aggregate",
        "order_type": ORDER_TYPES.get(str(row.get("aspr_type_code") or "").strip(), "UNKNOWN"),
        "order_status": status,
        "ordered_at_utc": None if ordered_at is None else _utc_text(ordered_at),
        "settlement_date": None,
        "broker_id": BROKER_ID,
        "broker_account_ref": ref,
        "broker_order_id": order_id,
        "verification_status": "broker_api_reported",
        "synced_at_utc": synced_at,
    }


# --- snapshot --------------------------------------------------------------------------


def account_snapshot(session: MeritzSession, now: Callable[[], datetime]) -> dict[str, Any]:
    """Holdings, cash, buying power and today's open US orders of the key's account.

    A part that fails is reported in `errors` and the rest is shown. Meritz has no
    commission-rate or conditional-order query in this app's allowlist, so those lists are
    empty.
    """
    errors: list[str] = []

    def read(label: str, call: Callable[[], Any]) -> Any:
        try:
            return call()
        except MarketDataError as exc:
            errors.append(f"{label}: {str(exc)[:200]}")
            return None

    holdings_rows = read("holdings", lambda: list(session.rows(HOLDINGS_PATH)))
    currencies = read("foreign cash", lambda: session.get(CURRENCIES_PATH).get("data"))
    valuation = read("valuation", lambda: session.get(VALUATION_PATH).get("data"))
    deposit = read("cash", lambda: session.get(DEPOSIT_PATH).get("data"))
    today = read(
        "open orders",
        lambda: list(session.rows(TODAY_PATH, {"cntg_cls_code": "2", "seln_byov_clcd": "0"})),
    )
    usd = next(
        (
            item
            for item in (currencies if isinstance(currencies, list) else [])
            if isinstance(item, dict) and str(item.get("crcd")) == "USD"
        ),
        {},
    )
    valuation = valuation if isinstance(valuation, dict) else {}
    deposit = deposit if isinstance(deposit, dict) else {}
    holdings = [
        _holding(row)
        for row in (holdings_rows or [])
        if str(row.get("crcd") or "").strip() in ("USD", "")
    ]
    purchase_usd = sum(
        (_number(row.get("byov_camt")) for row in holdings_rows or [] if row.get("crcd") == "USD"),
        Decimal(0),
    )
    value_usd = sum(
        (
            _number(row.get("frcr_vltn_amt"))
            for row in holdings_rows or []
            if row.get("crcd") == "USD"
        ),
        Decimal(0),
    )
    pnl_usd = sum(
        (
            _number(row.get("vltn_prls_camt"))
            for row in holdings_rows or []
            if row.get("crcd") == "USD"
        ),
        Decimal(0),
    )
    summary = (
        {
            "purchase_amount": {
                "krw": _optional(valuation.get("byng_amt")),
                "usd": _text(purchase_usd) if holdings_rows else None,
            },
            "market_value": {
                "krw": _optional(valuation.get("aset_vltn_amt")),
                "usd": _text(value_usd) if holdings_rows else None,
            },
            "profit_loss": {"krw": None, "usd": _text(pnl_usd) if holdings_rows else None},
            "profit_loss_rate": (
                _text((pnl_usd / purchase_usd * 100).quantize(Decimal("0.0001")))
                if purchase_usd
                else None
            ),
            "daily_profit_loss": {"krw": None, "usd": None},
            "daily_profit_loss_rate": None,
        }
        if valuation or holdings_rows
        else None
    )
    account = {
        "account_ref": account_ref(session),
        "account_seq": 1,
        "account_suffix": None,
        "account_type": "해외주식",
        "summary": summary,
        "holdings": holdings,
        "buying_power": {
            "usd": _optional(usd.get("estm_frcr_amt") if usd else None),
            "krw": _optional(deposit.get("oder_able_cash_amt")),
        },
        "commission_rates": [],
        "open_orders": [_open_order(row) for row in today or [] if _is_open(row)],
        "open_conditional_orders": [],
        "errors": errors,
    }
    document = {
        "kind": "broker_account_snapshot",
        "schema_version": "0.1",
        "broker_id": BROKER_ID,
        "observed_at_utc": _utc_text(now()),
        "accounts": [account],
    }
    try:
        validate_document(document)
    except ValueError as exc:
        raise ProviderDataError(f"Meritz account data violates the contract: {exc}") from exc
    return document


def _holding(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": (symbol_of(row) or "?")[:32],
        "name": (str(row.get("isnm") or "").strip()[:200] or None),
        "market_country": "US" if str(row.get("ovrs_stck_mrkt_tr_cls_code")) == US_MARKET else None,
        "currency": (str(row.get("crcd") or "").strip()[:3] or None),
        "quantity": _optional(row.get("rmnd_dqty")),
        "last_price": _optional(row.get("psnt_prc")),
        "average_purchase_price": _optional(row.get("avrg_unpr")),
        "purchase_amount": _optional(row.get("byov_camt")),
        "market_value": _optional(row.get("frcr_vltn_amt")),
        "profit_loss": _optional(row.get("vltn_prls_camt")),
        "profit_loss_rate": _optional(row.get("vltn_ert")),
        "daily_profit_loss": None,
        "commission": None,
        "tax": None,
    }


def _is_open(row: dict[str, Any]) -> bool:
    ordered = _number(row.get("odqt"))
    filled = _number(row.get("cntg_qty"))
    return (
        str(row.get("oder_rctf_cncl_cls_code") or "0") != "2"
        and str(row.get("ovrs_stck_oder_stat_cls_code") or "0") == "0"
        and ordered > filled
    )


def _open_order(row: dict[str, Any]) -> dict[str, Any]:
    ordered_at = _stamp(row.get("oder_exec_ymdh"))
    return {
        "broker_order_id": (
            f"{str(row.get('oder_date') or '').strip()[:8]}-{str(row.get('oder_no')).strip()[:20]}"
        ),
        "symbol": (symbol_of(row) or "?")[:32],
        "side": SIDES.get(str(row.get("seln_byov_clcd") or "").strip(), "unknown"),
        "order_type": ORDER_TYPES.get(str(row.get("aspr_type_code") or "").strip()),
        "status": STATUSES.get(str(row.get("ovrs_stck_oder_stat_cls_code") or ""), "UNKNOWN"),
        "price": _optional(row.get("ovrs_stck_oder_prc")),
        "quantity": _optional(row.get("odqt")),
        "filled_quantity": _optional(row.get("cntg_qty")),
        "currency": "USD",
        "ordered_at_utc": None if ordered_at is None else _utc_text(ordered_at),
    }
