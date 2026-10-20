"""Korean listings: instrument IDs, the KRX calendar, Toss Korean bars and market routing.

Candle payloads follow a live check of Toss Korean candles on 2026-10-20 (Samsung
Electronics 005930: minute bars 08:00-20:00 KST stamped at their end, daily bars at
midnight KST, KRW). No test reaches the network.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient

from packages.contracts.validator import validate_document
from services.api.main import create_app
from services.market_data.fake import FakeMarketDataProvider
from services.market_data.instruments import (
    calendar_for,
    kr_instrument_id,
    market_of,
    parse_instrument,
)
from services.market_data.market_router import MarketRouter
from services.market_data.provider import BarsQuery, ProviderAuthError
from services.market_data.storage import CachedMarketDataProvider, LocalMarketDataStore
from services.market_data.toss import TossConfig, TossMarketDataProvider

SAMSUNG = "kr:005930:xkrx:2026"
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)  # 21:00 KST, after the day


def test_korean_ids_parse_to_market_symbol_and_krx_calendar() -> None:
    parsed = parse_instrument(SAMSUNG)
    assert parsed is not None and (parsed.market, parsed.symbol, parsed.venue) == (
        "KR",
        "005930",
        "XKRX",
    )
    assert kr_instrument_id("0101N0", "XKOS") == "kr:0101n0:xkos:2026"
    assert market_of("us:aapl:xnas:2026") == "US"
    assert market_of("us:synth:xnas:2026") == "US"
    assert calendar_for(SAMSUNG).calendar_id == "XKRX"
    with pytest.raises(ValueError):
        kr_instrument_id("5930", "XKRX")


def minute(end_kst: str, close: str) -> dict[str, str]:
    return {
        "timestamp": f"2026-10-20T{end_kst}:00.000+09:00",
        "openPrice": "222500",
        "highPrice": "222500",
        "lowPrice": "219000",
        "closePrice": close,
        "volume": "176317",
        "currency": "KRW",
    }


def daily(day: str, close: str) -> dict[str, str]:
    return {
        "timestamp": f"{day}T00:00:00.000+09:00",
        "openPrice": "221000",
        "highPrice": "224000",
        "lowPrice": "208000",
        "closePrice": close,
        "volume": "30000000",
        "currency": "KRW",
    }


class Toss:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        params = parse_qs(request.url.query.decode())
        assert params["symbol"] == ["005930"] and params["adjusted"] == ["false"]
        if params["interval"] == ["1m"]:
            candles = [
                minute("08:03", "222000"),
                minute("08:02", "221500"),
                minute("08:01", "221000"),
            ]
        else:
            candles = [daily("2026-10-20", "209000"), daily("2026-10-19", "223500")]
        return httpx.Response(
            200, text=json.dumps({"result": {"candles": candles, "nextBefore": None}})
        )


def korean(toss: Toss) -> TossMarketDataProvider:
    return TossMarketDataProvider(
        TossConfig(client_id="kr-client", client_secret="test-secret-not-real", market="KR"),
        transport=httpx.MockTransport(toss),
        now=lambda: NOW,
    )


def test_toss_korean_minutes_are_krw_on_the_krx_calendar_with_nxt_hours() -> None:
    provider = korean(Toss())
    query = BarsQuery(
        SAMSUNG,
        "1m",
        datetime(2026, 10, 19, 23, 0, tzinfo=timezone.utc),  # 08:00 KST
        datetime(2026, 10, 19, 23, 3, tzinfo=timezone.utc),
    )
    result = provider.get_bars(query)
    validate_document(result.to_document())
    assert [(bar["interval_start_utc"], bar["session"], bar["close"]) for bar in result.bars] == [
        ("2026-10-19T23:00:00Z", "pre", "221000"),
        ("2026-10-19T23:01:00Z", "pre", "221500"),
        ("2026-10-19T23:02:00Z", "pre", "222000"),
    ]
    bar = result.bars[0]
    assert bar["source_id"] == "toss-kr"
    assert bar["source_feed_scope"] == "toss-kr-krx-nxt-integrated"
    assert bar["lineage"]["calendar_id"] == "XKRX"
    # A US instrument is not this adapter's.
    assert provider.symbol_for("us:aapl:xnas:2026") is None


def test_toss_korean_daily_bars_are_dated_in_korea() -> None:
    provider = korean(Toss())
    query = BarsQuery(
        SAMSUNG,
        "1d",
        datetime(2026, 10, 19, tzinfo=timezone.utc),
        datetime(2026, 10, 21, tzinfo=timezone.utc),
    )
    result = provider.get_daily_bars(query)
    validate_document(result.to_document())
    # Toss Korean daily candles span the whole KRX+NXT day, 08:00-20:00 KST.
    assert [
        (bar["interval_start_utc"], bar["interval_end_utc"], bar["session"], bar["close"])
        for bar in result.bars
    ] == [("2026-10-19T23:00:00Z", "2026-10-20T11:00:00Z", "all_day", "209000")]
    # The 19th's day began at 23:00Z on the 18th, before the query, so it is not included.


def test_cached_korean_bars_are_partitioned_by_korean_date(tmp_path: Path) -> None:
    store = LocalMarketDataStore(tmp_path)
    cached = CachedMarketDataProvider(korean(Toss()), store)
    query = BarsQuery(
        SAMSUNG,
        "1m",
        datetime(2026, 10, 19, 23, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 19, 23, 3, tzinfo=timezone.utc),
    )
    cached.get_bars(query)
    # 08:00 KST on the 20th is the 19th in UTC and New York; the file is under the 20th.
    dates = {p.parent.parent.name for p in (tmp_path / "bars").rglob("*.parquet")}
    assert dates == {"session_date=2026-10-20"}


def test_router_sends_korean_listings_to_toss_and_the_rest_to_the_provider() -> None:
    us = FakeMarketDataProvider.from_path()
    toss = Toss()
    router = MarketRouter(us, lambda: korean(toss))
    assert router.source_id == us.source_id
    query = BarsQuery(
        SAMSUNG,
        "1m",
        datetime(2026, 10, 19, 23, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 19, 23, 3, tzinfo=timezone.utc),
    )
    assert router.get_bars(query).coverage["source_id"] == "toss-kr"
    without_key = MarketRouter(us, lambda: None)
    with pytest.raises(ProviderAuthError, match="Toss"):
        without_key.get_bars(query)


def test_api_aggregates_korean_bars_on_the_krx_calendar() -> None:
    app = create_app(FakeMarketDataProvider.from_path(), korean_provider=korean(Toss()))
    params: dict[str, Any] = {
        "instrument_id": SAMSUNG,
        "timeframe": "3m",
        "start_utc": "2026-10-19T23:00:00Z",
        "end_utc": "2026-10-19T23:03:00Z",
    }
    with TestClient(app) as client:
        response = client.get("/v1/market/bars", params=params)
        assert response.status_code == 200, response.text
        document = response.json()
        validate_document(document)
        [bar] = document["bars"]
        assert (bar["session"], bar["open"], bar["close"]) == ("pre", "222500", "222000")
        assert bar["lineage"]["calendar_id"] == "XKRX"
        sessions = client.get(
            "/v1/market/sessions",
            params={"end_date": "2026-10-20", "count": 1, "calendar_id": "XKRX"},
        ).json()
        assert sessions["sessions"][0]["post_close_utc"] == "2026-10-20T11:00:00Z"


def listing_session() -> Any:
    from services.market_data.toss_session import TossCredentials, TossSession

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if request.url.path == "/api/v1/stocks/all":
            market = request.url.params["market"]
            rows = {
                "KOSPI": [
                    {"symbol": "005930", "name": "삼성전자"},
                    {"symbol": "000660", "name": "SK하이닉스"},
                ],
                "KOSDAQ": [{"symbol": "0101N0", "name": "어떤 코스닥 종목"}],
            }[market]
            return httpx.Response(200, json={"result": rows})
        if request.url.path == "/api/v1/prices":
            return httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "symbol": "005930",
                            "timestamp": "2026-10-20T10:00:00+09:00",
                            "lastPrice": "211000",
                            "currency": "KRW",
                        }
                    ]
                },
            )
        if request.url.path == "/api/v1/orderbook":
            return httpx.Response(
                200,
                json={
                    "result": {
                        "timestamp": "2026-10-20T10:00:00+09:00",
                        "currency": "KRW",
                        "asks": [{"price": "211500", "volume": "10"}],
                        "bids": [],
                    }
                },
            )
        if request.url.path == "/api/v1/trades":
            return httpx.Response(200, json={"result": []})
        return httpx.Response(
            200,
            json={
                "result": {
                    "timestamp": "2026-10-20T10:00:00+09:00",
                    "upperLimitPrice": "271000",
                    "lowerLimitPrice": "146000",
                    "currency": "KRW",
                }
            },
        )

    return TossSession(
        TossCredentials("kr-list-client", "test-secret-not-real"),
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
    )


def test_korean_listings_are_searchable_alongside_us_stocks(tmp_path: Path) -> None:
    from services.market_data.symbol_directory import SymbolDirectory, search_all
    from services.market_data.toss_info import korean_listings

    session = listing_session()
    korean_dir = SymbolDirectory(tmp_path, "toss-kr", lambda: korean_listings(session))
    us_dir = SymbolDirectory(
        tmp_path,
        "alpaca-sip",
        lambda: [{"symbol": "SAMS", "name": "Samsara Inc", "venue": "XNYS"}],
    )
    found, _fetched, _stale = search_all([us_dir, korean_dir], "삼성")
    assert [(entry.symbol, entry.instrument_id) for _d, entry in found] == [
        ("005930", "kr:005930:xkrx:2026")
    ]
    by_code, _f, _s = search_all([us_dir, korean_dir], "0101n0")
    directory, entry = by_code[0]
    listing = directory.listing(entry)
    assert listing["instrument"]["instrument_id"] == "kr:0101n0:xkos:2026"
    assert (listing["instrument"]["asset_class"], listing["instrument"]["currency"]) == (
        "kr_equity",
        "KRW",
    )
    app = create_app(
        FakeMarketDataProvider.from_path(),
        symbol_directory=us_dir,
        korean_directory=korean_dir,
        toss_session=lambda: session,
    )
    with TestClient(app) as client:
        response = client.get("/v1/market/symbols", params={"q": "sk"})
        document = response.json()
        validate_document(document)
        assert [r["listing"]["instrument"]["symbol"] for r in document["results"]] == ["000660"]
        quote = client.get("/v1/market/quote", params={"instrument_id": SAMSUNG}).json()
        validate_document(quote)
        assert (quote["source_id"], quote["currency"], quote["last"]["price"]) == (
            "toss-kr",
            "KRW",
            "211000",
        )
        assert quote["price_limits"]["upper"] == "271000"
