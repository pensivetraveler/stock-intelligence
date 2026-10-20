"""Shared Toss session, Toss daily bars and live quotes (no network).

Payloads follow the official OpenAPI 1.2.24 examples (openapi.tossinvest.com).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient

from packages.contracts.validator import validate_document
from services.api.main import create_app
from services.market_data.broker_common import BlockedEndpointError
from services.market_data.fake import FakeMarketDataProvider
from services.market_data.provider import BarsQuery, InvalidBarsQueryError
from services.market_data.toss import TossConfig, TossMarketDataProvider
from services.market_data.toss_quotes import market_quote
from services.market_data.toss_session import TossCredentials, TossSession, session_for

SECRET = "test-secret-not-real"
NOW = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)
TOKEN = {"access_token": "toss-token", "token_type": "Bearer", "expires_in": 3600}


class Server:
    """Answers by path; records every request."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json=TOKEN)
        answer = self.routes.get(request.url.path)
        if answer is None:
            return httpx.Response(404, json={"error": {"code": "not-found", "message": "x"}})
        if callable(answer):
            return answer(request)  # type: ignore[no-any-return]
        status, body = answer
        return httpx.Response(status, text=json.dumps(body))

    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]


def session(server: Server, **options: Any) -> TossSession:
    return TossSession(
        TossCredentials("toss-client", SECRET), transport=httpx.MockTransport(server), **options
    )


def test_only_documented_reads_are_allowed_and_accounts_go_in_a_header() -> None:
    server = Server({"/api/v1/holdings": (200, {"result": {"items": []}})})
    toss = session(server)
    with pytest.raises(BlockedEndpointError):
        toss.get("/api/v1/orders/{orderId}/cancel", path={"orderId": "1"})
    with pytest.raises(InvalidBarsQueryError, match="needs account"):
        toss.get("/api/v1/holdings")
    with pytest.raises(InvalidBarsQueryError, match="invalid Toss symbol"):
        toss.get("/api/v1/stocks/{symbol}/warnings", path={"symbol": "../orders"})

    assert toss.get("/api/v1/holdings", account=3) == {"items": []}
    holdings = server.requests[-1]
    assert holdings.method == "GET"
    assert holdings.headers["X-Tossinvest-Account"] == "3"
    assert holdings.headers["Authorization"] == "Bearer toss-token"


def test_requests_wait_for_their_rate_limit_group() -> None:
    server = Server({"/api/v1/accounts": (200, {"result": []})})
    waits: list[float] = []
    toss = session(server, sleep=waits.append, monotonic=lambda: 100.0)
    toss.get("/api/v1/accounts")
    toss.get("/api/v1/accounts")
    assert waits == [pytest.approx(1.0)]  # ACCOUNT: one request per second


def test_one_key_shares_one_token_across_the_process() -> None:
    server = Server({"/api/v1/accounts": (200, {"result": []})})
    transport = httpx.MockTransport(server)
    credentials = TossCredentials("shared-client", SECRET)
    first = session_for(credentials, transport=transport)
    assert session_for(credentials, transport=transport) is first
    chart = TossMarketDataProvider(
        TossConfig(client_id="shared-client", client_secret=SECRET), transport=transport
    )
    assert chart.session is first
    first.get("/api/v1/accounts")
    chart.session.get("/api/v1/accounts")
    assert server.paths().count("/oauth2/token") == 1


def daily(date: str, close: str) -> dict[str, str]:
    return {
        "timestamp": f"{date}T00:00:00-04:00",
        "openPrice": "100.00",
        "highPrice": "101.50",
        "lowPrice": "99.25",
        "closePrice": close,
        "volume": "1000",
        "currency": "USD",
    }


