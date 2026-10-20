"""한국투자증권 (KIS Developers) US minute-bar adapter (read-only, from official docs).

Source of the spec: github.com/koreainvestment/open-trading-api (해외주식분봉조회,
v1_해외주식-030, TR HHDFS76950200). Unverified until checked with the user's own keys:
whether a first request may jump back with KEYB, whether `xhms` is the bar start, and
which sessions NAS/NYS/AMS minute bars include.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

import httpx

from services.market_data.broker_common import (
    MAX_QUERY_MINUTES,
    AllowlistedClient,
    BrokerConfigError,
    RawMinute,
    SourceIdentity,
    TokenCache,
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
from services.market_data.calendar import TradingSessionName, UsEquityCalendar
from services.market_data.provider import (
    BarsQuery,
    BarsResult,
    InstrumentNotFoundError,
    InvalidBarsQueryError,
    ProviderAuthError,
    ProviderDataError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)

NEW_YORK = ZoneInfo("America/New_York")
TOKEN_PATH = "/oauth2/tokenP"
MINUTE_PATH = "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice"
MINUTE_TR_ID = "HHDFS76950200"
PAGE_SIZE = 120
EXCHANGE_CODES = {"XNAS": "NAS", "XNYS": "NYS", "ARCX": "AMS", "XASE": "AMS"}
TOKEN_ERRORS = {"EGW00121", "EGW00123"}
RATE_LIMIT_ERROR = "EGW00201"


# Demo (모의투자) domain, "vps" in the official open-trading-api kis_devlp.yaml.
DEMO_BASE_URL = "https://openapivts.koreainvestment.com:29443"


@dataclass(frozen=True)
class KisConfig:
    app_key: str
    app_secret: str
    base_url: str = "https://openapi.koreainvestment.com:9443"
    symbols: tuple[tuple[str, str], ...] = (("SPY", "ARCX"), ("AAPL", "XNAS"), ("MSFT", "XNAS"))
    sessions: frozenset[TradingSessionName] = frozenset({"regular"})
    finality_minutes: int = 15
    timestamp_is_bar_end: bool = False
    environment: Literal["real", "demo"] = "real"

    def __repr__(self) -> str:
        return (
            f"KisConfig(base_url={self.base_url!r}, symbols={self.symbols!r}, "
            f"environment={self.environment!r})"
        )

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> KisConfig:
        values = os.environ if env is None else env
        key, secret = require(values, "SIW_KIS_APP_KEY", "SIW_KIS_APP_SECRET")
        symbols = parse_symbols(
            values.get("SIW_KIS_SYMBOLS", "SPY:ARCX,AAPL:XNAS,MSFT:XNAS"), "SIW_KIS_SYMBOLS"
        )
        for _symbol, venue in symbols:
            if venue not in EXCHANGE_CODES:
                raise BrokerConfigError(
                    f"KIS supports MICs {', '.join(EXCHANGE_CODES)}; got {venue}"
                )
        environment, demo_url = account_environment(
            values, "SIW_KIS_ENVIRONMENT", DEMO_BASE_URL, "KIS"
        )
        return cls(
            app_key=key,
            app_secret=secret,
            base_url=demo_url or values.get("SIW_KIS_BASE_URL", cls.base_url).rstrip("/"),
            environment=environment,
            symbols=symbols,
            sessions=parse_sessions(values.get("SIW_KIS_SESSIONS", "regular"), "SIW_KIS_SESSIONS"),
            finality_minutes=int(values.get("SIW_KIS_FINALITY_MINUTES", "15")),
            timestamp_is_bar_end=values.get("SIW_KIS_TIMESTAMP_IS_BAR_END", "false").lower()
            == "true",
        )


class KisMarketDataProvider:
    source_id = "kis-us"

    def __init__(
        self,
        config: KisConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._calendar = UsEquityCalendar("XNAS")
        self._http = AllowlistedClient(
            config.base_url,
            [("POST", TOKEN_PATH), ("GET", MINUTE_PATH)],
            transport=transport,
            min_interval_seconds=0.06,  # documented REST limit: 20 calls per second
        )
        # Every issuance notifies the account holder (KakaoTalk), so the token stays in
        # memory for its lifetime and is only reissued on expiry or rejection.
        self._token = TokenCache(self._issue_token)
        self._instruments = {f"us:{s.lower()}:{v.lower()}:2026": (s, v) for s, v in config.symbols}
        self.last_raw_sample: dict[str, Any] | None = None
        self.identity = SourceIdentity(
            source_id=self.source_id,
            # A demo server's data is kept apart from the real one's.
            feed_scope="kis-us" + ("-demo" if config.environment == "demo" else ""),
            dataset_revision="kis-us-r1" + ("-demo" if config.environment == "demo" else ""),
            adjustment="unknown",  # the minute-bar spec does not say whether prices are adjusted
            covered_sessions=config.sessions,
            publication_delay_minutes=0,  # documented as free real-time for US quotes
            finality_minutes=config.finality_minutes,
        )

    def capabilities(self) -> dict[str, Any]:
        return capabilities(self.source_id, "kis-us", self._now())

    def instruments(self) -> list[dict[str, Any]]:
        return [
            instrument_listing(s, v, self.source_id, self.identity.dataset_revision, "kis-config")
            for s, v in self.config.symbols
        ]

    def get_bars(self, query: BarsQuery) -> BarsResult:
        now = self._now()
        validate_minute_query(query, now)
        if query.instrument_id not in self._instruments:
            raise InstrumentNotFoundError("instrument is not in SIW_KIS_SYMBOLS")
        symbol, venue = self._instruments[query.instrument_id]
        raw = self._fetch(symbol, EXCHANGE_CODES[venue], query) if query.start_utc < now else []
        return build_result(query, raw, self.identity, self._calendar, now)

    def _issue_token(self) -> tuple[str, float]:
        response = self._http.request(
            "POST",
            TOKEN_PATH,
            json={
                "grant_type": "client_credentials",
                "appkey": self.config.app_key,
                "appsecret": self.config.app_secret,
            },
        )
        if response.status_code >= 400:
            raise ProviderAuthError(f"KIS refused to issue a token ({response.status_code})")
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise ProviderAuthError("KIS returned no access token")
        return str(token), float(payload.get("expires_in", 86_400))

    def _fetch(self, symbol: str, exchange: str, query: BarsQuery) -> list[RawMinute]:
        bars: list[RawMinute] = []
        keyb = (query.end_utc - timedelta(minutes=1)).astimezone(NEW_YORK).strftime("%Y%m%d%H%M%S")
        for _page in range(MAX_QUERY_MINUTES // PAGE_SIZE + 2):
            records = self._page(symbol, exchange, keyb)
            if not records:
                break
            if self.last_raw_sample is None:
                self.last_raw_sample = dict(records[0])
            page = [self._minute(record) for record in records]
            bars.extend(page)
            oldest = min(item.start_utc for item in page)
            if oldest <= query.start_utc or len(records) < PAGE_SIZE:
                break
            keyb = (oldest - timedelta(minutes=1)).astimezone(NEW_YORK).strftime("%Y%m%d%H%M%S")
        return bars

    def _page(self, symbol: str, exchange: str, keyb: str) -> list[dict[str, Any]]:
        params = {
            "AUTH": "",
            "EXCD": exchange,
            "SYMB": symbol,
            "NMIN": "1",
            "PINC": "1",
            "NEXT": "1",
            "NREC": str(PAGE_SIZE),
            "FILL": "",
            "KEYB": keyb,
        }
        for attempt in range(2):
            headers = {
                "content-type": "application/json",
                "authorization": f"Bearer {self._token.get(refresh=attempt > 0)}",
                "appkey": self.config.app_key,
                "appsecret": self.config.app_secret,
                "tr_id": MINUTE_TR_ID,
                "custtype": "P",
            }
            response = self._http.request("GET", MINUTE_PATH, params=params, headers=headers)
            try:
                payload = response.json()
            except ValueError as exc:
                if response.status_code >= 500:
                    raise ProviderUnavailableError(
                        f"KIS is unavailable ({response.status_code})"
                    ) from exc
                raise ProviderDataError("KIS returned a response that is not JSON") from exc
            code = str(payload.get("msg_cd", ""))
            if code in TOKEN_ERRORS and attempt == 0:
                continue  # expired or invalid token: reissue once
            if code == RATE_LIMIT_ERROR:
                raise ProviderRateLimitError("KIS rate limit reached; retry later")
            if response.status_code in (401, 403) or code in TOKEN_ERRORS:
                raise ProviderAuthError(f"KIS rejected the request ({response.status_code} {code})")
            if response.status_code >= 500:
                raise ProviderUnavailableError(f"KIS is unavailable ({response.status_code})")
            if str(payload.get("rt_cd", "")) != "0":
                message = str(payload.get("msg1", ""))[:200]
                raise InvalidBarsQueryError(f"KIS refused the query ({code}): {message}")
            return list(payload.get("output2") or [])
        raise ProviderAuthError("KIS rejected the reissued token")

    def _minute(self, record: dict[str, Any]) -> RawMinute:
        try:
            local = datetime.strptime(f"{record['xymd']}{record['xhms']}", "%Y%m%d%H%M%S")
        except (KeyError, ValueError) as exc:
            raise ProviderDataError("KIS returned a bar without a valid local date/time") from exc
        start = local.replace(tzinfo=NEW_YORK).astimezone(timezone.utc)
        if self.config.timestamp_is_bar_end:
            start -= timedelta(minutes=1)
        return RawMinute(
            start_utc=start,
            open=decimal_field(record, "open"),
            high=decimal_field(record, "high"),
            low=decimal_field(record, "low"),
            close=decimal_field(record, "last"),
            volume=decimal_field(record, "evol"),
        )
