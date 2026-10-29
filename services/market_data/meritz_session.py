"""One read-only 메리츠증권 Open API session per key: token, MAC header and reply checks.

Spec: openapi.imeritz.com (JSON download /api-spec/download/json, generated 2026-10-26).
Facts used here, all from that spec:
- the token comes from a form POST with `scope=login`; `expires_in` is in seconds;
- every call needs the `authorization: Bearer` header and a `mac_address` header of 12
  characters, the MAC address of the calling device; without it the call is refused;
- every error comes back as HTTP 500, so success is judged by `rsp_cd`. The success code
  differs per API ("0000" for quotes, "5766" for account queries, "5762" when more pages
  follow) and "5820" means no rows. "EGW00200" means the per-second limit was exceeded;
- paged queries answer `tr_cont` "1" with a `tr_cont_key` that is sent back unchanged
  (as query parameters) for the next page; 30 rows a page;
- the access token is bound to one account; no account number is sent or stored.

Only the token path and the GET queries listed in READ_PATHS can be called. No order,
order change, reservation or currency exchange path is reachable from this session: the
broker has no demo server, so every order would be a real one (ADR 0043).
"""

from __future__ import annotations

import hashlib
import re
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx

from services.market_data.broker_common import AllowlistedClient, BrokerConfigError, TokenCache
from services.market_data.provider import (
    InvalidBarsQueryError,
    ProviderAuthError,
    ProviderDataError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)

BASE_URL = "https://openapi.imeritz.com:9443"
WS_URL = "wss://openapi.imeritz.com:29443/websocket"
TOKEN_PATH = "/oauth2/token"
MINUTES_PATH = "/market/v1/overseas/candles/minutes"
DAYS_PATH = "/market/v1/overseas/candles/days"
PRICES_PATH = "/market/v1/overseas/prices"
DAYTIME_PRICES_PATH = "/market/v1/overseas/prices/daytime"
AFTER_PRICES_PATH = "/market/v1/overseas/prices/after"
ORDERBOOK_PATH = "/market/v1/overseas/orderbook"
TICKS_PATH = "/market/v1/overseas/trades/ticks"
HISTORY_PATH = "/trading/v1/overseas/orders/history"
TODAY_PATH = "/trading/v1/overseas/orders/today"
HOLDINGS_PATH = "/accounts/v1/me/overseas/holdings"
CURRENCIES_PATH = "/accounts/v1/me/overseas/currencies"
VALUATION_PATH = "/accounts/v1/me/valuation"
DEPOSIT_PATH = "/accounts/v1/me/deposit"
# Every query this app may send: market data and the account's own read-only views.
READ_PATHS = frozenset(
    {
        MINUTES_PATH,
        DAYS_PATH,
        PRICES_PATH,
        DAYTIME_PRICES_PATH,
        AFTER_PRICES_PATH,
        ORDERBOOK_PATH,
        TICKS_PATH,
        HISTORY_PATH,
        TODAY_PATH,
        HOLDINGS_PATH,
        CURRENCIES_PATH,
        VALUATION_PATH,
        DEPOSIT_PATH,
    }
)
OK_CODES = frozenset({"0000", "5766", "5762"})
NO_ROWS = "5820"
RATE_LIMIT_CODES = frozenset({"EGW00200"})
MAX_PAGES = 500
MAC_PATTERN = re.compile(r"^[0-9A-F]{12}$")
# Market codes of the quote APIs (`mrkt_cls_code`) per listing venue (MIC).
EXCHANGE_CODES = {"XNAS": "OQ", "XNYS": "NY", "XASE": "AX", "ARCX": "AX"}


def normalize_mac(value: str) -> str:
    """ "aa:bb:cc:dd:ee:ff" or "AA-BB-..." as the 12 uppercase characters the API takes."""
    mac = value.replace(":", "").replace("-", "").strip().upper()
    if not MAC_PATTERN.fullmatch(mac):
        raise BrokerConfigError("the Meritz MAC address must be 12 hexadecimal characters")
    return mac


@dataclass(frozen=True)
class MeritzCredentials:
    app_key: str
    app_secret: str
    mac_address: str
    base_url: str = BASE_URL

    def __repr__(self) -> str:
        return f"MeritzCredentials(base_url={self.base_url!r})"


