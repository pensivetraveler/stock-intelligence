"""Toss research tables (no network). Payloads follow the official OpenAPI 1.2.24 examples."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from packages.contracts.validator import validate_document
from services.api.main import create_app
from services.market_data.fake import FakeMarketDataProvider
from services.market_data.provider import InvalidBarsQueryError
from services.market_data.toss_research import research_table
from services.market_data.toss_session import TossCredentials, TossSession

NOW = datetime(2026, 10, 20, 12, 30, tzinfo=timezone.utc)
ROUTES: dict[str, Any] = {
    "/api/v1/rankings": {
        "result": {
            "rankedAt": "2026-10-20T21:27:42+09:00",
            "rankings": [
                {
                    "rank": 1,
                    "symbol": "SOXL",
                    "currency": "USD",
                    "price": {"lastPrice": "93.06", "basePrice": "98.65", "changeRate": "-0.0566"},
                    "tradingVolume": "5806983",
                    "tradingAmount": "729711095863",
                }
            ],
        }
    },
    "/api/v1/sectors/100": {
        "result": {
            "sectorId": "100",
            "name": "반도체",
            "depth": 1,
            "parentSectorId": "10",
            "summary": None,
            "performance": {
                "changeRates": [
                    {"duration": "1d", "changeRate": "0.0179"},
                    {"duration": "6mo", "changeRate": None},
                ]
            },
        }
    },
    "/api/v1/stocks/005930/investor-trading": {
        "result": {
            "nextUntil": "2026-10-16",
            "records": [
                {
                    "date": "2026-10-20",
                    "updatedAt": "2026-10-20T20:21:29+09:00",
                    "individual": {"buyVolume": "12978259", "netBuyVolume": "1312941"},
                    "foreigner": None,
                }
            ],
        }
    },
}


def session(requests: list[httpx.Request] | None = None) -> TossSession:
    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        body = ROUTES.get(request.url.path)
        if body is None:
            return httpx.Response(404, json={"error": {"code": "not-found"}})
        return httpx.Response(200, text=json.dumps(body))

    return TossSession(
        TossCredentials("research-client", "test-secret-not-real"),
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
    )


def test_rankings_flatten_nested_prices_and_keep_exact_strings() -> None:
    requests: list[httpx.Request] = []
    table = research_table(
        session(requests),
        "rankings",
        None,
        {"type": "MARKET_TRADING_AMOUNT", "marketCountry": "US", "duration": "1d"},
        lambda: NOW,
    )
    validate_document(table)
    assert requests[-1].url.params["marketCountry"] == "US"
    assert table["as_of_utc"] == "2026-10-20T12:27:42Z"
    row = dict(zip(table["columns"], table["rows"][0], strict=True))
    assert row["price.changeRate"] == "-0.0566"
    assert row["rank"] == "1"


def test_sector_performance_becomes_one_column_per_period() -> None:
    table = research_table(session(), "sector", "100", {"marketCountry": "US"}, lambda: NOW)
    validate_document(table)
    row = dict(zip(table["columns"], table["rows"][0], strict=True))
    assert row["performance.changeRates.1d.changeRate"] == "0.0179"
    assert row["performance.changeRates.6mo.changeRate"] is None


def test_trading_trends_keep_a_cursor_and_utc_times() -> None:
    table = research_table(session(), "stock_investor_trading", "005930", {}, lambda: NOW)
    validate_document(table)
    assert table["next_cursor"] == "2026-10-16"
    row = dict(zip(table["columns"], table["rows"][0], strict=True))
    assert row["updatedAt"] == "2026-10-20T11:21:29Z"
    assert row["foreigner"] is None


@pytest.mark.parametrize(
    ("dataset", "key", "params", "message"),
    [
        ("orders", None, {}, "unknown research dataset"),
        ("rankings", None, {"accountSeq": "1"}, "does not take accountSeq"),
        ("sector", None, {}, "needs a sectorId"),
        ("rankings", "005930", {}, "takes no key"),
        ("rankings", None, {"type": "a b"}, "invalid type"),
    ],
)
def test_only_known_datasets_and_parameters_are_sent(
    dataset: str, key: str | None, params: dict[str, str], message: str
) -> None:
    requests: list[httpx.Request] = []
    with pytest.raises(InvalidBarsQueryError, match=message):
        research_table(session(requests), dataset, key, params, lambda: NOW)
    assert requests == []


def test_research_api() -> None:
    toss = session()
    app = create_app(
        FakeMarketDataProvider.from_path(), toss_session=lambda: toss, clock=lambda: NOW
    )
    with TestClient(app) as client:
        ok = client.get(
            "/v1/research/rankings",
            params={"type": "TOP_GAINERS", "marketCountry": "US", "duration": "1d"},
        )
        assert ok.status_code == 200
        validate_document(ok.json())
        keyed = client.get("/v1/research/sector", params={"key": "100", "marketCountry": "KR"})
        assert keyed.status_code == 200
        bad = client.get("/v1/research/rankings", params={"secret": "x"})
        assert bad.status_code == 422
