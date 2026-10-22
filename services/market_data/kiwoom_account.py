"""Kiwoom US order history (read-only): a sync of filled orders into the broker ledger.

One account API of the official spec, POST /api/us/acnt with api-id ust21150 (미국주식 일별
주문체결내역): one order date's filled orders with filled quantity, average fill price
(`cntr_uv`), status and one fill time (`cntr_time`). It is read for every weekday of the
range. ust21180 (기간별 주문내역) was not used to find the dates with fills: it lists every
order, filled or not, and one live month took 44 pages.

What is and is not reported, checked live on 2026-10-22 (account suffix 2859):

- One row per order, not per fill: each becomes one `broker_order_execution` marked
  `order_aggregate`. `cntr_time` is a single time per order and the spec does not say
  whether it is the first or the last fill, so it is marked `reported_fill_time`.
- Times are Seoul clock times and `ord_dt` is the order's US (New York) trading date: one
  order date held orders from 22:51 to 06:58 Seoul time, in order-number order, which is
  09:51 to 17:58 New York time on that date. A time is placed on the Seoul date that puts
  it inside 04:00-20:00 New York time on `ord_dt`. Otherwise it is placed in the day market
  (overnight session) that belongs to `ord_dt`, 20:00 the evening before to 04:00 New York
  time (ADR 0030): live day-market fills were at 09:00-17:59 Seoul time with order numbers
  before the date's first 04:00-20:00 order. When neither fits, the order is skipped as
  `time_unresolved` rather than dated by guess.
- Neither API reports commission or tax per order (ust21100 has them per settlement line
  without an order number), so fees are stored as null, never as zero.

Only the token and this api-id can be called: no order endpoint is reachable. The
access token is bound to one account; the account number is never read or stored, and
the account is referred to by a hash of the app key.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from packages.contracts.validator import validate_document
from services.journal.store import JournalStore
from services.market_data.broker_common import AllowlistedClient, TokenCache
from services.market_data.calendar import UsEquityCalendar
from services.market_data.overnight import overnight_window
from services.market_data.provider import (
    InvalidBarsQueryError,
    ProviderAuthError,
    ProviderDataError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from services.market_data.symbol_history import resolution_of

BROKER_ID = "kiwoom"
REAL_BASE_URL = "https://api.kiwoom.com"
DEMO_BASE_URL = "https://mockapi.kiwoom.com"
TOKEN_PATH = "/oauth2/token"
ACCOUNT_PATH = "/api/us/acnt"
READ_ONLY_API_IDS = frozenset({"ust21150"})
RATE_LIMIT_CODES = {1700, 1701, 1702}
# Seen live: return_code 20 with message code 571758 ("조회내역이 없습니다", "해당 계좌의
# 체결내역이 없습니다") when a range or date simply has no orders.
NO_ROWS = (20, "571758")
MAX_PAGES = 500  # per order date
MAX_RANGE_DAYS = 400
MAX_HISTORY_DAYS = 366 * 5
SEOUL = ZoneInfo("Asia/Seoul")
NEW_YORK = ZoneInfo("America/New_York")
SESSION_START, SESSION_END = time(4, 0), time(20, 0)  # pre-market open to after-hours close
SIDES = {"매수": "buy", "매도": "sell"}
US_CALENDAR = UsEquityCalendar("XNAS")


@dataclass(frozen=True)
class KiwoomAccountCredentials:
    app_key: str
    secret_key: str
    environment: str  # "real" or "demo"

    def __repr__(self) -> str:
        return f"KiwoomAccountCredentials(environment={self.environment!r})"


class KiwoomAccountSession:
    """Token plus the read-only account calls; nothing else can be sent."""

    def __init__(
        self,
        credentials: KiwoomAccountCredentials,
        *,
        transport: httpx.BaseTransport | None = None,
        min_interval_seconds: float = 0.25,
    ) -> None:
        self._credentials = credentials
        base_url = DEMO_BASE_URL if credentials.environment == "demo" else REAL_BASE_URL
        self._http = AllowlistedClient(
            base_url,
            [("POST", TOKEN_PATH), ("POST", ACCOUNT_PATH)],
            transport=transport,
            min_interval_seconds=min_interval_seconds,
        )
        self._token = TokenCache(self._issue_token)

    @property
    def key_ref(self) -> str:
        """A stable, non-secret reference to this key (a truncated hash of its app key)."""
        return hashlib.sha256(self._credentials.app_key.encode()).hexdigest()[:16]

    def _issue_token(self) -> tuple[str, float]:
        response = self._http.request(
            "POST",
            TOKEN_PATH,
            headers={"api-id": "au10001", "content-type": "application/json;charset=UTF-8"},
            json={
                "grant_type": "client_credentials",
                "appkey": self._credentials.app_key,
                "secretkey": self._credentials.secret_key,
            },
        )
        if response.status_code >= 400:
            raise ProviderAuthError(f"Kiwoom refused to issue a token ({response.status_code})")
        payload = response.json()
        token = payload.get("token")
        if not token or int(payload.get("return_code", 0) or 0) != 0:
            raise ProviderAuthError("Kiwoom returned no access token")
        lifetime = 3_600.0
        try:
            expires_at = datetime.strptime(str(payload.get("expires_dt", "")), "%Y%m%d%H%M%S")
            remaining = expires_at.replace(tzinfo=SEOUL) - datetime.now(timezone.utc)
            lifetime = max(60.0, remaining.total_seconds())
        except ValueError:
            pass
        return str(token), lifetime

    def rows(self, api_id: str, body: dict[str, str]) -> Iterator[dict[str, Any]]:
        """Every `result_list` row of a read-only account query, following continuation."""
        if api_id not in READ_ONLY_API_IDS:
            raise ProviderDataError(f"{api_id} is not on the read-only allowlist")
        continuation: tuple[str, str] | None = None
        for _page in range(MAX_PAGES):
            payload, continuation = self._page(api_id, body, continuation)
            for row in payload.get("result_list") or []:
                if isinstance(row, dict):
                    yield row
            if continuation is None:
                return
        raise ProviderDataError(f"Kiwoom {api_id} returned more than {MAX_PAGES} pages")

    def _page(
        self, api_id: str, body: dict[str, str], continuation: tuple[str, str] | None
    ) -> tuple[dict[str, Any], tuple[str, str] | None]:
        for attempt in range(2):
            headers = {
                "api-id": api_id,
                "authorization": f"Bearer {self._token.get(refresh=attempt > 0)}",
                "content-type": "application/json;charset=UTF-8",
            }
            if continuation is not None:
                headers["cont-yn"], headers["next-key"] = continuation
            response = self._http.request("POST", ACCOUNT_PATH, json=body, headers=headers)
            try:
                payload = response.json()
            except ValueError as exc:
                if response.status_code >= 500:
                    raise ProviderUnavailableError(
                        f"Kiwoom is unavailable ({response.status_code})"
                    ) from exc
                raise ProviderDataError("Kiwoom returned a response that is not JSON") from exc
            code = int(payload.get("return_code", 0) or 0)
            if response.status_code == 401 and attempt == 0:
                continue  # token expired or rejected: reissue once
            if code in RATE_LIMIT_CODES or response.status_code == 429:
                raise ProviderRateLimitError("Kiwoom request limit reached; retry later")
            if response.status_code in (401, 403):
                raise ProviderAuthError(f"Kiwoom rejected the request ({response.status_code})")
            if response.status_code >= 500:
                raise ProviderUnavailableError(f"Kiwoom is unavailable ({response.status_code})")
            message = str(payload.get("return_msg", ""))
            if response.status_code == 200 and code == NO_ROWS[0] and NO_ROWS[1] in message:
                return {"result_list": []}, None
            if response.status_code >= 400 or code != 0:
                raise InvalidBarsQueryError(f"Kiwoom refused the query ({code}): {message[:200]}")
            more = response.headers.get("cont-yn", "N") == "Y" and response.headers.get("next-key")
            return payload, (("Y", response.headers["next-key"]) if more else None)
        raise ProviderAuthError("Kiwoom rejected the reissued token")


def _utc_text(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _clock(value: object) -> time | None:
    try:
        return datetime.strptime(str(value), "%H:%M:%S").time()
    except ValueError:
        return None


def session_time(order_date: date, seoul_clock: time) -> datetime | None:
    """The instant of a Seoul clock time inside `order_date`'s US sessions: 04:00-20:00
    New York on that date, else its day market (ADR 0030); None when neither fits or the
    date is not a US trading day."""
    if US_CALENDAR.window_for_date(order_date) is None:
        return None
    for offset in (0, 1):
        moment = datetime.combine(order_date + timedelta(days=offset), seoul_clock, SEOUL)
        local = moment.astimezone(NEW_YORK)
        if local.date() == order_date and SESSION_START <= local.time() < SESSION_END:
            return moment
    window = overnight_window(US_CALENDAR, order_date)
    if window is None:
        return None
    found = [
        moment
        for offset in (-1, 0)
        if window[0]
        <= (moment := datetime.combine(order_date + timedelta(days=offset), seoul_clock, SEOUL))
        < window[1]
    ]
    return found[0] if len(found) == 1 else None


def _ordered_at(fill: datetime, seoul_clock: time) -> datetime:
    """The latest instant with this Seoul clock time that is not after the fill."""
    moment = datetime.combine(fill.astimezone(SEOUL).date(), seoul_clock, SEOUL)
    return moment if moment <= fill else moment - timedelta(days=1)


def _quantity(value: object) -> Decimal:
    try:
        number = Decimal(str(value).strip() or "0")
    except InvalidOperation as exc:
        raise ProviderDataError("Kiwoom returned an invalid quantity") from exc
    if not number.is_finite() or number < 0:
        raise ProviderDataError("Kiwoom returned an invalid quantity")
    return number


def _text(number: Decimal) -> str:
    return "0" if number == 0 else format(number.normalize(), "f")


def account_ref(session: KiwoomAccountSession) -> str:
    return f"kiwoom-{session.key_ref}"


def order_dates(start: date, end: date) -> Iterator[date]:
    """Every weekday in [start, end]: a holiday answers "no orders" (NO_ROWS)."""
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            yield cursor
        cursor += timedelta(days=1)


def sync_executions(
    session: KiwoomAccountSession,
    store: JournalStore,
    instrument_for: Callable[..., str | None],
    now: Callable[[], datetime],
    since: date,
    until: date | None = None,
) -> dict[str, Any]:
    """Store every filled US order with a New York order date in [since, until], once.

    `until` defaults to today. One request per weekday, so a range is at most
    MAX_RANGE_DAYS long and starts at most MAX_HISTORY_DAYS ago; longer histories are
    synced in several calls.
    """
    current = now()
    today = current.astimezone(NEW_YORK).date()
    last = min(until or today, today)
    if since > last:
        raise InvalidBarsQueryError("since must not be after until or today")
    if (today - since).days > MAX_HISTORY_DAYS:
        raise InvalidBarsQueryError(f"since can be at most {MAX_HISTORY_DAYS} days ago")
    if (last - since).days >= MAX_RANGE_DAYS:
        raise InvalidBarsQueryError(f"one sync covers at most {MAX_RANGE_DAYS} days")
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
    for order_date in order_dates(since, last):
        body = {
            "ord_dt": order_date.strftime("%Y%m%d"),
            "query_tp": "5",  # filled orders, in order
            "slby_tp": "0",
        }
        for row in session.rows("ust21150", body):
            if not row.get("ord_no"):
                continue
            counts["orders_seen"] += 1
            unknown_before = skipped["unknown_instrument"]
            document = _execution(row, order_date, ref, synced_at, instrument_for, skipped)
            if skipped["unknown_instrument"] > unknown_before:
                unknown[str(row.get("stk_cd") or "?").strip().upper()[:32]] += 1
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
        # Which symbols could not be matched to a listing, most orders first.
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
    order_date: date,
    ref: str,
    synced_at: str,
    instrument_for: Callable[..., str | None],
    skipped: dict[str, int],
) -> dict[str, Any] | None:
    quantity = _quantity(row.get("cntr_qty"))
    price = _quantity(row.get("cntr_uv"))
    if quantity == 0 or price == 0:
        skipped["not_filled"] += 1
        return None
    if row.get("crnc_code") != "USD":
        skipped["unsupported_currency"] += 1
        return None
    fill_clock, order_clock = _clock(row.get("cntr_time")), _clock(row.get("ord_time"))
    filled_at = session_time(order_date, fill_clock) if fill_clock else None
    if filled_at is None or order_clock is None:
        skipped["time_unresolved"] += 1
        return None
    symbol = str(row.get("stk_cd") or "").strip().upper()
    side = SIDES.get(str(row.get("slby_tp_nm") or "").strip())
    # The order date lets ticker changes and delistings be followed (ADR 0033).
    instrument_id = instrument_for(symbol, "USD", order_date) if symbol else None
    if instrument_id is None or side is None:
        skipped["unknown_instrument"] += 1
        return None
    order_id = f"{order_date:%Y%m%d}-{str(row['ord_no']).strip()[:40]}"
    return {
        "kind": "broker_order_execution",
        "schema_version": "0.1",
        "execution_id": "kiwoom-" + hashlib.sha256(f"{ref}\0{order_id}".encode()).hexdigest()[:32],
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
        "order_type": str(row.get("frgn_trde_tp") or "UNKNOWN").strip()[:40] or "UNKNOWN",
        "order_status": str(row.get("ord_stat_nm") or "UNKNOWN").strip()[:40] or "UNKNOWN",
        "ordered_at_utc": _utc_text(_ordered_at(filled_at, order_clock)),
        "settlement_date": None,
        "broker_id": BROKER_ID,
        "broker_account_ref": ref,
        "broker_order_id": order_id,
        "verification_status": "broker_api_reported",
        "synced_at_utc": synced_at,
    }
