"""One read-only Toss Open API client per key, shared by everything in this process.

Toss keeps one valid access token per client: issuing a new one revokes the old. The chart
adapter, quotes and account views must therefore share one token per key, so sessions are
kept in a process-wide registry keyed by the key itself (`session_for`).

Only the documented GET read endpoints are on the allowlist (official OpenAPI 1.2.24,
openapi.tossinvest.com/openapi-docs/latest/openapi.json). Order and conditional-order
create, modify and cancel endpoints are not, so this client cannot place, change or cancel
orders. Each request waits for its documented rate-limit group (requests per second per
client and group, overview.md "Rate Limits").
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import quote

import httpx

from services.market_data.broker_common import BlockedEndpointError, TokenCache
from services.market_data.provider import (
    InvalidBarsQueryError,
    ProviderAuthError,
    ProviderDataError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)

DEFAULT_BASE_URL = "https://openapi.tossinvest.com"
TOKEN_PATH = "/oauth2/token"
TOKEN_ERRORS = {"expired-token", "token-revoked", "invalid-token"}

# Read endpoints and their rate-limit groups, from the official OpenAPI document.
READ_ENDPOINTS: dict[str, str] = {
    "/api/v1/orderbook": "MARKET_DATA",
    "/api/v1/prices": "MARKET_DATA",
    "/api/v1/trades": "MARKET_DATA",
    "/api/v1/price-limits": "MARKET_DATA",
    "/api/v1/candles": "MARKET_DATA_CHART",
    "/api/v1/stocks": "STOCK",
    "/api/v1/stocks/all": "STOCK_ALL",
    "/api/v1/stocks/{symbol}/warnings": "STOCK",
    "/api/v1/stocks/{symbol}/investor-trading": "STOCK_TRADING_TREND",
    "/api/v1/stocks/{symbol}/program-trades": "STOCK_TRADING_TREND",
    "/api/v1/stocks/{symbol}/short-selling": "STOCK_TRADING_TREND",
    "/api/v1/stocks/{symbol}/credit-trades": "STOCK_TRADING_TREND",
    "/api/v1/stocks/{symbol}/securities-lending": "STOCK_TRADING_TREND",
    "/api/v1/exchange-rate": "MARKET_INFO",
    "/api/v1/market-calendar/KR": "MARKET_INFO",
    "/api/v1/market-calendar/US": "MARKET_INFO",
    "/api/v1/rankings": "RANKING",
    "/api/v1/market-indicators/prices": "MARKET_INDICATOR",
    "/api/v1/market-indicators/{symbol}/candles": "MARKET_INDICATOR_CHART",
    "/api/v1/market-indicators/{symbol}/investor-trading": "MARKET_INDICATOR",
    "/api/v1/sectors": "SECTOR",
    "/api/v1/sectors/rankings": "SECTOR_RANKING",
    "/api/v1/sectors/{sectorId}": "SECTOR",
    "/api/v1/sectors/{sectorId}/stocks": "SECTOR",
    "/api/v1/sectors/{sectorId}/etfs": "SECTOR",
    "/api/v1/accounts": "ACCOUNT",
    "/api/v1/holdings": "ASSET",
    "/api/v1/orders": "ORDER_HISTORY",
    "/api/v1/orders/{orderId}": "ORDER_HISTORY",
    "/api/v1/conditional-orders": "CONDITIONAL_ORDER_HISTORY",
    "/api/v1/conditional-orders/{conditionalOrderId}": "CONDITIONAL_ORDER_HISTORY",
    "/api/v1/buying-power": "ORDER_INFO",
    "/api/v1/sellable-quantity": "ORDER_INFO",
    "/api/v1/commissions": "ORDER_INFO",
}
# Requests per second per client and group (ORDER_INFO uses its 09:00-09:10 KST peak limit).
GROUP_PER_SECOND: dict[str, int] = {
    "AUTH": 5,
    "ACCOUNT": 1,
    "ASSET": 5,
    "STOCK": 5,
    "STOCK_ALL": 1,
    "STOCK_TRADING_TREND": 10,
    "MARKET_INFO": 3,
    "MARKET_DATA": 15,
    "MARKET_DATA_CHART": 20,
    "RANKING": 5,
    "MARKET_INDICATOR": 10,
    "MARKET_INDICATOR_CHART": 5,
    "SECTOR": 5,
    "SECTOR_RANKING": 5,
    "ORDER_HISTORY": 5,
    "ORDER_INFO": 3,
    "CONDITIONAL_ORDER_HISTORY": 10,
}
# Account-context endpoints take the account sequence in this header.
ACCOUNT_HEADER = "X-Tossinvest-Account"
ACCOUNT_GROUPS = frozenset({"ASSET", "ORDER_HISTORY", "ORDER_INFO", "CONDITIONAL_ORDER_HISTORY"})
PATH_VALUE = re.compile(r"^[A-Za-z0-9.\-_]{1,64}$")


@dataclass(frozen=True)
class TossCredentials:
    client_id: str
    client_secret: str
    base_url: str = DEFAULT_BASE_URL

    def __repr__(self) -> str:  # never print secrets
        return f"TossCredentials(base_url={self.base_url!r})"


class TossSession:
    """Read-only Toss client: one token and one request spacing per group, per key."""

    def __init__(
        self,
        credentials: TossCredentials,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._credentials = credentials
        self._client = httpx.Client(
            base_url=credentials.base_url,
            timeout=httpx.Timeout(10.0, connect=5.0),
            transport=transport,
        )
        self._sleep = sleep
        self._monotonic = monotonic
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self.token = TokenCache(self._issue_token)

    @property
    def key_ref(self) -> str:
        """A stable, non-secret reference to this key (a truncated hash of its client ID)."""
        return hashlib.sha256(self._credentials.client_id.encode()).hexdigest()[:16]

    def get(
        self,
        template: str,
        *,
        path: Mapping[str, str] | None = None,
        params: Mapping[str, str] | None = None,
        account: int | None = None,
    ) -> Any:
        """The `result` of one documented read endpoint (prices parse as Decimal)."""
        group = READ_ENDPOINTS.get(template)
        if group is None:
            raise BlockedEndpointError(f"GET {template} is not on the Toss read-only allowlist")
        url = template
        for name, value in (path or {}).items():
            if not PATH_VALUE.fullmatch(value):
                raise InvalidBarsQueryError(f"invalid Toss {name}: {value!r}")
            url = url.replace("{" + name + "}", quote(value, safe=""))
        if "{" in url:
            raise InvalidBarsQueryError(f"missing path values for {template}")
        if (group in ACCOUNT_GROUPS) != (account is not None):
            raise InvalidBarsQueryError(
                f"{template} {'needs' if account is None else 'takes no'} account"
            )
        for attempt in range(2):
            headers = {
                "Authorization": f"Bearer {self.token.get(refresh=attempt > 0)}",
                "Accept": "application/json",
            }
            if account is not None:
                headers[ACCOUNT_HEADER] = str(account)
            self._wait(group)
            response = self._send("GET", url, params=dict(params or {}), headers=headers)
            payload = self._payload(response)
            error = payload.get("error") if isinstance(payload, dict) else None
            code = str(error.get("code", "")) if isinstance(error, dict) else ""
            if response.status_code == 401 and code in TOKEN_ERRORS and attempt == 0:
                continue  # expired, or revoked by another token issue: reissue once
            self._raise_for_status(response.status_code, code, error)
            if not isinstance(payload, dict) or "result" not in payload:
                raise ProviderDataError("Toss returned a response without a result")
            return payload["result"]
        raise ProviderAuthError("Toss rejected the reissued token")

    def _issue_token(self) -> tuple[str, float]:
        self._wait("AUTH")
        response = self._send(
            "POST",
            TOKEN_PATH,
            data={
                "grant_type": "client_credentials",
                "client_id": self._credentials.client_id,
                "client_secret": self._credentials.client_secret,
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

    def _wait(self, group: str) -> None:
        interval = 1.0 / GROUP_PER_SECOND[group]
        with self._lock:
            last = self._last.get(group)
            now = self._monotonic()
            wait = 0.0 if last is None else max(0.0, interval - (now - last))
            self._last[group] = now + wait
        if wait > 0:
            self._sleep(wait)

    def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            return self._client.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            raise ProviderUnavailableError("Toss did not answer in time") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(f"could not reach Toss: {type(exc).__name__}") from exc

    @staticmethod
    def _payload(response: httpx.Response) -> Any:
        try:
            return json.loads(response.text, parse_float=Decimal)
        except ValueError as exc:
            if response.status_code >= 500:
                raise ProviderUnavailableError(
                    f"Toss is unavailable ({response.status_code})"
                ) from exc
            raise ProviderDataError("Toss returned a response that is not JSON") from exc

    @staticmethod
    def _raise_for_status(status: int, code: str, error: object) -> None:
        if status < 400:
            return
        if status == 429:
            raise ProviderRateLimitError("Toss rate limit reached; retry later")
        if status in (401, 403):
            hint = " (this IP is not on the Open API allowlist)" if code == "ip-not-allowed" else ""
            raise ProviderAuthError(f"Toss rejected the request ({status} {code}){hint}")
        if status >= 500:
            raise ProviderUnavailableError(f"Toss is unavailable ({status})")
        message = str(error.get("message", ""))[:200] if isinstance(error, dict) else ""
        raise InvalidBarsQueryError(f"Toss refused the query ({code}): {message}")


_SESSIONS: dict[tuple[str, int | None], TossSession] = {}
_SESSIONS_LOCK = threading.Lock()


def session_for(
    credentials: TossCredentials, *, transport: httpx.BaseTransport | None = None
) -> TossSession:
    """The process-wide session of one key (and one test transport), created once."""
    digest = hashlib.sha256(
        "\0".join((credentials.base_url, credentials.client_id, credentials.client_secret)).encode()
    ).hexdigest()
    key = (digest, id(transport) if transport is not None else None)
    with _SESSIONS_LOCK:
        session = _SESSIONS.get(key)
        if session is None:
            session = TossSession(credentials, transport=transport)
            _SESSIONS[key] = session
        return session
