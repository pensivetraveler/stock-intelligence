"""Stock and market information from Toss: stock details and warnings, the USD/KRW rate,
market hours compared with the local calendar, and Korean names for ticker search.

Everything is read on demand through the key's shared `TossSession` (Korean names are kept
by the symbol directory, which refreshes daily). Toss groups US stocks by its own market
labels, not by listing exchange (SPY, listed on NYSE Arca, is under "AMEX"; live check
2026-10-20), so Toss never supplies the venue of an instrument ID here.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from packages.contracts.validator import validate_document
from services.market_data.calendar import CalendarError, UsEquityCalendar
from services.market_data.instruments import KR_CODE
from services.market_data.provider import InvalidBarsQueryError, ProviderDataError
from services.market_data.toss_session import TossSession

SOURCE_ID = "toss-us"
US_LIST_MARKETS = ("NASDAQ", "NYSE", "AMEX")
# Toss US session names and the matching local session bounds (None: no local session).
US_SESSIONS = (
    ("dayMarket", "day_market", None),
    ("preMarket", "pre_market", "pre"),
    ("regularMarket", "regular", "regular"),
    ("afterMarket", "after_market", "post"),
)
KR_SESSIONS = (
    ("preMarket", "pre_market"),
    ("regularMarket", "regular"),
    ("afterMarket", "after_market"),
)


def _utc(value: object, what: str) -> str:
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProviderDataError(f"Toss returned {what} without a valid time") from exc
    if stamp.tzinfo is None:
        raise ProviderDataError(f"Toss returned {what} without a time offset")
    return stamp.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _now_text(now: Callable[[], datetime]) -> str:
    return now().astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _decimal(value: object) -> str | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ProviderDataError("Toss returned an invalid number") from exc
    if not number.is_finite():
        raise ProviderDataError("Toss returned an invalid number")
    return format(number, "f")


def _text(value: object, limit: int = 200) -> str | None:
    return None if value is None else str(value).strip()[:limit] or None


def _validated(document: dict[str, Any]) -> dict[str, Any]:
    try:
        validate_document(document)
    except ValueError as exc:
        raise ProviderDataError(f"Toss data violates the contract: {exc}") from exc
    return document


def stock_info(
    session: TossSession,
    instrument_id: str,
    symbol: str,
    now: Callable[[], datetime],
    source_id: str = SOURCE_ID,
) -> dict[str, Any]:
    """Toss's reference data for one stock plus its active buying warnings."""
    items = session.get("/api/v1/stocks", params={"symbols": symbol})
    item = next(
        (
            entry
            for entry in (items if isinstance(items, list) else [])
            if isinstance(entry, dict) and str(entry.get("symbol", "")).upper() == symbol
        ),
        None,
    )
    if item is None:
        raise InvalidBarsQueryError(f"Toss has no stock {symbol}")
    warnings = session.get("/api/v1/stocks/{symbol}/warnings", path={"symbol": symbol})
    return _validated(
        {
            "kind": "stock_info",
            "schema_version": "0.1",
            "instrument_id": instrument_id,
            "symbol": symbol,
            "source_id": source_id,
            "observed_at_utc": _now_text(now),
            "name": _text(item.get("name")),
            "english_name": _text(item.get("englishName")),
            "isin": _text(item.get("isinCode"), 12),
            "market": _text(item.get("market"), 20),
            "security_type": _text(item.get("securityType"), 40),
            "is_common_share": item.get("isCommonShare")
            if isinstance(item.get("isCommonShare"), bool)
            else None,
            "status": _text(item.get("status"), 20),
            "currency": _text(item.get("currency"), 3),
            "list_date": _text(item.get("listDate"), 10),
            "delist_date": _text(item.get("delistDate"), 10),
            "shares_outstanding": _decimal(item.get("sharesOutstanding")),
            "leverage_factor": _decimal(item.get("leverageFactor")),
            "warnings": [
                {
                    "type": str(warning.get("warningType", ""))[:40],
                    "exchange": _text(warning.get("exchange"), 20),
                    "start_date": _text(warning.get("startDate"), 10),
                    "end_date": _text(warning.get("endDate"), 10),
                }
                for warning in (warnings if isinstance(warnings, list) else [])
                if isinstance(warning, dict) and warning.get("warningType")
            ],
        }
    )


