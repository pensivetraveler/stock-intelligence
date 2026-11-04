"""Toss stock rankings (no network). Payloads follow the official OpenAPI 1.2.28 examples."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from packages.contracts.validator import validate_document
from services.api.main import create_app
from services.market_data.fake import FakeMarketDataProvider
from services.market_data.provider import InvalidBarsQueryError, ProviderDataError
from services.market_data.toss_rankings import stock_rankings
from services.market_data.toss_session import TossCredentials, TossSession

NOW = datetime(2026, 11, 4, 12, 30, tzinfo=timezone.utc)
RANKINGS: dict[str, Any] = {
    "result": {
        "rankedAt": "2026-06-10T14:30:00+09:00",
        "rankings": [
            {
                "rank": 1,
                "symbol": "NVDA",
                "currency": "USD",
                "price": {"lastPrice": "131.38", "basePrice": "128.45", "changeRate": "0.0228"},
                "tradingVolume": "342100",
                "tradingAmount": "44942580",
            },
            {
                "rank": 2,
                "symbol": "AAPL",
                "currency": "USD",
                "price": {"lastPrice": "248.5", "basePrice": "0", "changeRate": None},
                "tradingVolume": "218500",
                "tradingAmount": "54296250",
            },
        ],
    }
}
INVESTOR: dict[str, Any] = {
    "result": {
        "foreigner": {
            "rankedAt": "2026-10-20T15:41:19+09:00",
            "netBuyRankings": [
                {"rank": 1, "symbol": "005930", "currency": "KRW", "netBuyAmount": "386487682000"}
            ],
            "netSellRankings": [
                {"rank": 1, "symbol": "000660", "currency": "KRW", "netSellAmount": "391201805000"}
            ],
        },
        "institution": {"rankedAt": None, "netBuyRankings": [], "netSellRankings": []},
        "individual": {
            "rankedAt": "2026-10-19T20:17:53+09:00",
            "netBuyRankings": [],
            "netSellRankings": [],
        },
    }
}
SAMSUNG = {
    "listing": {
        "instrument": {
            "kind": "instrument_ref",
            "schema_version": "0.1",
            "instrument_id": "kr:005930:xkrx:2026",
            "symbol": "005930",
            "venue": "XKRX",
            "asset_class": "kr_equity",
            "currency": "KRW",
            "identity_as_of": "2026-11-04T00:00:00Z",
            "identity_source": "toss-kr-assets",
        },
        "source_id": "toss-kr",
        "dataset_revision": "toss-kr-assets-2026-11-04",
        "session_dates": [],
    },
    "name": "Samsung Electronics",
    "local_name": "삼성전자",
}


def session(requests: list[httpx.Request] | None = None, body: Any = None) -> TossSession:
    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if body is not None:
            return httpx.Response(200, text=json.dumps(body))
        if request.url.path == "/api/v1/rankings":
            return httpx.Response(200, text=json.dumps(RANKINGS))
        if request.url.path == "/api/v1/rankings/investor-trading":
            return httpx.Response(200, text=json.dumps(INVESTOR))
        return httpx.Response(404, json={"error": {"code": "not-found"}})

    return TossSession(
        TossCredentials("ranking-client", "test-secret-not-real"),
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
    )


def no_listings(_market: str, _symbols: Iterable[str]) -> Mapping[str, Mapping[str, Any]]:
    return {}


def read(toss: TossSession, ranking: str, market: Any = "US", **options: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "duration": "1d",
        "investor": None,
        "exclude_caution": False,
        "count": 100,
        "listings": no_listings,
        "now": lambda: NOW,
    }
    arguments.update(options)
    return stock_rankings(toss, ranking, market, **arguments)


def test_price_rankings_keep_exact_strings_and_utc_time() -> None:
    requests: list[httpx.Request] = []
    document = read(session(requests), "TOSS_SECURITIES_TRADING_VOLUME", exclude_caution=True)
    validate_document(document)
    params = requests[-1].url.params
    assert params["type"] == "TOSS_SECURITIES_TRADING_VOLUME"
    assert params["marketCountry"] == "US"
    assert params["duration"] == "1d"
    assert params["excludeInvestmentCaution"] == "true"
    assert document["ranked_at_utc"] == "2026-06-10T05:30:00Z"
    assert document["source_id"] == "toss-us"
    first, second = document["rows"]
    assert first["last_price"] == "131.38"
    assert first["change_rate"] == "0.0228"
    assert first["net_amount"] is None
    assert first["listing"] is None
    assert second["change_rate"] is None  # basePrice 0: Toss sends no rate


def test_rows_carry_the_known_listing() -> None:
    def listings(market: str, symbols: Iterable[str]) -> Mapping[str, Mapping[str, Any]]:
        assert market == "KR"
        assert list(symbols) == ["005930"]
        return {"005930": SAMSUNG}

    document = read(
        session(), "NET_BUY", "KR", duration=None, investor="foreigner", listings=listings
    )
    validate_document(document)
    row = document["rows"][0]
    assert row["net_amount"] == "386487682000"
    assert row["last_price"] is None
    assert row["listing"]["instrument"]["instrument_id"] == "kr:005930:xkrx:2026"
    assert row["local_name"] == "삼성전자"
    assert document["ranked_at_utc"] == "2026-10-20T06:41:19Z"


def test_investor_rankings_pick_one_investor_and_side() -> None:
    sold = read(session(), "NET_SELL", "KR", duration=None, investor="foreigner")
    assert [row["symbol"] for row in sold["rows"]] == ["000660"]
    assert sold["rows"][0]["net_amount"] == "391201805000"
    empty = read(session(), "NET_BUY", "KR", duration=None, investor="institution")
    assert empty["rows"] == []
    assert empty["ranked_at_utc"] is None


def test_an_empty_ranking_is_not_an_error() -> None:
    body = {"result": {"rankedAt": None, "rankings": []}}
    document = read(session(body=body), "TOP_GAINERS", duration="1w")
    validate_document(document)
    assert document["rows"] == []


def test_a_non_decimal_value_is_refused() -> None:
    body = json.loads(json.dumps(RANKINGS))
    body["result"]["rankings"][0]["tradingAmount"] = "1.2e9"
    with pytest.raises(ProviderDataError, match="violates the contract"):
        read(session(body=body), "MARKET_TRADING_AMOUNT")


@pytest.mark.parametrize(
    ("ranking", "market", "options", "message"),
    [
        ("TOP_GAINERS", "US", {"duration": "realtime"}, "no realtime ranking"),
        ("TOP_LOSERS", "US", {"duration": None}, "needs a duration"),
        ("MARKET_TRADING_AMOUNT", "US", {"investor": "foreigner"}, "takes no investor"),
        ("NET_BUY", "US", {"duration": None, "investor": "foreigner"}, "Korean stocks only"),
        ("NET_BUY", "KR", {"duration": None}, "needs an investor"),
        ("NET_SELL", "KR", {"investor": "individual"}, "latest business day only"),
        (
            "NET_SELL",
            "KR",
            {"duration": None, "investor": "individual", "exclude_caution": True},
            "cannot exclude caution",
        ),
        ("MARKET_TRADING_VOLUME", "US", {"count": 101}, "between 1 and 100"),
        ("ORDERS", "US", {}, "unknown ranking"),
    ],
)
def test_invalid_choices_are_refused_before_any_request(
    ranking: str, market: str, options: dict[str, Any], message: str
) -> None:
    requests: list[httpx.Request] = []
    with pytest.raises(InvalidBarsQueryError, match=message):
        read(session(requests), ranking, market, **options)
    assert requests == []


def test_rankings_api_resolves_configured_instruments() -> None:
    toss = session()
    app = create_app(
        FakeMarketDataProvider.from_path(), toss_session=lambda: toss, clock=lambda: NOW
    )
    with TestClient(app) as client:
        ok = client.get("/v1/rankings", params={"type": "MARKET_TRADING_AMOUNT", "duration": "1d"})
        assert ok.status_code == 200
        document = ok.json()
        validate_document(document)
        nvda, aapl = document["rows"]
        assert nvda["listing"] is None
        assert aapl["listing"]["instrument"]["instrument_id"] == "us:aapl:xnas:2026"
        refused = client.get("/v1/rankings", params={"type": "TOP_GAINERS", "duration": "realtime"})
        assert refused.status_code == 422
        assert client.get("/v1/rankings", params={"type": "SECRET"}).status_code == 422


def test_rankings_api_without_a_toss_key() -> None:
    app = create_app(FakeMarketDataProvider.from_path(), toss_session=lambda: None)
    with TestClient(app) as client:
        response = client.get("/v1/rankings", params={"type": "TOP_LOSERS", "duration": "1d"})
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "quote_source_unavailable"
