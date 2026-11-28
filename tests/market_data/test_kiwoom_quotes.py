"""Kiwoom usa20100 quote snapshot over a fake transport (no network, ADR 0055 #393).

The reply follows the usa20100 spec example and the live answer for SDA on 2026-11-28.
"""

from __future__ import annotations

import json

import httpx
import pytest

from services.market_data.broker_common import BlockedEndpointError
from services.market_data.kiwoom_quotes import QUOTE_PATH, KiwoomQuoteInfo
from services.market_data.provider import (
    InvalidBarsQueryError,
    ProviderAuthError,
    ProviderRateLimitError,
)

SDA = {
    "stex_tp": "ND",
    "stk_cd": "SDA",
    "stk_cnt": "102009365",
    "mac": "21014",
    "cur_prc": "+0.2060",
    "acc_trde_qty": "56119",
    "open_pric": "+0.2061",
    "high_pric": "+0.2127",
    "low_pric": "+0.2000",
    "base_close_pric": "0.1966",
    "return_code": 0,
    "return_msg": "정상적으로 처리되었습니다",
}


def client(replies: list[httpx.Response], seen: list[httpx.Request]) -> KiwoomQuoteInfo:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return replies.pop(0)

    tokens = iter(["first", "second"])
    return KiwoomQuoteInfo(
        lambda _refresh: next(tokens), transport=httpx.MockTransport(handle), min_interval_seconds=0
    )


def test_a_quote_is_asked_by_exchange_code_and_kept_as_sent() -> None:
    seen: list[httpx.Request] = []
    values = client([httpx.Response(200, json=SDA)], seen).info("sda", "ND")
    assert values["acc_trde_qty"] == "56119" and values["mac"] == "21014"
    assert "return_code" not in values and "return_msg" not in values
    request = seen[0]
    assert request.url.path == QUOTE_PATH and request.headers["api-id"] == "usa20100"
    assert request.headers["authorization"] == "Bearer first"
    assert json.loads(request.content) == {"stex_tp": "ND", "stk_cd": "SDA"}


def test_a_replaced_token_is_reissued_once() -> None:
    seen: list[httpx.Request] = []
    stale = httpx.Response(
        200, json={"return_code": 3, "return_msg": "[8005] Token이 유효하지 않습니다"}
    )
    values = client([stale, httpx.Response(200, json=SDA)], seen).info("SDA", "ND")
    assert values["stk_cd"] == "SDA"
    assert [item.headers["authorization"] for item in seen] == ["Bearer first", "Bearer second"]


def test_refusals_are_errors_not_empty_quotes() -> None:
    with pytest.raises(ProviderRateLimitError):
        client([httpx.Response(200, json={"return_code": 1700})], []).info("SDA", "ND")
    with pytest.raises(InvalidBarsQueryError):
        client([httpx.Response(200, json={"return_code": 20, "return_msg": "x"})], []).info(
            "SDA", "ND"
        )
    with pytest.raises(ProviderAuthError):
        client([httpx.Response(401), httpx.Response(401)], []).info("SDA", "ND")
    with pytest.raises(InvalidBarsQueryError):
        client([], []).info("SDA", "NASDAQ")


def test_only_the_quote_path_can_be_called() -> None:
    quotes = client([], [])
    with pytest.raises(BlockedEndpointError):
        quotes._http.request("POST", "/api/us/ordr")
