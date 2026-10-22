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
DOMESTIC_PATH = "/api/dostk/acnt"
# The only account queries that can be sent: api-id -> (path, list field of the reply).
READ_ONLY_QUERIES = {
    "ust21150": (ACCOUNT_PATH, "result_list"),  # 미국주식 일별 주문체결내역
    "kt00015": (DOMESTIC_PATH, "trst_ovrl_trde_prps_array"),  # 위탁종합거래내역
    "ust21100": (ACCOUNT_PATH, "result_list"),  # 미국주식 거래내역 (fees per line)
}
FEE_RANGE_DAYS = 31  # ust21100 is read a month at a time
MAX_FEE_DAYS = 400
READ_ONLY_API_IDS = frozenset(READ_ONLY_QUERIES)
DOMESTIC_RANGE_DAYS = 365  # kt00015 answers at most a year per request
RATE_LIMIT_CODES = {1700, 1701, 1702}
TOKEN_INVALID_CODE = 3
# Seen live: return_code 20 with message code 571758 ("조회내역이 없습니다", "해당 계좌의
# 체결내역이 없습니다") when a range or date simply has no orders.
NO_ROWS = (20, ("571758", "501724"))  # 501724 "관련자료가 없습니다" (ust21100)
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
            [("POST", TOKEN_PATH), ("POST", ACCOUNT_PATH), ("POST", DOMESTIC_PATH)],
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
        """Every row of a read-only account query, following continuation."""
        if api_id not in READ_ONLY_API_IDS:
            raise ProviderDataError(f"{api_id} is not on the read-only allowlist")
        path, field = READ_ONLY_QUERIES[api_id]
        continuation: tuple[str, str] | None = None
        for _page in range(MAX_PAGES):
            payload, continuation = self._page(api_id, path, body, continuation)
            for row in payload.get(field) or []:
                if isinstance(row, dict):
                    yield row
            if continuation is None:
                return
        raise ProviderDataError(f"Kiwoom {api_id} returned more than {MAX_PAGES} pages")

    def _page(
        self,
        api_id: str,
        path: str,
        body: dict[str, str],
        continuation: tuple[str, str] | None,
    ) -> tuple[dict[str, Any], tuple[str, str] | None]:
        for attempt in range(2):
            headers = {
                "api-id": api_id,
                "authorization": f"Bearer {self._token.get(refresh=attempt > 0)}",
                "content-type": "application/json;charset=UTF-8",
            }
            if continuation is not None:
                headers["cont-yn"], headers["next-key"] = continuation
            response = self._http.request("POST", path, json=body, headers=headers)
            try:
                payload = response.json()
            except ValueError as exc:
                if response.status_code >= 500:
                    raise ProviderUnavailableError(
                        f"Kiwoom is unavailable ({response.status_code})"
                    ) from exc
                raise ProviderDataError("Kiwoom returned a response that is not JSON") from exc
            code = int(payload.get("return_code", 0) or 0)
            # A token stops working when another client issues one for the same key
            # (8005 "Token이 유효하지 않습니다", answered with HTTP 200): reissue once.
            invalid = code == TOKEN_INVALID_CODE and "8005" in str(payload.get("return_msg", ""))
            if (response.status_code == 401 or invalid) and attempt == 0:
                continue
            if code in RATE_LIMIT_CODES or response.status_code == 429:
                raise ProviderRateLimitError("Kiwoom request limit reached; retry later")
            if response.status_code in (401, 403):
                raise ProviderAuthError(f"Kiwoom rejected the request ({response.status_code})")
            if response.status_code >= 500:
                raise ProviderUnavailableError(f"Kiwoom is unavailable ({response.status_code})")
            message = str(payload.get("return_msg", ""))
            if (
                response.status_code == 200
                and code == NO_ROWS[0]
                and any(marker in message for marker in NO_ROWS[1])
            ):
                return {}, None
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


DOMESTIC_SIDES = {"매수": "buy", "매도": "sell"}


def _won(value: object) -> Decimal:
    """A Kiwoom amount such as "000000000000102", "+0000102" or "56,900" in exact form."""
    text = str(value or "0").replace(",", "").strip() or "0"
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ProviderDataError("Kiwoom returned an invalid amount") from exc
    if not number.is_finite():
        raise ProviderDataError("Kiwoom returned an invalid amount")
    return abs(number)


