"""Ticker search: Alpaca's asset list, the local symbol directory and the search API.

Asset payloads follow docs.alpaca.markets `GET /v2/assets`; no test reaches the network.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from packages.contracts.validator import validate_document
from services.api.main import create_app
from services.market_data.alpaca import AlpacaConfig, AlpacaMarketDataProvider
from services.market_data.fake import FakeMarketDataProvider
from services.market_data.provider import ProviderUnavailableError
from services.market_data.symbol_directory import (
    SymbolDirectory,
    SymbolDirectoryUnavailableError,
)

SECRET = "test-secret-not-real"
NOW = datetime(2026, 10, 20, 1, 0, tzinfo=timezone.utc)
ASSETS = [
    {"symbol": "AAPL", "name": "Apple Inc. Common Stock", "exchange": "NASDAQ"},
    {"symbol": "AAP", "name": "Advance Auto Parts Inc.", "exchange": "NYSE"},
    {"symbol": "APLE", "name": "Apple Hospitality REIT, Inc.", "exchange": "NYSE"},
    {"symbol": "SPY", "name": "SPDR S&P 500 ETF Trust", "exchange": "ARCA"},
    {"symbol": "BRK.B", "name": "Berkshire Hathaway Inc. Class B", "exchange": "NYSE"},
    {"symbol": "OTCX", "name": "Some OTC Company", "exchange": "OTC"},
    {"symbol": "BTC/USD", "name": "Bitcoin", "exchange": "CRYPTO"},
]


def alpaca(handler: Any) -> AlpacaMarketDataProvider:
    return AlpacaMarketDataProvider(
        AlpacaConfig(key_id="test-key-id", secret_key=SECRET),
        transport=httpx.MockTransport(handler),
        now=lambda: NOW,
    )


def test_alpaca_lists_listed_stocks_from_the_live_or_paper_host() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "api.alpaca.markets":
            return httpx.Response(401, json={"message": "unauthorized"})  # a paper key
        return httpx.Response(200, json=ASSETS)

    assets = alpaca(handler).list_assets()

    assert [(r.method, r.url.host, r.url.path) for r in requests] == [
        ("GET", "api.alpaca.markets", "/v2/assets"),
        ("GET", "paper-api.alpaca.markets", "/v2/assets"),
    ]
    assert requests[0].url.params["status"] == "active"
    assert requests[0].url.params["asset_class"] == "us_equity"
    # OTC and crypto are left out; venues become MICs.
    assert {item["symbol"]: item["venue"] for item in assets} == {
        "AAPL": "XNAS",
        "AAP": "XNYS",
        "APLE": "XNYS",
        "SPY": "ARCX",
        "BRK.B": "XNYS",
    }


def test_any_listed_symbol_can_be_charted_with_alpaca() -> None:
    adapter = alpaca(lambda request: httpx.Response(500))
    assert adapter.symbol_for("us:nvda:xnas:2026") == "NVDA"
    assert adapter.symbol_for("us:brk.b:xnys:2026") == "BRK.B"
    assert adapter.symbol_for("us:otcx:otcm:2026") is None


def directory(tmp_path: Path, fetch: Any, clock: list[datetime]) -> SymbolDirectory:
    return SymbolDirectory(tmp_path, "alpaca-sip", fetch, now=lambda: clock[0])


def assets() -> list[dict[str, str]]:
    return [
        {"symbol": "AAPL", "name": "Apple Inc. Common Stock", "venue": "XNAS"},
        {"symbol": "AAP", "name": "Advance Auto Parts Inc.", "venue": "XNYS"},
        {"symbol": "APLE", "name": "Apple Hospitality REIT, Inc.", "venue": "XNYS"},
        {"symbol": "SNAP", "name": "Snap Inc.", "venue": "XNYS"},
    ]


def test_search_ranks_tickers_before_company_names(tmp_path: Path) -> None:
    clock = [NOW]
    found = directory(tmp_path, assets, clock).search("aap")
    # Exact ticker, then ticker prefix, then company-name word.
    assert [entry.symbol for entry in found.entries] == ["AAP", "AAPL"]
    by_name = directory(tmp_path, assets, clock).search("apple")
    assert [entry.symbol for entry in by_name.entries] == ["AAPL", "APLE"]
    assert directory(tmp_path, assets, clock).search("  ").entries == ()


def test_the_directory_is_kept_for_a_day_and_served_stale_on_failure(tmp_path: Path) -> None:
    calls: list[int] = []
    clock = [NOW]

    def fetch() -> list[dict[str, str]]:
        calls.append(1)
        if len(calls) > 1:
            raise ProviderUnavailableError("Alpaca is down")
        return assets()

    first = directory(tmp_path, fetch, clock)
    assert first.search("snap").fetched_at_utc == "2026-10-20T01:00:00Z"
    stored = json.loads((tmp_path / "symbols" / "alpaca-sip.json").read_text())
    assert len(stored["symbols"]) == 4

    # A new process reads the file instead of fetching again.
    second = directory(tmp_path, fetch, clock)
    assert second.search("snap").stale is False
    assert len(calls) == 1
    assert second.known()["us:snap:xnys:2026"].name == "Snap Inc."

    clock[0] = NOW + timedelta(hours=25)
    stale = second.search("snap")
    assert stale.stale is True and [entry.symbol for entry in stale.entries] == ["SNAP"]
    second.search("snap")  # the failed refresh is not retried on every keystroke
    assert len(calls) == 2


def test_no_directory_and_no_provider_means_unavailable(tmp_path: Path) -> None:
    def fetch() -> list[dict[str, str]]:
        raise ProviderUnavailableError("Alpaca is down")

    with pytest.raises(SymbolDirectoryUnavailableError, match="could not be fetched"):
        directory(tmp_path, fetch, [NOW]).search("a")


def test_search_api_returns_chartable_listings(tmp_path: Path) -> None:
    symbols = directory(tmp_path, assets, [NOW])
    app = create_app(FakeMarketDataProvider.from_path(), symbol_directory=symbols)
    with TestClient(app) as client:
        response = client.get("/v1/market/symbols", params={"q": "apple", "limit": 1})
        assert response.status_code == 200
        document = response.json()
        validate_document(document)
        assert document["query"] == "apple"
        assert [item["name"] for item in document["results"]] == ["Apple Inc. Common Stock"]
        instrument = document["results"][0]["listing"]["instrument"]
        assert instrument["instrument_id"] == "us:aapl:xnas:2026"
        assert instrument["venue"] == "XNAS"

        assert client.get("/v1/market/symbols", params={"q": "x" * 41}).status_code == 422

    without = create_app(FakeMarketDataProvider.from_path())
    with TestClient(without) as client:
        response = client.get("/v1/market/symbols", params={"q": "a"})
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "symbol_search_unavailable"
