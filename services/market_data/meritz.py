"""메리츠증권 Open API US minute-bar adapter (read-only, from the official spec).

Spec: openapi.imeritz.com (JSON download /api-spec/download/json, generated 2026-10-17;
how-to page). Public beta 2026-10-12 to 10-25, general availability planned 2026-10-26.
Documented facts used here:
- `GET /market/v1/overseas/candles/minutes` (TR ohhts_eapi_005r) returns at most the
  latest 40 bars, newest first, with no date or paging parameter;
- bar times are `korea_date` + `korea_hour` in Korean time;
- every error comes back as HTTP 500, so success is judged by `rsp_cd == "0000"`;
  `EGW00200` means the per-second limit was exceeded;
- every call needs a `mac_address` header (12 uppercase hex characters);
- data may be used only for the account holder's own investment, never passed on.
Not documented: whether `korea_hour` marks the bar start or end, and session coverage.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from services.market_data.broker_common import (
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

SEOUL = ZoneInfo("Asia/Seoul")
TOKEN_PATH = "/oauth2/token"
MINUTES_PATH = "/market/v1/overseas/candles/minutes"
PAGE_CAP = 40
EXCHANGE_CODES = {"XNAS": "OQ", "XNYS": "NY", "XASE": "AX", "ARCX": "AX"}
RATE_LIMIT_CODES = {"EGW00200"}
MAC_PATTERN = re.compile(r"^[0-9A-F]{12}$")


@dataclass(frozen=True)
class MeritzConfig:
    app_key: str
    app_secret: str
    mac_address: str
    base_url: str = "https://openapi.imeritz.com:9443"
    symbols: tuple[tuple[str, str], ...] = (("SPY", "ARCX"), ("AAPL", "XNAS"), ("MSFT", "XNAS"))
    sessions: frozenset[TradingSessionName] = frozenset({"regular"})
    finality_minutes: int = 15
    delayed: bool = False
    timestamp_is_bar_end: bool = False

    def __repr__(self) -> str:
        return f"MeritzConfig(base_url={self.base_url!r}, symbols={self.symbols!r})"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> MeritzConfig:
        values = os.environ if env is None else env
        key, secret, mac = require(
            values, "SIW_MERITZ_APP_KEY", "SIW_MERITZ_APP_SECRET", "SIW_MERITZ_MAC_ADDRESS"
        )
        # No demo domain is documented, so demo account keys are refused.
        account_environment(values, "SIW_MERITZ_ENVIRONMENT", None, "Meritz Securities")
        mac = mac.replace(":", "").replace("-", "").upper()
        if not MAC_PATTERN.fullmatch(mac):
            raise BrokerConfigError("SIW_MERITZ_MAC_ADDRESS must be 12 hexadecimal characters")
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


class MeritzMarketDataProvider:
    source_id = "meritz-us"

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
        # No published request limit beyond the EGW00200 error; space calls conservatively.
        self._http = AllowlistedClient(
            config.base_url,
            [("POST", TOKEN_PATH), ("GET", MINUTES_PATH)],
            transport=transport,
            min_interval_seconds=0.2,
        )
        self._token = TokenCache(self._issue_token, margin_seconds=60)
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

    def _issue_token(self) -> tuple[str, float]:
        response = self._http.request(
            "POST",
            TOKEN_PATH,
            data={
                "grant_type": "client_credentials",
                "client_id": self.config.app_key,
                "client_secret": self.config.app_secret,
                "scope": "login",
            },
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderAuthError(
                f"Meritz refused to issue a token ({response.status_code})"
            ) from exc
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if response.status_code >= 400 or not token:
            raise ProviderAuthError(f"Meritz refused to issue a token ({response.status_code})")
        return str(token), float(payload.get("expires_in", 86_400))

    def _latest(self, symbol: str, exchange: str) -> list[dict[str, Any]]:
        params = {
            "mrkt_div_code": "OV",
            "mrkt_cls_code": exchange,
            "iscd": symbol,
            "dely_rltm_cls_code": "1" if self.config.delayed else "0",
            "hour_cls_code": "60",
        }
        headers = {
            "authorization": f"Bearer {self._token.get()}",
            "mac_address": self.config.mac_address,
        }
        response = self._http.request("GET", MINUTES_PATH, params=params, headers=headers)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderUnavailableError(
                f"Meritz returned no JSON ({response.status_code})"
            ) from exc
        code = str(payload.get("rsp_cd", "")) if isinstance(payload, dict) else ""
        if code == "0000":
            return list(payload.get("data") or [])
        if code in RATE_LIMIT_CODES:
            raise ProviderRateLimitError("Meritz per-second limit reached; retry later")
        message = str(payload.get("rsp_msg", ""))[:200] if isinstance(payload, dict) else ""
        if response.status_code in (401, 403):
            raise ProviderAuthError(f"Meritz rejected the request ({response.status_code} {code})")
        if not code:
            raise ProviderUnavailableError(f"Meritz is unavailable ({response.status_code})")
        # Every error is HTTP 500 with a code; the spec gives no token-specific codes.
        raise InvalidBarsQueryError(f"Meritz refused the query ({code}): {message}")

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
