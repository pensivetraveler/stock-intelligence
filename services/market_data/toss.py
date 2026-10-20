"""토스증권 Open API US minute and daily bar adapter (read-only, from official docs).

Spec: openapi.tossinvest.com (GET /api/v1/candles, Candle, CandlePageResponse, overview).
Documented facts used here: a candle `timestamp` is the bar END ([t-1m, t)); newest
first; `count` <= 200; page with `result.nextBefore`; `adjusted` defaults to true (we
request false); OAuth2 client credentials with one valid token per client (a new token
revokes the old); calls from IPs outside the allowlist get 403 `ip-not-allowed`; US data
comes from some venues only, not the NBBO; minutes without trades are omitted. Data may be
used only for the account holder's own trading and must not be passed to third parties.

Requests go through the process-wide `TossSession` of the key (toss_session.py), so the
chart, quotes and account views share one token. US daily candles (`interval=1d`) are
stamped at midnight New York of their session date, written in Korean time (live check
2026-10-20, AAPL: "2026-10-19T13:00:00+09:00" is the 10-19 session), so the session date
is read in New York time.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal

import httpx

from services.market_data.broker_common import (
    MAX_QUERY_MINUTES,
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
    utc_text,
    validate_minute_query,
)
from services.market_data.calendar import (
    NEW_YORK,
    SEOUL,
    CalendarError,
    ExchangeCalendar,
    TradingSessionName,
)
from services.market_data.daily import (
    DailySource,
    RawDay,
    build_daily_result,
    sessions_in_range,
    validate_daily_query,
)
from services.market_data.instruments import parse_instrument
from services.market_data.provider import (
    BarsQuery,
    BarsResult,
    InstrumentNotFoundError,
    InvalidBarsQueryError,
    ProviderDataError,
)
from services.market_data.toss_session import TossCredentials, session_for

CANDLES_PATH = "/api/v1/candles"
PAGE_SIZE = 200
# Daily pages: 40 years of sessions at 200 candles a page.
DAILY_PAGES = 52


@dataclass(frozen=True)
class TossConfig:
    client_id: str
    client_secret: str
    base_url: str = "https://openapi.tossinvest.com"
    symbols: tuple[tuple[str, str], ...] = (("SPY", "ARCX"), ("AAPL", "XNAS"), ("MSFT", "XNAS"))
    sessions: frozenset[TradingSessionName] = frozenset({"regular"})
    finality_minutes: int = 15
    # US listings, or Korean listings (KRX and NXT trading combined by Toss).
    market: Literal["US", "KR"] = "US"

    def __repr__(self) -> str:
        return (
            f"TossConfig(base_url={self.base_url!r}, symbols={self.symbols!r}, "
            f"market={self.market!r})"
        )

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> TossConfig:
        values = os.environ if env is None else env
        client_id, secret = require(values, "SIW_TOSS_CLIENT_ID", "SIW_TOSS_CLIENT_SECRET")
        # No demo domain is documented, so demo account keys are refused.
        account_environment(values, "SIW_TOSS_ENVIRONMENT", None, "Toss Securities")
        return cls(
            client_id=client_id,
            client_secret=secret,
            base_url=values.get("SIW_TOSS_BASE_URL", cls.base_url).rstrip("/"),
            symbols=parse_symbols(
                values.get("SIW_TOSS_SYMBOLS", "SPY:ARCX,AAPL:XNAS,MSFT:XNAS"), "SIW_TOSS_SYMBOLS"
            ),
            sessions=parse_sessions(
                values.get("SIW_TOSS_SESSIONS", "regular"), "SIW_TOSS_SESSIONS"
            ),
            finality_minutes=int(values.get("SIW_TOSS_FINALITY_MINUTES", "15")),
        )


class TossMarketDataProvider:
    def __init__(
        self,
        config: TossConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self._now = now or (lambda: datetime.now(timezone.utc))
        korean = config.market == "KR"
        self.source_id = "toss-kr" if korean else "toss-us"
        self._currency = "KRW" if korean else "USD"
        self._calendar = ExchangeCalendar("XKRX" if korean else "XNAS")
        self._zone = SEOUL if korean else NEW_YORK
        feed_scope = "toss-kr-krx-nxt-integrated" if korean else "toss-us-partial-venues"
        self.session = session_for(
            TossCredentials(config.client_id, config.client_secret, config.base_url),
            transport=transport,
        )
        self._instruments = (
            {}
            if korean
            else {f"us:{s.lower()}:{v.lower()}:2026": (s, v) for s, v in config.symbols}
        )
        self.last_raw_sample: dict[str, Any] | None = None
        self.identity = SourceIdentity(
            source_id=self.source_id,
            feed_scope=feed_scope,
            dataset_revision=f"{self.source_id}-raw-r1",
            adjustment="raw",  # adjusted=false is requested explicitly
            # Toss's Korean minute candles cover 08:00-20:00 KST (checked 2026-10-20).
            covered_sessions=frozenset({"pre", "regular", "post"}) if korean else config.sessions,
            publication_delay_minutes=0,
            finality_minutes=config.finality_minutes,
        )

    def capabilities(self) -> dict[str, Any]:
        # Live check 2026-10-19: SPY 30/30 minutes with the user's key.
        return capabilities(
            self.source_id,
            self.identity.feed_scope,
            self._now(),
            verification_status="integration_tested",
        )

    def instruments(self) -> list[dict[str, Any]]:
        return [
            instrument_listing(s, v, self.source_id, self.identity.dataset_revision, "toss-config")
            for s, v in self.config.symbols
        ]

    def symbol_for(self, instrument_id: str) -> str | None:
        """Ticker or KRX code of an instrument of this adapter's market."""
        configured = self._instruments.get(instrument_id)
        if configured is not None:
            return configured[0]
        parsed = parse_instrument(instrument_id)
        if parsed is None or parsed.market != self.config.market:
            return None
        return parsed.symbol

    def get_bars(self, query: BarsQuery) -> BarsResult:
        now = self._now()
        validate_minute_query(query, now)
        symbol = self.symbol_for(query.instrument_id)
        if symbol is None:
            raise InstrumentNotFoundError(f"not a listed {self.config.market} equity ID")
        raw = self._fetch(symbol, query) if query.start_utc < now else []
        return build_result(query, raw, self.identity, self._calendar, now)

    def get_daily_bars(self, query: BarsQuery) -> BarsResult:
        """Daily bars (Toss `1d`, unadjusted) for sessions opening in the range."""
        now = self._now()
        validate_daily_query(query, "1d", now)
        symbol = self.symbol_for(query.instrument_id)
        if symbol is None:
            raise InstrumentNotFoundError(f"not a listed {self.config.market} equity ID")
        try:
            sessions = sessions_in_range(self._calendar, query.start_utc, query.end_utc)
        except CalendarError as exc:
            raise InvalidBarsQueryError(str(exc)) from exc
        raw: dict[date, RawDay] = {}
        if sessions and sessions[0].regular_open_utc < now:
            first = sessions[0].session_date
            # Inclusive upper bound: the end of the last session's date in New York.
            before: str | None = utc_text(
                datetime.combine(sessions[-1].session_date, time(23, 59), self._zone)
            )
            for _page in range(DAILY_PAGES):
                if before is None:
                    break
                result = self._candles(symbol, "1d", before)
                candles = list(result.get("candles") or [])
                for candle in candles:
                    day = self._day(candle)
                    if day.session_date in raw:
                        raise ProviderDataError("Toss returned two daily candles for one date")
                    raw[day.session_date] = day
                if not candles or min(raw) <= first:
                    break
                next_before = result.get("nextBefore")
                before = str(next_before) if next_before else None
        source = DailySource(
            source_id=self.source_id,
            feed_scope=self.identity.feed_scope,
            dataset_revision=f"{self.identity.dataset_revision}:1d",
            adjustment="raw",
            publication_delay_minutes=0,
            finality_minutes=self.config.finality_minutes,
            # Korean daily candles cover the whole KRX+NXT day; US ones the regular session.
            span="all_day" if self.config.market == "KR" else "regular",
        )
        return build_daily_result(query, raw, source, self._calendar, now)

    def _fetch(self, symbol: str, query: BarsQuery) -> list[RawMinute]:
        bars: list[RawMinute] = []
        before: str | None = utc_text(query.end_utc)  # candles end at or before this time
        for _page in range(MAX_QUERY_MINUTES // PAGE_SIZE + 2):
            if before is None:
                break
            result = self._candles(symbol, "1m", before)
            candles = list(result.get("candles") or [])
            if candles and self.last_raw_sample is None:
                self.last_raw_sample = dict(candles[0])
            page = [self._minute(candle) for candle in candles]
            bars.extend(page)
            if not page or min(item.start_utc for item in page) <= query.start_utc:
                break
            next_before = result.get("nextBefore")
            before = str(next_before) if next_before else None
        return bars

    def _candles(self, symbol: str, interval: str, before: str) -> dict[str, Any]:
        result = self.session.get(
            CANDLES_PATH,
            params={
                "symbol": symbol,
                "interval": interval,
                "count": str(PAGE_SIZE),
                "before": before,
                "adjusted": "false",
            },
        )
        if not isinstance(result, dict):
            raise ProviderDataError("Toss returned candles without a result object")
        return result

    def _day(self, candle: dict[str, Any]) -> RawDay:
        try:
            stamp = datetime.fromisoformat(str(candle["timestamp"]).replace("Z", "+00:00"))
        except (KeyError, ValueError) as exc:
            raise ProviderDataError("Toss returned a daily candle without a timestamp") from exc
        self._check_currency(candle)
        if stamp.tzinfo is None:
            raise ProviderDataError("Toss returned a daily candle timestamp without an offset")
        return RawDay(
            # Midnight of the session date in the exchange's zone (US candles write it in
            # KST, Korean ones are already KST).
            stamp.astimezone(self._zone).date(),
            decimal_field(candle, "openPrice"),
            decimal_field(candle, "highPrice"),
            decimal_field(candle, "lowPrice"),
            decimal_field(candle, "closePrice"),
            decimal_field(candle, "volume"),
        )

    def _check_currency(self, candle: dict[str, Any]) -> None:
        currency = candle.get("currency")
        if currency not in (None, self._currency):
            raise ProviderDataError(
                f"Toss returned a {currency} candle for a {self.config.market} instrument"
            )

    def _minute(self, candle: dict[str, Any]) -> RawMinute:
        try:
            end = datetime.fromisoformat(str(candle["timestamp"]).replace("Z", "+00:00"))
        except (KeyError, ValueError) as exc:
            raise ProviderDataError("Toss returned a candle without a valid timestamp") from exc
        if end.tzinfo is None:
            raise ProviderDataError("Toss returned a candle timestamp without an offset")
        self._check_currency(candle)
        return RawMinute(
            start_utc=end.astimezone(timezone.utc) - timedelta(minutes=1),
            open=decimal_field(candle, "openPrice"),
            high=decimal_field(candle, "highPrice"),
            low=decimal_field(candle, "lowPrice"),
            close=decimal_field(candle, "closePrice"),
            volume=decimal_field(candle, "volume"),
        )
