"""HTTP client that can only reach a broker's paper-trading (모의투자) domain (ADR 0037 §4).

Kiwoom's live and paper servers differ only by host (the api-ids are the same), so the
exact host check below is what keeps paper code away from live accounts. There is no
URL argument: the base URL is fixed per broker, and every request is checked again just
before it is sent, so an absolute URL or a redirect cannot leave the paper domain.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

import httpx

BrokerId = Literal["kiwoom", "kis"]


@dataclass(frozen=True)
class PaperDomain:
    scheme: str
    host: str
    port: int
    # Header that names the operation: Kiwoom `api-id`, KIS `tr_id`.
    operation_header: str

    @property
    def base_url(self) -> str:
        default = 443 if self.scheme == "https" else None
        port = "" if self.port == default else f":{self.port}"
        return f"{self.scheme}://{self.host}{port}"


# From the official specs: Kiwoom "모의투자 도메인", KIS open-trading-api `vps`.
PAPER_DOMAINS: dict[BrokerId, PaperDomain] = {
    "kiwoom": PaperDomain("https", "mockapi.kiwoom.com", 443, "api-id"),
    "kis": PaperDomain("https", "openapivts.koreainvestment.com", 29443, "tr_id"),
}


class LiveDomainBlockedError(RuntimeError):
    """A request would have left the broker's paper-trading domain."""


class PaperOperationBlockedError(RuntimeError):
    """A request named an operation that is not on the paper allowlist."""


class NotPaperCredentialError(ValueError):
    """A key registered for a real (실전) account was given to paper-order code."""


def require_paper_environment(environment: str | None) -> None:
    """Refuse every key that is not registered as a demo (모의투자) account."""
    if environment != "demo":
        raise NotPaperCredentialError("paper orders need a key registered as a demo account")


class PaperOnlyClient:
    """Sends only allowlisted (method, path, operation) requests to one paper domain."""

    def __init__(
        self,
        broker_id: BrokerId,
        allowed: Iterable[tuple[str, str, str]],
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.domain = PAPER_DOMAINS[broker_id]
        self.broker_id = broker_id
        self._allowed = {(method.upper(), path, operation) for method, path, operation in allowed}
        for _method, _path, operation in self._allowed:
            # KIS paper TR IDs start with V; T… IDs are the live ones.
            if broker_id == "kis" and not operation.startswith("V"):
                raise PaperOperationBlockedError(f"{operation} is not a KIS paper TR ID")
        self._client = httpx.Client(
            base_url=self.domain.base_url,
            timeout=httpx.Timeout(10.0, connect=5.0),
            transport=transport,
            follow_redirects=False,
        )

    def _check(self, request: httpx.Request) -> None:
        url = request.url
        if (url.scheme, url.host, url.port or 443) != (
            self.domain.scheme,
            self.domain.host,
            self.domain.port,
        ):
            raise LiveDomainBlockedError(
                f"{url.scheme}://{url.host} is not the {self.broker_id} paper domain"
            )
        operation = request.headers.get(self.domain.operation_header, "")
        if (request.method, url.path, operation) not in self._allowed:
            raise PaperOperationBlockedError(
                f"{request.method} {url.path} {operation or '(no operation)'} "
                "is not on the paper allowlist"
            )

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        request = self._client.build_request(method, path, **kwargs)
        self._check(request)
        response = self._client.send(request, follow_redirects=False)
        if response.is_redirect:
            raise LiveDomainBlockedError(f"{self.broker_id} paper domain answered a redirect")
        return response

    def close(self) -> None:
        self._client.close()
