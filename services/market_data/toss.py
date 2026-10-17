"""토스증권 Open API US minute-bar adapter (read-only, from official docs).

Spec: openapi.tossinvest.com (GET /api/v1/candles, Candle, CandlePageResponse, overview).
Documented facts used here: a candle `timestamp` is the bar END ([t-1m, t)); newest
first; `count` <= 200; page with `result.nextBefore`; `adjusted` defaults to true (we
request false); OAuth2 client credentials with one valid token per client (a new token
revokes the old); calls from IPs outside the allowlist get 403 `ip-not-allowed`; US data
comes from some venues only, not the NBBO; minutes without trades are omitted. Data may be
used only for the account holder's own trading and must not be passed to third parties.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx

from services.market_data.broker_common import (
    MAX_QUERY_MINUTES,
    AllowlistedClient,
    RawMinute,
    SourceIdentity,
    TokenCache,
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

TOKEN_PATH = "/oauth2/token"
CANDLES_PATH = "/api/v1/candles"
PAGE_SIZE = 200
TOKEN_ERRORS = {"expired-token", "token-revoked", "invalid-token"}


@dataclass(frozen=True)
class TossConfig:
    client_id: str
    client_secret: str
    base_url: str = "https://openapi.tossinvest.com"
    symbols: tuple[tuple[str, str], ...] = (("SPY", "ARCX"), ("AAPL", "XNAS"), ("MSFT", "XNAS"))
    sessions: frozenset[TradingSessionName] = frozenset({"regular"})
    finality_minutes: int = 15

    def __repr__(self) -> str:
        return f"TossConfig(base_url={self.base_url!r}, symbols={self.symbols!r})"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> TossConfig:
        values = os.environ if env is None else env
        client_id, secret = require(values, "SIW_TOSS_CLIENT_ID", "SIW_TOSS_CLIENT_SECRET")
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
    source_id = "toss-us"

    def __init__(
        self,
        config: TossConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._calendar = UsEquityCalendar("XNAS")
        self._http = AllowlistedClient(
            config.base_url,
            [("POST", TOKEN_PATH), ("GET", CANDLES_PATH)],
            transport=transport,
            min_interval_seconds=0.06,  # documented MARKET_DATA_CHART group: 20 per second
        )
        self._token = TokenCache(self._issue_token)
        self._instruments = {f"us:{s.lower()}:{v.lower()}:2026": (s, v) for s, v in config.symbols}
        self.last_raw_sample: dict[str, Any] | None = None
        self.identity = SourceIdentity(
            source_id=self.source_id,
            feed_scope="toss-us-partial-venues",
            dataset_revision="toss-us-raw-r1",
            adjustment="raw",  # adjusted=false is requested explicitly
            covered_sessions=config.sessions,
            publication_delay_minutes=0,
            finality_minutes=config.finality_minutes,
        )

    def capabilities(self) -> dict[str, Any]:
        return capabilities(self.source_id, "toss-us-partial-venues", self._now())

    def instruments(self) -> list[dict[str, Any]]:
        return [
            instrument_listing(s, v, self.source_id, self.identity.dataset_revision, "toss-config")
            for s, v in self.config.symbols
        ]

    def get_bars(self, query: BarsQuery) -> BarsResult:
        now = self._now()
        validate_minute_query(query, now)
        if query.instrument_id not in self._instruments:
            raise InstrumentNotFoundError("instrument is not in SIW_TOSS_SYMBOLS")
        symbol, _venue = self._instruments[query.instrument_id]
        raw = self._fetch(symbol, query) if query.start_utc < now else []
        return build_result(query, raw, self.identity, self._calendar, now)

    def _issue_token(self) -> tuple[str, float]:
        response = self._http.request(
            "POST",
            TOKEN_PATH,
            data={
                "grant_type": "client_credentials",
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
            },
        )
        if response.status_code >= 400:
            raise ProviderAuthError(
                f"Toss refused to issue a token ({response.status_code}); check the keys and "
                "that this machine's IP is on the Open API allowlist"
            )
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise ProviderAuthError("Toss returned no access token")
        return str(token), float(payload.get("expires_in", 3_600))

    def _fetch(self, symbol: str, query: BarsQuery) -> list[RawMinute]:
        bars: list[RawMinute] = []
        before: str | None = utc_text(query.end_utc)  # candles end at or before this time
        for _page in range(MAX_QUERY_MINUTES // PAGE_SIZE + 2):
            if before is None:
                break
            result = self._page(symbol, before)
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

    def _page(self, symbol: str, before: str) -> dict[str, Any]:
        params = {
            "symbol": symbol,
            "interval": "1m",
            "count": str(PAGE_SIZE),
            "before": before,
            "adjusted": "false",
        }
        for attempt in range(2):
            headers = {
                "Authorization": f"Bearer {self._token.get(refresh=attempt > 0)}",
                "Accept": "application/json",
            }
            response = self._http.request("GET", CANDLES_PATH, params=params, headers=headers)
            try:
                payload = json.loads(response.text, parse_float=Decimal)
            except ValueError as exc:
                if response.status_code >= 500:
                    raise ProviderUnavailableError(
                        f"Toss is unavailable ({response.status_code})"
                    ) from exc
                raise ProviderDataError("Toss returned a response that is not JSON") from exc
            error = payload.get("error") if isinstance(payload, dict) else None
            code = str(error.get("code", "")) if isinstance(error, dict) else ""
            if response.status_code == 401 and code in TOKEN_ERRORS and attempt == 0:
                continue  # expired, or revoked by another process issuing a token: reissue once
            if response.status_code == 429:
                raise ProviderRateLimitError("Toss rate limit reached; retry later")
            if response.status_code in (401, 403):
                hint = (
                    " (this IP is not on the Open API allowlist)"
                    if code == "ip-not-allowed"
                    else ""
                )
                raise ProviderAuthError(
                    f"Toss rejected the request ({response.status_code} {code}){hint}"
                )
            if response.status_code >= 500:
                raise ProviderUnavailableError(f"Toss is unavailable ({response.status_code})")
            if response.status_code >= 400:
                message = str(error.get("message", ""))[:200] if isinstance(error, dict) else ""
                raise InvalidBarsQueryError(f"Toss refused the query ({code}): {message}")
            result = payload.get("result")
            if not isinstance(result, dict):
                raise ProviderDataError("Toss returned candles without a result object")
            return result
        raise ProviderAuthError("Toss rejected the reissued token")

    @staticmethod
    def _minute(candle: dict[str, Any]) -> RawMinute:
        try:
            end = datetime.fromisoformat(str(candle["timestamp"]).replace("Z", "+00:00"))
        except (KeyError, ValueError) as exc:
            raise ProviderDataError("Toss returned a candle without a valid timestamp") from exc
        if end.tzinfo is None:
            raise ProviderDataError("Toss returned a candle timestamp without an offset")
        currency = candle.get("currency")
        if currency not in (None, "USD"):
            raise ProviderDataError(f"Toss returned a {currency} candle for a US instrument")
        return RawMinute(
            start_utc=end.astimezone(timezone.utc) - timedelta(minutes=1),
            open=decimal_field(candle, "openPrice"),
            high=decimal_field(candle, "highPrice"),
            low=decimal_field(candle, "lowPrice"),
            close=decimal_field(candle, "closePrice"),
            volume=decimal_field(candle, "volume"),
        )
