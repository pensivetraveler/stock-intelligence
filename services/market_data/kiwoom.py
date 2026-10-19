"""키움증권 REST API US minute-bar adapter (read-only, from the official spec).

Spec: github.com/Kiwoom-Securities/Kiwoom-REST-API, kiwoom/_data/kiwoom_api_spec.json
(au10001 token, usa06011 미국주식 분 차트, POST /api/us/chart, paging via the cont-yn /
next-key headers, return_code 1700-1702 = request limits). The spec does not state the
time zone of `cntr_tm` or whether it marks the bar start, and its example shows two rows
with the same `cntr_tm`; duplicate minutes therefore fail loudly instead of being merged.

Live check, 2026-10-19 (user's key, AAPL 2026-10-16 13:30-13:45Z): read as New York time,
14 of 15 minute closes equal Alpaca SIP at the same bar start, and read as Seoul time no
bar falls in the window. So `cntr_tm` is New York time marking the bar start; that is the
default, and SIW_KIWOOM_TIME_ZONE remains as an override. Kiwoom lists NYSE Arca ETFs
(SPY) under stex_tp `NY`: `NA` answered 1903 "no such symbol".
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
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

TOKEN_PATH = "/oauth2/token"
CHART_PATH = "/api/us/chart"
# ARCX -> NY was confirmed live (SPY); XASE -> NA follows the spec and is unverified.
EXCHANGE_CODES = {"XNAS": "ND", "XNYS": "NY", "XASE": "NA", "ARCX": "NY"}
RATE_LIMIT_CODES = {1700, 1701, 1702}
NEW_YORK = ZoneInfo("America/New_York")
TimeZoneName = Literal["America/New_York", "Asia/Seoul"]


@dataclass(frozen=True)
class KiwoomConfig:
    app_key: str
    secret_key: str
    time_zone: TimeZoneName
    base_url: str = "https://api.kiwoom.com"
    symbols: tuple[tuple[str, str], ...] = (("SPY", "ARCX"), ("AAPL", "XNAS"), ("MSFT", "XNAS"))
    sessions: frozenset[TradingSessionName] = frozenset({"regular"})
    finality_minutes: int = 15

    def __repr__(self) -> str:
        return f"KiwoomConfig(base_url={self.base_url!r}, time_zone={self.time_zone!r})"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> KiwoomConfig:
        values = os.environ if env is None else env
        key, secret = require(values, "SIW_KIWOOM_APP_KEY", "SIW_KIWOOM_SECRET_KEY")
        zone = values.get("SIW_KIWOOM_TIME_ZONE", "America/New_York").strip()
        if zone not in ("America/New_York", "Asia/Seoul"):
            raise BrokerConfigError(
                "SIW_KIWOOM_TIME_ZONE must be America/New_York (confirmed live) or Asia/Seoul"
            )
        symbols = parse_symbols(
            values.get("SIW_KIWOOM_SYMBOLS", "SPY:ARCX,AAPL:XNAS,MSFT:XNAS"), "SIW_KIWOOM_SYMBOLS"
        )
        for _symbol, venue in symbols:
            if venue not in EXCHANGE_CODES:
                raise BrokerConfigError(
                    f"Kiwoom supports MICs {', '.join(EXCHANGE_CODES)}; got {venue}"
                )
        return cls(
            app_key=key,
            secret_key=secret,
            time_zone=zone,  # type: ignore[arg-type]
            base_url=values.get("SIW_KIWOOM_BASE_URL", cls.base_url).rstrip("/"),
            symbols=symbols,
            sessions=parse_sessions(
                values.get("SIW_KIWOOM_SESSIONS", "regular"), "SIW_KIWOOM_SESSIONS"
            ),
            finality_minutes=int(values.get("SIW_KIWOOM_FINALITY_MINUTES", "15")),
        )


class KiwoomMarketDataProvider:
    source_id = "kiwoom-us"

    def __init__(
        self,
        config: KiwoomConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._calendar = UsEquityCalendar("XNAS")
        self._zone = ZoneInfo(config.time_zone)
        # Request limits are not published; space calls conservatively.
        self._http = AllowlistedClient(
            config.base_url,
            [("POST", TOKEN_PATH), ("POST", CHART_PATH)],
            transport=transport,
            min_interval_seconds=0.25,
        )
        self._token = TokenCache(self._issue_token)
        self._instruments = {f"us:{s.lower()}:{v.lower()}:2026": (s, v) for s, v in config.symbols}
        self.last_raw_sample: dict[str, Any] | None = None
        self.identity = SourceIdentity(
            source_id=self.source_id,
            feed_scope="kiwoom-us",
            dataset_revision="kiwoom-us-raw-r1",
            adjustment="raw",  # upd_stkpc_tp=0 (no price adjustment) is requested explicitly
            covered_sessions=config.sessions,
            publication_delay_minutes=0,
            finality_minutes=config.finality_minutes,
        )

    def capabilities(self) -> dict[str, Any]:
        return capabilities(
            self.source_id, "kiwoom-us", self._now(), verification_status="integration_tested"
        )

    def instruments(self) -> list[dict[str, Any]]:
        return [
            instrument_listing(
                s, v, self.source_id, self.identity.dataset_revision, "kiwoom-config"
            )
            for s, v in self.config.symbols
        ]

    def get_bars(self, query: BarsQuery) -> BarsResult:
        now = self._now()
        validate_minute_query(query, now)
        if query.instrument_id not in self._instruments:
            raise InstrumentNotFoundError("instrument is not in SIW_KIWOOM_SYMBOLS")
        symbol, venue = self._instruments[query.instrument_id]
        raw = self._fetch(symbol, EXCHANGE_CODES[venue], query) if query.start_utc < now else []
        return build_result(query, raw, self.identity, self._calendar, now)

    def _issue_token(self) -> tuple[str, float]:
        response = self._http.request(
            "POST",
            TOKEN_PATH,
            headers={"api-id": "au10001", "content-type": "application/json;charset=UTF-8"},
            json={
                "grant_type": "client_credentials",
                "appkey": self.config.app_key,
                "secretkey": self.config.secret_key,
            },
        )
        if response.status_code >= 400:
            raise ProviderAuthError(f"Kiwoom refused to issue a token ({response.status_code})")
        payload = response.json()
        token = payload.get("token")
        if not token or int(payload.get("return_code", 0)) != 0:
            raise ProviderAuthError("Kiwoom returned no access token")
        lifetime = 3_600.0
        expires = str(payload.get("expires_dt", ""))
        try:
            expires_at = datetime.strptime(expires, "%Y%m%d%H%M%S").replace(
                tzinfo=ZoneInfo("Asia/Seoul")
            )
            lifetime = max(60.0, (expires_at - datetime.now(timezone.utc)).total_seconds())
        except ValueError:
            pass
        return str(token), lifetime

    def _fetch(self, symbol: str, exchange: str, query: BarsQuery) -> list[RawMinute]:
        body = {
            "stex_tp": exchange,
            "stk_cd": symbol,
            "strt_dt": query.start_utc.astimezone(NEW_YORK).strftime("%Y%m%d"),
            "tic_scope": "1",
            "upd_stkpc_tp": "0",
            "exrt_appl_tp": "0",
        }
        bars: list[RawMinute] = []
        continuation: tuple[str, str] | None = None
        for _page in range(MAX_QUERY_MINUTES // 100 + 5):
            records, continuation = self._page(body, continuation)
            if records and self.last_raw_sample is None:
                self.last_raw_sample = dict(records[0])
            page = [self._minute(record) for record in records]
            bars.extend(page)
            if (
                continuation is None
                or not page
                or min(item.start_utc for item in page) <= query.start_utc
            ):
                break
        return bars

    def _page(
        self, body: dict[str, str], continuation: tuple[str, str] | None
    ) -> tuple[list[dict[str, Any]], tuple[str, str] | None]:
        for attempt in range(2):
            headers = {
                "api-id": "usa06011",
                "authorization": f"Bearer {self._token.get(refresh=attempt > 0)}",
                "content-type": "application/json;charset=UTF-8",
            }
            if continuation is not None:
                headers["cont-yn"], headers["next-key"] = continuation
            response = self._http.request("POST", CHART_PATH, json=body, headers=headers)
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
            if response.status_code >= 400 or code != 0:
                message = str(payload.get("return_msg", ""))[:200]
                raise InvalidBarsQueryError(f"Kiwoom refused the query ({code}): {message}")
            more = response.headers.get("cont-yn", "N") == "Y" and response.headers.get("next-key")
            next_page = ("Y", response.headers["next-key"]) if more else None
            return list(payload.get("result_list") or []), next_page
        raise ProviderAuthError("Kiwoom rejected the reissued token")

    def _minute(self, record: dict[str, Any]) -> RawMinute:
        try:
            local = datetime.strptime(str(record["cntr_tm"]), "%Y%m%d%H%M%S")
        except (KeyError, ValueError) as exc:
            raise ProviderDataError("Kiwoom returned a bar without a valid cntr_tm") from exc
        return RawMinute(
            start_utc=local.replace(tzinfo=self._zone).astimezone(timezone.utc).replace(second=0),
            open=decimal_field(record, "open_pric"),
            high=decimal_field(record, "high_pric"),
            low=decimal_field(record, "low_pric"),
            close=decimal_field(record, "cur_prc"),
            volume=decimal_field(record, "trde_qty"),
        )