def sync_domestic(
    session: KiwoomAccountSession,
    store: JournalStore,
    instrument_for: Callable[..., str | None],
    now: Callable[[], datetime],
    since: date,
    until: date | None = None,
) -> dict[str, Any]:
    """Store the Kiwoom account's Korean stock trades from `since` to `until`, once.

    Source: kt00015 (위탁종합거래내역, 매매, 국내주식), the only account history that
    still answers for past years (kt00007 returned nothing for 2023 dates when checked).
    It reports settlement lines: trade (contract) date, side, quantity, price,
    commission and taxes, but no order number and no fill or order time. Each line
    becomes one record marked `settlement_line` with `time_meaning: not_reported`; its
    fill time is left empty rather than guessed, so FIFO positions list it as unordered.
    """
    current = now()
    today = current.astimezone(SEOUL).date()
    last = min(until or today, today)
    if since > last:
        raise InvalidBarsQueryError("since must not be after until or today")
    if (today - since).days > MAX_HISTORY_DAYS:
        raise InvalidBarsQueryError(f"since can be at most {MAX_HISTORY_DAYS} days ago")
    if (last - since).days >= DOMESTIC_RANGE_DAYS:
        raise InvalidBarsQueryError(f"one domestic sync covers at most {DOMESTIC_RANGE_DAYS} days")
    synced_at = _utc_text(current)
    ref = account_ref(session)
    counts = {"orders_seen": 0, "stored_new": 0, "already_stored": 0}
    skipped = {"not_filled": 0, "unsupported_currency": 0, "unknown_instrument": 0}
    conflicts: list[str] = []
    unknown: Counter[str] = Counter()
    body = {
        "strt_dt": since.strftime("%Y%m%d"),
        "end_dt": last.strftime("%Y%m%d"),
        "tp": "3",  # 매매
        "stk_cd": "",
        "crnc_cd": "",
        "gds_tp": "1",  # 국내주식
        "frgn_stex_code": "",
        "dmst_stex_tp": "%",
        "qry_sort_tp": "2",  # oldest first
    }
    for row in session.rows("kt00015", body):
        if not row.get("trde_no"):
            continue
        counts["orders_seen"] += 1
        before = skipped["unknown_instrument"]
        document = _domestic_execution(row, ref, synced_at, instrument_for, skipped)
        if skipped["unknown_instrument"] > before:
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
        "unknown_symbols": [
            {"symbol": symbol, "orders": count}
            for symbol, count in sorted(unknown.items(), key=lambda item: (-item[1], item[0]))[
                :1000
            ]
        ],
    }
    validate_document(result)
    return result


def _domestic_execution(
    row: dict[str, Any],
    ref: str,
    synced_at: str,
    instrument_for: Callable[..., str | None],
    skipped: dict[str, int],
) -> dict[str, Any] | None:
    quantity = _won(row.get("trde_qty_jwa_cnt"))
    price = _won(row.get("trde_unit"))
    if quantity == 0 or price == 0:
        skipped["not_filled"] += 1
        return None
    if str(row.get("crnc_cd") or "KRW") != "KRW":
        skipped["unsupported_currency"] += 1
        return None
    raw_code = str(row.get("stk_cd") or "").strip().upper()
    side = DOMESTIC_SIDES.get(str(row.get("io_tp_nm") or "").strip())
    # "A005930": A marks a stock (J: ELW, Q: ETN are not listings the app charts).
    code = raw_code[1:] if raw_code.startswith("A") and len(raw_code) == 7 else None
    instrument_id = instrument_for(code, "KRW") if code else None
    if instrument_id is None or side is None:
        skipped["unknown_instrument"] += 1
        return None
    trade_day = _order_date(row.get("cntr_dt") or row.get("trde_dt"))
    settled = _order_date(row.get("trde_dt"))
    order_id = f"kr-{settled:%Y%m%d}-{str(row['trde_no']).strip()[:20]}"
    commission = _won(row.get("cmsn"))
    tax = _won(row.get("trde_agri_tax"))
    return {
        "kind": "broker_order_execution",
        "schema_version": "0.1",
        "execution_id": "kiwoom-" + hashlib.sha256(f"{ref}\0{order_id}".encode()).hexdigest()[:32],
        "instrument_id": instrument_id,
        "symbol": code,
        "side": side,
        "quantity": _text(quantity),
        "price": _text(price),
        "fees": _text(commission + tax),
        "commission": _text(commission),
        "tax": _text(tax),
        "currency": "KRW",
        "executed_at_utc": None,
        "time_meaning": "not_reported",
        "granularity": "settlement_line",
        "order_type": str(row.get("crd_deal_tp_nm") or "UNKNOWN").strip()[:40] or "UNKNOWN",
        "order_status": str(row.get("rmrk_nm") or "UNKNOWN").strip()[:40] or "UNKNOWN",
        "ordered_at_utc": None,
        "trade_date": trade_day.isoformat(),
        "settlement_date": settled.isoformat(),
        "broker_id": BROKER_ID,
        "broker_account_ref": ref,
        "broker_order_id": order_id,
        "verification_status": "broker_api_reported",
        "synced_at_utc": synced_at,
    }