class MeritzSession:
    """Token plus the read-only GET queries; nothing else can be sent."""

    def __init__(
        self,
        credentials: MeritzCredentials,
        *,
        transport: httpx.BaseTransport | None = None,
        min_interval_seconds: float = 0.2,
    ) -> None:
        self._credentials = credentials
        self.mac_address = normalize_mac(credentials.mac_address)
        # No request limit is published beyond EGW00200; calls are spaced conservatively.
        self._http = AllowlistedClient(
            credentials.base_url,
            [("POST", TOKEN_PATH), *(("GET", path) for path in sorted(READ_PATHS))],
            transport=transport,
            min_interval_seconds=min_interval_seconds,
        )
        self.token = TokenCache(self._issue_token, margin_seconds=60)

    @property
    def key_ref(self) -> str:
        """A stable, non-secret reference to this key (a truncated hash of its app key)."""
        return hashlib.sha256(self._credentials.app_key.encode()).hexdigest()[:16]

    def _issue_token(self) -> tuple[str, float]:
        response = self._http.request(
            "POST",
            TOKEN_PATH,
            data={
                "grant_type": "client_credentials",
                "client_id": self._credentials.app_key,
                "client_secret": self._credentials.app_secret,
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
        try:
            lifetime = float(payload.get("expires_in", 86_400))
        except (TypeError, ValueError):
            lifetime = 86_400.0
        return str(token), lifetime

    def get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        """One reply of a read-only query; an empty reply (no rows) has no `data`."""
        if path not in READ_PATHS:
            raise ProviderDataError(f"{path} is not on the read-only allowlist")
        for attempt in range(2):
            headers = {
                "authorization": f"Bearer {self.token.get(refresh=attempt > 0)}",
                "mac_address": self.mac_address,
            }
            response = self._http.request("GET", path, params=params or {}, headers=headers)
            try:
                payload = response.json()
            except ValueError as exc:
                raise ProviderUnavailableError(
                    f"Meritz returned no JSON ({response.status_code})"
                ) from exc
            if not isinstance(payload, dict):
                raise ProviderDataError("Meritz returned a reply that is not an object")
            code = str(payload.get("rsp_cd", ""))
            if code in OK_CODES:
                return payload
            if code == NO_ROWS:
                return {"rsp_cd": code, "tr_cont": "0"}
            if code in RATE_LIMIT_CODES:
                raise ProviderRateLimitError("Meritz per-second limit reached; retry later")
            # The spec lists no token error code: an HTTP 401/403 is retried once with a
            # new token.
            if response.status_code in (401, 403):
                if attempt == 0:
                    continue
                raise ProviderAuthError(
                    f"Meritz rejected the request ({response.status_code} {code})"
                )
            if not code:
                raise ProviderUnavailableError(f"Meritz is unavailable ({response.status_code})")
            message = str(payload.get("rsp_msg", ""))[:200]
            raise InvalidBarsQueryError(f"Meritz refused the query ({code}): {message}")
        raise ProviderAuthError("Meritz rejected the reissued token")

    def rows(self, path: str, params: dict[str, str] | None = None) -> Iterator[dict[str, Any]]:
        """Every row of a paged query, following `tr_cont` / `tr_cont_key`."""
        query = dict(params or {})
        for _page in range(MAX_PAGES):
            payload = self.get(path, query)
            data = payload.get("data")
            for row in data if isinstance(data, list) else []:
                if isinstance(row, dict):
                    yield row
            key = payload.get("tr_cont_key")
            if str(payload.get("tr_cont", "0")) != "1" or not key:
                return
            # Sent back exactly as received (it can hold long runs of spaces).
            query = {**(params or {}), "tr_cont": "1", "tr_cont_key": str(key)}
        raise ProviderDataError(f"Meritz {path} returned more than {MAX_PAGES} pages")


_SESSIONS: dict[str, MeritzSession] = {}
_SESSIONS_LOCK = threading.Lock()


def session_for(
    credentials: MeritzCredentials, *, transport: httpx.BaseTransport | None = None
) -> MeritzSession:
    """The process-wide session of one key, created once, so the chart, the account view
    and the realtime relay share one token. A test transport always gets its own."""
    if transport is not None:
        return MeritzSession(credentials, transport=transport)
    digest = hashlib.sha256(
        "\0".join(
            (
                credentials.base_url,
                credentials.app_key,
                credentials.app_secret,
                credentials.mac_address,
            )
        ).encode()
    ).hexdigest()
    key = digest
    with _SESSIONS_LOCK:
        session = _SESSIONS.get(key)
        if session is None:
            session = MeritzSession(credentials, transport=transport)
            _SESSIONS[key] = session
        return session