def exchange_rate(
    session: TossSession, base: str, quote: str, now: Callable[[], datetime]
) -> dict[str, Any]:
    """Toss's reference (display) exchange rate; real trades may use a different rate."""
    result = session.get(
        "/api/v1/exchange-rate", params={"baseCurrency": base, "quoteCurrency": quote}
    )
    if not isinstance(result, dict):
        raise ProviderDataError("Toss returned no exchange rate")
    return _validated(
        {
            "kind": "exchange_rate",
            "schema_version": "0.1",
            "source_id": SOURCE_ID,
            "observed_at_utc": _now_text(now),
            "base_currency": base,
            "quote_currency": quote,
            "rate": _decimal(result.get("rate")),
            "mid_rate": _decimal(result.get("midRate")),
            "basis_point": _decimal(result.get("basisPoint")),
            "change": _text(result.get("rateChangeType"), 10),
            "valid_from_utc": _utc(result.get("validFrom"), "an exchange rate"),
            "valid_until_utc": _utc(result.get("validUntil"), "an exchange rate"),
        }
    )


def market_calendar(
    session: TossSession,
    market: str,
    day: date | None,
    local: UsEquityCalendar,
    now: Callable[[], datetime],
) -> dict[str, Any]:
    """Toss's market hours for the previous, current and next business day.

    US sessions are compared with the local exchange calendar the charts use; a session
    that starts or ends at a different time is reported, never silently corrected.
    """
    if market not in ("US", "KR"):
        raise InvalidBarsQueryError("market must be US or KR")
    params = {"date": day.isoformat()} if day is not None else {}
    result = session.get(f"/api/v1/market-calendar/{market}", params=params)
    if not isinstance(result, dict):
        raise ProviderDataError("Toss returned no market calendar")
    days: list[dict[str, Any]] = []
    for key in ("previousBusinessDay", "today", "nextBusinessDay"):
        entry = result.get(key)
        if not isinstance(entry, dict):
            continue
        session_date = str(entry.get("date", ""))[:10]
        if market == "US":
            sessions = _us_sessions(entry, session_date, local)
        else:
            raw = entry.get("integrated")
            integrated: dict[str, Any] = raw if isinstance(raw, dict) else {}
            sessions = [
                _session(name, integrated.get(toss_name), None) for toss_name, name in KR_SESSIONS
            ]
        days.append(
            {
                "role": {"previousBusinessDay": "previous", "today": "today"}.get(key, "next"),
                "date": session_date,
                "sessions": [item for item in sessions if item is not None],
            }
        )
    return _validated(
        {
            "kind": "broker_market_calendar",
            "schema_version": "0.1",
            "source_id": SOURCE_ID,
            "market": market,
            "observed_at_utc": _now_text(now),
            "days": days,
        }
    )


def _us_sessions(
    entry: dict[str, Any], session_date: str, local: UsEquityCalendar
) -> list[dict[str, Any] | None]:
    try:
        window = local.window_for_date(date.fromisoformat(session_date))
    except (ValueError, CalendarError):
        window = None
    sessions: list[dict[str, Any] | None] = []
    for toss_name, name, local_name in US_SESSIONS:
        bounds = None
        if window is not None and local_name is not None:
            bounds = window.bounds(local_name)  # type: ignore[arg-type]
        sessions.append(_session(name, entry.get(toss_name), bounds))
    return sessions


def _session(
    name: str, value: object, local: tuple[datetime, datetime] | None
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None  # closed
    start = _utc(value.get("startTime"), "a session")
    end = _utc(value.get("endTime"), "a session")
    local_start = local_end = None
    if local is not None:
        local_start, local_end = (
            moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            for moment in local
        )
    return {
        "name": name,
        "start_utc": start,
        "end_utc": end,
        "local_start_utc": local_start,
        "local_end_utc": local_end,
        "agrees": None if local is None else (start, end) == (local_start, local_end),
    }


def korean_names(session: TossSession) -> dict[str, str]:
    """Korean display names of US-listed stocks by ticker, where Toss has one."""
    names: dict[str, str] = {}
    for market in US_LIST_MARKETS:
        items = session.get("/api/v1/stocks/all", params={"market": market})
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol", "")).upper()
            name = str(item.get("name") or "").strip()
            # Toss repeats the ticker when it has no Korean name (e.g. "SPY").
            if symbol and name and name.upper() != symbol:
                names[symbol] = name[:200]
    return names


KOREAN_MARKETS = (("KOSPI", "XKRX"), ("KOSDAQ", "XKOS"))


def korean_listings(session: TossSession) -> list[dict[str, str]]:
    """Every active KOSPI and KOSDAQ listing Toss trades, with its Korean name."""
    listings: list[dict[str, str]] = []
    for market, venue in KOREAN_MARKETS:
        items = session.get("/api/v1/stocks/all", params={"market": market})
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            code = str(item.get("symbol", "")).upper()
            name = " ".join(str(item.get("name") or "").split())[:200]
            if KR_CODE.fullmatch(code) and name:
                listings.append({"symbol": code, "name": name, "venue": venue})
    return listings