def _order_date(value: object) -> date:
    try:
        return datetime.strptime(str(value), "%Y%m%d").date()
    except ValueError as exc:
        raise ProviderDataError("Kiwoom returned an invalid date") from exc


def sync_fees(
    session: KiwoomAccountSession,
    store: JournalStore,
    instrument_for: Callable[..., str | None],
    now: Callable[[], datetime],
    since: date,
    until: date | None = None,
) -> dict[str, Any]:
    """Store the commission and tax of the Kiwoom account's US settlement lines, once.

    ust21100 (미국주식 거래내역, 매매) reports fees per settlement line without an order
    number, often on one line of a stock's day; they cannot be tied to orders, so each
    line with a fee is kept as a `broker_fee_line` and positions add them per account
    and instrument (ADR 0029). `since`/`until` are settlement (Seoul) dates.
    """
    current = now()
    today = current.astimezone(SEOUL).date()
    last = min(until or today, today)
    if since > last:
        raise InvalidBarsQueryError("since must not be after until or today")
    if (today - since).days > MAX_HISTORY_DAYS:
        raise InvalidBarsQueryError(f"since can be at most {MAX_HISTORY_DAYS} days ago")
    if (last - since).days >= MAX_FEE_DAYS:
        raise InvalidBarsQueryError(f"one fee sync covers at most {MAX_FEE_DAYS} days")
    synced_at = _utc_text(current)
    ref = account_ref(session)
    counts = {"orders_seen": 0, "stored_new": 0, "already_stored": 0}
    skipped = {"not_filled": 0, "unsupported_currency": 0, "unknown_instrument": 0}
    conflicts: list[str] = []
    unknown: Counter[str] = Counter()
    cursor = since
    while cursor <= last:
        stop = min(last, cursor + timedelta(days=FEE_RANGE_DAYS - 1))
        body = {
            "strt_dt": cursor.strftime("%Y%m%d"),
            "end_dt": stop.strftime("%Y%m%d"),
            "tp": "3",  # 매매
            "stex_tp": "",
            "stk_cd": "",
            "krw_repl_skip_yn": "N",  # required in practice, though the spec says optional
        }
        for row in session.rows("ust21100", body):
            if not row.get("deal_no"):
                continue
            counts["orders_seen"] += 1
            commission = _signed(row.get("fc_cmsn"))
            tax = _signed(row.get("fc_deal_tax"))
            if commission == 0 and tax == 0:
                continue  # most lines carry no fee; the stock's fee sits on one line
            if str(row.get("crnc_code") or "USD") != "USD":
                skipped["unsupported_currency"] += 1
                continue
            symbol = str(row.get("stk_cd") or "").strip().upper()
            side = {"매수": "buy", "매도": "sell"}.get(str(row.get("rmrk_nm") or "").strip())
            settled = _order_date(row.get("deal_dt"))
            instrument_id = instrument_for(symbol, "USD", settled) if symbol else None
            if instrument_id is None or side is None:
                skipped["unknown_instrument"] += 1
                unknown[symbol or "?"] += 1
                continue
            line_key = f"{settled:%Y%m%d}-{str(row['deal_no']).strip()[:20]}"
            document = {
                "kind": "broker_fee_line",
                "schema_version": "0.1",
                "line_id": "kiwoom-fee-"
                + hashlib.sha256(f"{ref}\0{line_key}".encode()).hexdigest()[:32],
                "broker_id": BROKER_ID,
                "broker_account_ref": ref,
                "instrument_id": instrument_id,
                "symbol": symbol[:32],
                "side": side,
                "settlement_date": settled.isoformat(),
                # Correction lines can carry signed amounts; the fee keeps its sign.
                "quantity": _text(abs(_signed(row.get("deal_qty")))),
                "price": _text(abs(_signed(row.get("uv_exrt")))),
                "commission": _text_signed(commission),
                "tax": _text_signed(tax),
                "currency": "USD",
                "synced_at_utc": synced_at,
            }
            outcome = store.record_broker_fee_line(document)
            if outcome == "new":
                counts["stored_new"] += 1
            elif outcome == "same":
                counts["already_stored"] += 1
            else:
                conflicts.append(line_key)
        cursor = stop + timedelta(days=1)
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


def _signed(value: object) -> Decimal:
    text = str(value or "0").replace(",", "").strip() or "0"
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ProviderDataError("Kiwoom returned an invalid amount") from exc
    if not number.is_finite():
        raise ProviderDataError("Kiwoom returned an invalid amount")
    return number


def _text_signed(number: Decimal) -> str:
    return "0" if number == 0 else format(number.normalize(), "f")
