"""메리츠증권 Open API US market data (read-only, from the official spec): minute bars,
daily bars and a point-in-time quote.

Spec: openapi.imeritz.com (JSON download /api-spec/download/json, generated 2026-10-26;
how-to page). Public beta 2026-10-12 to 10-25, general availability 2026-10-26.
Documented facts used here:
- `GET /market/v1/overseas/candles/minutes` (TR ohhts_eapi_005r) returns at most the
  latest 40 bars, newest first, with no date or paging parameter; bar times are
  `korea_date` + `korea_hour` in Korean time;
- `GET /market/v1/overseas/candles/days` (TR ohhts_6001_003r) returns every day between
  `from` and `to` (YYYYMMDD), newest first; `mod_stpr_cls_code` "1" asks for prices as
  traded and "0" for split-adjusted ones;
- the current price (TR ohhts_6000_001r) carries no time; the tick list (`trades/ticks`)
  carries Korean clock times without a date; the order book carries no time;
- data may be used only for the account holder's own investment, never passed on.
Token, MAC header and `rsp_cd` handling live in `meritz_session`.
Not documented: whether `korea_hour` marks the bar start or end, session coverage of the
minute and daily bars, and whether a daily `date` is the New York trading date (assumed).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from packages.contracts.validator import validate_document
from services.credentials.catalog import LISTING_ONLY
from services.market_data.broker_common import (
    BrokerConfigError,
    RawMinute,
    SourceIdentity,
    account_environment,
    build_result,
    capabilities,
    decimal_field,
    instrument_listing,
    parse_sessions,
    parse_symbols,
    require,
    validate_minute_query,
)
from services.market_data.calendar import CalendarError, TradingSessionName, UsEquityCalendar
from services.market_data.daily import (
    ADJUSTED,
    DailySource,
    RawDay,
    build_daily_result,
    sessions_in_range,
    validate_daily_query,
)
from services.market_data.instruments import parse_instrument
from services.market_data.meritz_session import (
    BASE_URL,
    DAYS_PATH,
    EXCHANGE_CODES,
    MINUTES_PATH,
    ORDERBOOK_PATH,
    PRICES_PATH,
    TICKS_PATH,
    MeritzCredentials,
    MeritzSession,
    normalize_mac,
    session_for,
)
from services.market_data.provider import (
    BarsQuery,
    BarsResult,
    InstrumentNotFoundError,
    InvalidBarsQueryError,
    ProviderDataError,
)

SEOUL = ZoneInfo("Asia/Seoul")
PAGE_CAP = 40
# One daily request covers at most this many calendar days; the spec names no cap.
DAILY_CHUNK_DAYS = 366
SOURCE_ID = "meritz-us"


@dataclass(frozen=True)
class MeritzConfig:
    app_key: str
    app_secret: str
    mac_address: str
    base_url: str = BASE_URL
    symbols: tuple[tuple[str, str], ...] = (("SPY", "ARCX"), ("AAPL", "XNAS"), ("MSFT", "XNAS"))
    sessions: frozenset[TradingSessionName] = frozenset({"regular"})
    finality_minutes: int = 15
    delayed: bool = False
    timestamp_is_bar_end: bool = False

    def __repr__(self) -> str:
        return f"MeritzConfig(base_url={self.base_url!r}, symbols={self.symbols!r})"

    @property
    def credentials(self) -> MeritzCredentials:
        return MeritzCredentials(self.app_key, self.app_secret, self.mac_address, self.base_url)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> MeritzConfig:
        values = os.environ if env is None else env
        key, secret, mac = require(
            values, "SIW_MERITZ_APP_KEY", "SIW_MERITZ_APP_SECRET", "SIW_MERITZ_MAC_ADDRESS"
        )
        # No demo domain is documented, so demo account keys are refused.
        account_environment(values, "SIW_MERITZ_ENVIRONMENT", None, "Meritz Securities")
        try:
            # An adapter built only to list instruments gets placeholders and never calls.
            mac = mac if mac == LISTING_ONLY else normalize_mac(mac)
        except BrokerConfigError as exc:
            raise BrokerConfigError(
                "SIW_MERITZ_MAC_ADDRESS must be 12 hexadecimal characters"
            ) from exc
        symbols = parse_symbols(
            values.get("SIW_MERITZ_SYMBOLS", "SPY:ARCX,AAPL:XNAS,MSFT:XNAS"), "SIW_MERITZ_SYMBOLS"
        )
        for _symbol, venue in symbols:
            if venue not in EXCHANGE_CODES:
                raise BrokerConfigError(
                    f"Meritz supports MICs {', '.join(EXCHANGE_CODES)}; got {venue}"
                )
        return cls(
            app_key=key,
            app_secret=secret,
            mac_address=mac,
            base_url=values.get("SIW_MERITZ_BASE_URL", cls.base_url).rstrip("/"),
            symbols=symbols,
            sessions=parse_sessions(
                values.get("SIW_MERITZ_SESSIONS", "regular"), "SIW_MERITZ_SESSIONS"
            ),
            finality_minutes=int(values.get("SIW_MERITZ_FINALITY_MINUTES", "15")),
            delayed=values.get("SIW_MERITZ_DELAYED", "false").lower() == "true",
            timestamp_is_bar_end=values.get("SIW_MERITZ_TIMESTAMP_IS_BAR_END", "false").lower()
            == "true",
        )


def listing_of(instrument_id: str) -> tuple[str, str] | None:
    """(symbol, quote-API market code) of a US listing on a venue Meritz quotes."""
    parsed = parse_instrument(instrument_id)
    if parsed is None or parsed.market != "US" or parsed.venue not in EXCHANGE_CODES:
        return None
    return parsed.symbol, EXCHANGE_CODES[parsed.venue]


class MeritzMarketDataProvider:
    source_id = SOURCE_ID

    def __init__(
        self,
        config: MeritzConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._calendar = UsEquityCalendar("XNAS")
        self._transport = transport
        self._session: MeritzSession | None = None
        self._instruments = {f"us:{s.lower()}:{v.lower()}:2026": (s, v) for s, v in config.symbols}
        self.last_raw_sample: dict[str, Any] | None = None
        self.identity = SourceIdentity(
            source_id=self.source_id,
            feed_scope="meritz-us-delayed" if config.delayed else "meritz-us",
            dataset_revision="meritz-us-r1",
            adjustment="unknown",  # the spec does not say whether minute prices are adjusted
            covered_sessions=config.sessions,
            publication_delay_minutes=15 if config.delayed else 0,
            finality_minutes=config.finality_minutes,
        )

    @property
    def session(self) -> MeritzSession:
        # Built on first use: listing instruments never needs the key or the MAC address.
        if self._session is None:
            self._session = session_for(self.config.credentials, transport=self._transport)
        return self._session

    def capabilities(self) -> dict[str, Any]:
        return capabilities(self.source_id, self.identity.feed_scope, self._now())

    def instruments(self) -> list[dict[str, Any]]:
        return [
            instrument_listing(
                s, v, self.source_id, self.identity.dataset_revision, "meritz-config"
            )
            for s, v in self.config.symbols
        ]

    def get_bars(self, query: BarsQuery) -> BarsResult:
        now = self._now()
        validate_minute_query(query, now)
        if query.instrument_id not in self._instruments:
            raise InstrumentNotFoundError("instrument is not in SIW_MERITZ_SYMBOLS")
        symbol, venue = self._instruments[query.instrument_id]
        records = self._latest(symbol, EXCHANGE_CODES[venue]) if query.start_utc < now else []
        if records and self.last_raw_sample is None:
            self.last_raw_sample = dict(records[0])
        raw = [self._minute(record) for record in records]
        # A full page means older bars exist but cannot be requested from this source.
        floor = min(item.start_utc for item in raw) if len(raw) >= PAGE_CAP else None
        return build_result(query, raw, self.identity, self._calendar, now, history_start_utc=floor)

    def get_adjusted_daily_bars(self, query: BarsQuery, adjustment: str) -> BarsResult:
        """Daily bars adjusted for splits (`mod_stpr_cls_code` "0"); never cached, since
        adjusted history changes when a new split is applied. Dividends are not adjusted."""
        if adjustment not in ADJUSTED:
            raise InvalidBarsQueryError("adjustment must be split_adjusted or fully_adjusted")
        if adjustment != "split_adjusted":
            raise InvalidBarsQueryError(
                "Meritz's adjusted daily prices reflect splits only; use split_adjusted"
            )
        return self.get_daily_bars(query, adjustment)

    def get_daily_bars(self, query: BarsQuery, adjustment: str = "raw") -> BarsResult:
        """Daily bars (prices as traded unless asked) for sessions opening in the range."""
        now = self._now()
        validate_daily_query(query, "1d", now)
        listing = listing_of(query.instrument_id)
        if listing is None:
            raise InstrumentNotFoundError("not a US listing on a venue Meritz quotes")
        symbol, market = listing
        try:
            sessions = sessions_in_range(self._calendar, query.start_utc, query.end_utc)
        except CalendarError as exc:
            raise InvalidBarsQueryError(str(exc)) from exc
        raw: dict[date, RawDay] = {}
        if sessions and sessions[0].regular_open_utc < now:
            cursor, last = sessions[0].session_date, sessions[-1].session_date
            while cursor <= last:
                stop = min(last, cursor + timedelta(days=DAILY_CHUNK_DAYS - 1))
                for row in self._days(symbol, market, cursor, stop, adjustment != "raw"):
                    day = self._day(row)
                    if day is None or not cursor <= day.session_date <= stop:
                        continue
                    if day.session_date in raw:
                        raise ProviderDataError("Meritz returned two daily bars for one date")
                    raw[day.session_date] = day
                cursor = stop + timedelta(days=1)
        source = DailySource(
            source_id=self.source_id,
            feed_scope=self.identity.feed_scope,
            dataset_revision=f"{self.identity.dataset_revision}:1d"
            if adjustment == "raw"
            else f"{self.source_id}-adjusted-r1:1d:split:asof-{now.date()}",
            adjustment=adjustment,
            publication_delay_minutes=self.identity.publication_delay_minutes,
            finality_minutes=self.config.finality_minutes,
        )
        return build_daily_result(query, raw, source, self._calendar, now)

    def _quote_params(self, symbol: str, market: str) -> dict[str, str]:
        return {
            "mrkt_div_code": "OV",
            "mrkt_cls_code": market,
            "iscd": symbol,
            "dely_rltm_cls_code": "1" if self.config.delayed else "0",
        }

    def _latest(self, symbol: str, market: str) -> list[dict[str, Any]]:
        params = {**self._quote_params(symbol, market), "hour_cls_code": "60"}
        data = self.session.get(MINUTES_PATH, params).get("data")
        return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []

    def _days(
        self, symbol: str, market: str, start: date, stop: date, adjusted: bool
    ) -> list[dict[str, Any]]:
        params = {
            **self._quote_params(symbol, market),
            "mod_stpr_cls_code": "0" if adjusted else "1",
            "from": start.strftime("%Y%m%d"),
            "to": stop.strftime("%Y%m%d"),
        }
        data = self.session.get(DAYS_PATH, params).get("data")
        return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []

    def _day(self, row: dict[str, Any]) -> RawDay | None:
        try:
            day = datetime.strptime(str(row["date"]), "%Y%m%d").date()
        except (KeyError, ValueError) as exc:
            raise ProviderDataError("Meritz returned a daily bar without a valid date") from exc
        volume = decimal_field(row, "acml_vol")
        # A day without trades (seen in the spec's example for the day not yet traded,
        # with every price equal to the previous close) is not a bar.
        if volume == 0:
            return None
        return RawDay(
            day,
            decimal_field(row, "oprc"),
            decimal_field(row, "hprc"),
            decimal_field(row, "lprc"),
            decimal_field(row, "prpr"),
            volume,
        )

    def _minute(self, record: dict[str, Any]) -> RawMinute:
        try:
            local = datetime.strptime(
                f"{record['korea_date']}{record['korea_hour']}", "%Y%m%d%H%M%S"
            )
        except (KeyError, ValueError) as exc:
            raise ProviderDataError(
                "Meritz returned a bar without a valid Korean date/time"
            ) from exc
        start = local.replace(tzinfo=SEOUL).astimezone(timezone.utc)
        if self.config.timestamp_is_bar_end:
            start -= timedelta(minutes=1)
        return RawMinute(
            start_utc=start,
            open=decimal_field(record, "oprc"),
            high=decimal_field(record, "hprc"),
            low=decimal_field(record, "lprc"),
            close=decimal_field(record, "prpr"),
            volume=decimal_field(record, "cntg_vol"),
        )


# --- quote ---------------------------------------------------------------------------

TRADE_COUNT = 30


def _price(value: object) -> str:
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except InvalidOperation as exc:
        raise ProviderDataError("Meritz returned a quote with an invalid number") from exc
    if not number.is_finite() or number < 0:
        raise ProviderDataError("Meritz returned a quote with an invalid number")
    return format(number, "f")


def _utc_ms(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def tick_times(clocks: list[str], now: datetime) -> list[datetime | None]:
    """Instants of Korean clock times listed newest first, none after `now`.

    The tick list carries times without a date: the newest is the latest instant with
    its clock time not after `now`, and each older one the latest not after the next
    newer tick. A clock that cannot be read gets None, and the walk goes on.
    """
    out: list[datetime | None] = []
    bound = now.astimezone(SEOUL)
    for clock in clocks:
        try:
            parsed = datetime.strptime(clock, "%H%M%S").time()
        except ValueError:
            out.append(None)
            continue
        moment = datetime.combine(bound.date(), parsed, SEOUL)
        if moment > bound:
            moment -= timedelta(days=1)
        out.append(moment)
        bound = moment
    return out


def market_quote(
    session: MeritzSession,
    instrument_id: str,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    delayed: bool = False,
) -> dict[str, Any]:
    """Last trade, order book and recent trades of a US listing, read live from Meritz.

    The current-price reply carries no time, so the last price is taken from the newest
    tick, which does. The order book carries no time either: its `at_utc` is when this
    API read it. US listings have no daily price limits.
    """
    listing = listing_of(instrument_id)
    if listing is None:
        raise InvalidBarsQueryError("not a US listing on a venue Meritz quotes")
    symbol, market = listing
    params = {
        "mrkt_div_code": "OV",
        "mrkt_cls_code": market,
        "iscd": symbol,
        "dely_rltm_cls_code": "1" if delayed else "0",
    }
    observed = now()
    price = session.get(PRICES_PATH, params).get("data")
    price = price if isinstance(price, dict) else {}
    # A wrong code answers "0000" with an empty iscd and zero prices (spec).
    if not str(price.get("iscd") or "").strip():
        raise InvalidBarsQueryError(f"Meritz has no quote for {symbol}")
    currency = str(price.get("crnc_code") or "USD").strip()
    if currency != "USD":
        raise ProviderDataError(f"Meritz returned a {currency} quote for a US listing")
    ticks_data = session.get(TICKS_PATH, params).get("data")
    listed = ticks_data if isinstance(ticks_data, list) else []
    rows = [row for row in listed if isinstance(row, dict)][:TRADE_COUNT]
    times = tick_times([str(row.get("korea_hour") or "") for row in rows], observed)
    trades = [
        {
            "price": _price(row.get("prpr")),
            "volume": _price(row.get("cntg_vol")),
            "at_utc": _utc_ms(moment),
        }
        for row, moment in zip(rows, times, strict=True)
        if moment is not None
    ]
    book_data = session.get(ORDERBOOK_PATH, params).get("data")
    book = book_data if isinstance(book_data, dict) else {}

    def levels(side: str, volume: str) -> list[dict[str, str]]:
        out = []
        for level in range(1, 11):
            level_price = _price(book.get(f"{side}{level}", "0"))
            if Decimal(level_price) == 0:
                continue  # an empty level is sent as 0
            out.append({"price": level_price, "volume": _price(book.get(f"{volume}{level}", "0"))})
        return out

    asks, bids = levels("askp", "askp_rsqn"), levels("bidp", "bidp_rsqn")
    observed_text = (
        observed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )
    document: dict[str, Any] = {
        "kind": "market_quote",
        "schema_version": "0.1",
        "instrument_id": instrument_id,
        "symbol": symbol,
        "source_id": "meritz-us-delayed" if delayed else SOURCE_ID,
        "currency": "USD",
        "observed_at_utc": observed_text,
        "last": {"price": trades[0]["price"], "at_utc": trades[0]["at_utc"]} if trades else None,
        # Delayed quotes send every level as 0: no book rather than an empty one.
        "orderbook": {"at_utc": _utc_ms(observed), "asks": asks, "bids": bids}
        if asks or bids
        else None,
        "trades": trades,
        "price_limits": None,
    }
    try:
        validate_document(document)
    except ValueError as exc:
        raise ProviderDataError(f"Meritz quote violates the contract: {exc}") from exc
    return document
