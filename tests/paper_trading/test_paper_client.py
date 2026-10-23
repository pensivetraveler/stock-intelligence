from __future__ import annotations

import inspect

import httpx
import pytest

from services.paper_trading.paper_client import (
    LiveDomainBlockedError,
    NotPaperCredentialError,
    PaperOnlyClient,
    PaperOperationBlockedError,
    require_paper_environment,
)

KIWOOM_ALLOWED = [("POST", "/oauth2/token", "au10001"), ("POST", "/api/us/acnt", "ust21070")]


def _client(sent: list[httpx.Request], status: int = 200, **reply: object) -> PaperOnlyClient:
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(status, json={"return_code": 0}, **reply)  # type: ignore[arg-type]

    return PaperOnlyClient("kiwoom", KIWOOM_ALLOWED, transport=httpx.MockTransport(handler))


def test_constructor_takes_no_url() -> None:
    parameters = inspect.signature(PaperOnlyClient.__init__).parameters
    assert not {"base_url", "url", "host"} & set(parameters)


def test_allowlisted_request_goes_to_the_paper_host() -> None:
    sent: list[httpx.Request] = []
    response = _client(sent).request("POST", "/api/us/acnt", headers={"api-id": "ust21070"})
    assert response.status_code == 200
    assert [(r.url.host, r.url.path) for r in sent] == [("mockapi.kiwoom.com", "/api/us/acnt")]


@pytest.mark.parametrize(
    "url",
    [
        "https://api.kiwoom.com/api/us/acnt",
        "https://mockapi.kiwoom.com.example/api/us/acnt",
        "https://mockapi.kiwoom.com:8443/api/us/acnt",
        "http://mockapi.kiwoom.com/api/us/acnt",
        "https://openapi.koreainvestment.com:9443/api/us/acnt",
    ],
)
def test_other_hosts_are_blocked_before_sending(url: str) -> None:
    sent: list[httpx.Request] = []
    with pytest.raises(LiveDomainBlockedError):
        _client(sent).request("POST", url, headers={"api-id": "ust21070"})
    assert sent == []


@pytest.mark.parametrize(
    ("path", "operation"),
    [
        ("/api/us/ordr", "ust20000"),  # an order api-id that is not allowlisted
        ("/api/dostk/crdordr", "kt10006"),  # credit orders are never allowed
        ("/api/us/acnt", "ust21150"),  # allowlisted path, other api-id
        ("/api/us/acnt", ""),
    ],
)
def test_operations_off_the_allowlist_are_blocked(path: str, operation: str) -> None:
    sent: list[httpx.Request] = []
    headers = {"api-id": operation} if operation else {}
    with pytest.raises(PaperOperationBlockedError):
        _client(sent).request("POST", path, headers=headers)
    assert sent == []


def test_redirects_are_not_followed() -> None:
    sent: list[httpx.Request] = []
    client = _client(sent, status=302, headers={"location": "https://api.kiwoom.com/x"})
    with pytest.raises(LiveDomainBlockedError):
        client.request("POST", "/api/us/acnt", headers={"api-id": "ust21070"})
    assert len(sent) == 1


def test_kis_live_tr_ids_are_refused() -> None:
    with pytest.raises(PaperOperationBlockedError):
        PaperOnlyClient("kis", [("POST", "/uapi/overseas-stock/v1/trading/order", "TTTT1002U")])
    client = PaperOnlyClient(
        "kis", [("POST", "/uapi/overseas-stock/v1/trading/order", "VTTT1002U")]
    )
    assert client.domain.base_url == "https://openapivts.koreainvestment.com:29443"


@pytest.mark.parametrize("environment", ["real", None, "", "DEMO"])
def test_only_demo_keys_are_accepted(environment: str | None) -> None:
    with pytest.raises(NotPaperCredentialError):
        require_paper_environment(environment)
    require_paper_environment("demo")
