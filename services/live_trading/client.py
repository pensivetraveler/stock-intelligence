"""HTTP client that can only reach Kiwoom's live (실전) domain (ADR 0047 §3).

This module is the only place that names the live host. Like the paper client, the
base URL is fixed, every request is checked again just before it is sent (scheme, host,
port and the (method, path, api-id) allowlist), and redirects are never followed.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import httpx

from services.paper_trading.paper_client import (
    LiveDomainBlockedError,
    PaperOperationBlockedError,
)

LIVE_SCHEME = "https"
LIVE_HOST = "api.kiwoom.com"
LIVE_PORT = 443
OPERATION_HEADER = "api-id"


class NotLiveCredentialError(ValueError):
    """A key registered for a demo (모의투자) account was given to live-order code."""


def require_live_environment(environment: str | None) -> None:
    if environment != "real":
        raise NotLiveCredentialError("live orders need a key registered as a real account")


class LiveOnlyClient:
    """Sends only allowlisted (method, path, api-id) requests to Kiwoom's live domain.

    A request elsewhere raises `LiveDomainBlockedError` and one off the allowlist
    `PaperOperationBlockedError`, the same errors that make the runner halt.
    """

    def __init__(
        self,
        allowed: Iterable[tuple[str, str, str]],
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._allowed = {(method.upper(), path, operation) for method, path, operation in allowed}
        self._client = httpx.Client(
            base_url=f"{LIVE_SCHEME}://{LIVE_HOST}",
            timeout=httpx.Timeout(10.0, connect=5.0),
            transport=transport,
            follow_redirects=False,
        )

    def _check(self, request: httpx.Request) -> None:
        url = request.url
        if (url.scheme, url.host, url.port or 443) != (LIVE_SCHEME, LIVE_HOST, LIVE_PORT):
            raise LiveDomainBlockedError(f"{url.scheme}://{url.host} is not the Kiwoom live domain")
        operation = request.headers.get(OPERATION_HEADER, "")
        if (request.method, url.path, operation) not in self._allowed:
            raise PaperOperationBlockedError(
                f"{request.method} {url.path} {operation or '(no operation)'} "
                "is not on the live allowlist"
            )

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        request = self._client.build_request(method, path, **kwargs)
        self._check(request)
        response = self._client.send(request, follow_redirects=False)
        if response.is_redirect:
            raise LiveDomainBlockedError("the Kiwoom live domain answered a redirect")
        return response

    def close(self) -> None:
        self._client.close()
