"""Kiwoom US quote snapshot (read-only): usa20100, 미국주식 현재가 종목정보.

POST /api/us/mrkcond with api-id usa20100 answers one ticker's current price, open, high,
low, previous close, cumulative volume (`acc_trde_qty`), share count and market cap
(`mac`, thousand USD) as Kiwoom sees them. The condition watch asks it the moment a
ticker enters a condition, so the values Kiwoom's condition engine worked from can be
compared with other feeds (ADR 0055, mondash #393). Values are kept as Kiwoom sent them:
the spec does not say which sessions `acc_trde_qty` counts, which is what is being found
out. Live check, 2026-11-28 (user's key, through kwcli): SDA answered every field above.

Only the token and this api-id can be called: no order endpoint is reachable.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

import httpx

from services.market_data.broker_common import AllowlistedClient
from services.market_data.provider import (
    InvalidBarsQueryError,
    ProviderAuthError,
    ProviderDataError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)

REAL_BASE_URL = "https://api.kiwoom.com"
DEMO_BASE_URL = "https://mockapi.kiwoom.com"
QUOTE_PATH = "/api/us/mrkcond"
API_ID = "usa20100"
# Kiwoom's exchange codes, as condition events carry them (stexTp).
EXCHANGES = frozenset({"NA", "ND", "NY"})
RATE_LIMIT_CODES = {1700, 1701, 1702}
TOKEN_INVALID_CODE = 3
MAX_VALUE_LENGTH = 200


class KiwoomQuoteInfo:
    """Calls usa20100 with the REST token of an existing Kiwoom session."""

    def __init__(
        self,
        token: Callable[[bool], str],
        environment: Literal["real", "demo"] = "real",
        *,
        transport: httpx.BaseTransport | None = None,
        min_interval_seconds: float = 0.25,
    ) -> None:
        self._token = token
        self._http = AllowlistedClient(
            DEMO_BASE_URL if environment == "demo" else REAL_BASE_URL,
            [("POST", QUOTE_PATH)],
            transport=transport,
            min_interval_seconds=min_interval_seconds,
        )

    def info(self, symbol: str, kiwoom_exchange: str) -> dict[str, str]:
        """Every field of the reply except return_code and return_msg, as text."""
        exchange = kiwoom_exchange.strip().upper()
        if exchange not in EXCHANGES:
            raise InvalidBarsQueryError(f"unknown Kiwoom exchange code {kiwoom_exchange!r}")
        body = {"stex_tp": exchange, "stk_cd": symbol.strip().upper()}
        for attempt in range(2):
            headers = {
                "api-id": API_ID,
                "authorization": f"Bearer {self._token(attempt > 0)}",
                "content-type": "application/json;charset=UTF-8",
            }
            response = self._http.request("POST", QUOTE_PATH, json=body, headers=headers)
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if payload is None or not isinstance(payload, dict):
                if response.status_code == 401 and attempt == 0:
                    continue
                if response.status_code in (401, 403):
                    raise ProviderAuthError(f"Kiwoom rejected the request ({response.status_code})")
                if response.status_code == 429:
                    raise ProviderRateLimitError("Kiwoom request limit reached; retry later")
                if response.status_code >= 500:
                    raise ProviderUnavailableError(
                        f"Kiwoom is unavailable ({response.status_code})"
                    )
                raise ProviderDataError("Kiwoom returned a quote that is not a JSON object")
            try:
                code = int(str(payload.get("return_code", 0) or 0).strip())
            except ValueError:
                code = -1
            message = str(payload.get("return_msg", ""))
            # 8005: the token stopped working because another client issued one.
            invalid = code == TOKEN_INVALID_CODE and "8005" in message
            if (response.status_code == 401 or invalid) and attempt == 0:
                continue
            if code in RATE_LIMIT_CODES or response.status_code == 429:
                raise ProviderRateLimitError("Kiwoom request limit reached; retry later")
            if response.status_code in (401, 403):
                raise ProviderAuthError(f"Kiwoom rejected the request ({response.status_code})")
            if response.status_code >= 500:
                raise ProviderUnavailableError(f"Kiwoom is unavailable ({response.status_code})")
            if response.status_code >= 400 or code != 0:
                raise InvalidBarsQueryError(f"Kiwoom refused the query ({code}): {message[:200]}")
            return {
                str(key): str(value)[:MAX_VALUE_LENGTH]
                for key, value in payload.items()
                if key not in {"return_code", "return_msg"}
            }
        raise ProviderAuthError("Kiwoom rejected the reissued token")