def test_toss_daily_bars_are_unadjusted_and_paged() -> None:
    pages = [
        {
            "candles": [daily("2026-10-16", "100.10"), daily("2026-10-15", "100.20")],
            "nextBefore": "2026-10-14T00:00:00-04:00",
        },
        {"candles": [daily("2026-10-14", "100.30")], "nextBefore": None},
    ]

    def candles(request: httpx.Request) -> httpx.Response:
        params = parse_qs(request.url.query.decode())
        assert params["interval"] == ["1d"] and params["adjusted"] == ["false"]
        return httpx.Response(200, json={"result": pages.pop(0)})

    server = Server({"/api/v1/candles": candles})
    provider = TossMarketDataProvider(
        TossConfig(client_id="daily-client", client_secret=SECRET),
        transport=httpx.MockTransport(server),
        now=lambda: NOW,
    )
    query = BarsQuery(
        "us:nvda:xnas:2026",  # any listed stock, not only configured symbols
        "1d",
        datetime(2026, 10, 14, tzinfo=timezone.utc),
        datetime(2026, 10, 17, tzinfo=timezone.utc),
    )
    result = provider.get_daily_bars(query)
    validate_document(result.to_document())
    assert [(bar["interval_start_utc"], bar["close"]) for bar in result.bars] == [
        ("2026-10-14T13:30:00Z", "100.30"),
        ("2026-10-15T13:30:00Z", "100.20"),
        ("2026-10-16T13:30:00Z", "100.10"),
    ]
    assert result.coverage["dataset_revision"] == "toss-us-raw-r1:1d"
    assert result.coverage["coverage_status"] == "complete"


QUOTE_ROUTES: dict[str, Any] = {
    "/api/v1/prices": (
        200,
        {
            "result": [
                {
                    "symbol": "AAPL",
                    "timestamp": "2026-10-20T10:59:58.120-04:00",
                    "lastPrice": "252.31",
                    "currency": "USD",
                }
            ]
        },
    ),
    "/api/v1/orderbook": (
        200,
        {
            "result": {
                "timestamp": "2026-10-20T10:59:59.000-04:00",
                "currency": "USD",
                "asks": [{"price": "252.33", "volume": "120"}],
                "bids": [{"price": "252.30", "volume": "80"}],
            }
        },
    ),
    "/api/v1/trades": (
        200,
        {
            "result": [
                {
                    "price": "252.31",
                    "volume": "15",
                    "timestamp": "2026-10-20T10:59:58.120-04:00",
                    "currency": "USD",
                }
            ]
        },
    ),
    "/api/v1/price-limits": (
        400,
        {"error": {"code": "unsupported-market", "message": "US stocks have no limits"}},
    ),
}


def test_a_quote_keeps_exact_prices_and_utc_times() -> None:
    quote = market_quote(session(Server(QUOTE_ROUTES)), "us:aapl:xnas:2026", "AAPL", lambda: NOW)
    validate_document(quote)
    assert quote["currency"] == "USD"
    assert quote["last"] == {"price": "252.31", "at_utc": "2026-10-20T14:59:58.120Z"}
    assert quote["orderbook"]["asks"] == [{"price": "252.33", "volume": "120"}]
    assert quote["trades"][0]["volume"] == "15"
    assert quote["price_limits"] is None  # US stocks: Toss refuses price limits


def test_quote_api_needs_a_toss_key_and_serves_listed_stocks() -> None:
    toss = session(Server(QUOTE_ROUTES))
    app = create_app(
        FakeMarketDataProvider.from_path(), toss_session=lambda: toss, clock=lambda: NOW
    )
    with TestClient(app) as client:
        response = client.get("/v1/market/quote", params={"instrument_id": "us:aapl:xnas:2026"})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        validate_document(response.json())
        missing = client.get("/v1/market/quote", params={"instrument_id": "nope"})
        assert missing.status_code == 404

    without = create_app(FakeMarketDataProvider.from_path(), toss_session=lambda: None)
    with TestClient(without) as client:
        response = client.get("/v1/market/quote", params={"instrument_id": "us:aapl:xnas:2026"})
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "quote_source_unavailable"
