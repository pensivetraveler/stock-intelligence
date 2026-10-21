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
from services.market_data.provider import BarsQuery, InvalidBarsQueryError, ProviderAuthError
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
        assert params["symbol"] == ["005930"]
        adjusted = params["adjusted"] == ["true"]
        assert adjusted or params["adjusted"] == ["false"]
        assert not adjusted or params["interval"] == ["1d"]
        if adjusted:
            # Prices before a 50:1 split, divided by 50.
            candles = [
                {**daily(day, close), "openPrice": "4420", "highPrice": "4480", "lowPrice": "4160"}
                for day, close in (("2026-10-20", "4180"), ("2026-10-19", "4470"))
            ]
        elif params["interval"] == ["1m"]:
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


def test_toss_korean_adjusted_daily_bars_are_split_adjusted_and_dated() -> None:
    toss = Toss()
    provider = korean(toss)
    query = BarsQuery(
        SAMSUNG,
        "1d",
        datetime(2026, 10, 19, tzinfo=timezone.utc),
        datetime(2026, 10, 21, tzinfo=timezone.utc),
    )
    result = provider.get_adjusted_daily_bars(query, "split_adjusted")
    validate_document(result.to_document())
    assert [bar["close"] for bar in result.bars] == ["4180"]
    assert result.coverage["adjustment"] == "split_adjusted"
    assert result.coverage["dataset_revision"] == "toss-kr-adjusted-r1:1d:split:asof-2026-10-20"
    assert parse_qs(toss.requests[-1].url.query.decode())["adjusted"] == ["true"]
    # Toss's Korean adjustment leaves cash dividends out (live check 2026-10-21).
    with pytest.raises(InvalidBarsQueryError, match="cash dividends"):
        provider.get_adjusted_daily_bars(query, "fully_adjusted")
    with pytest.raises(InvalidBarsQueryError, match="split_adjusted"):
        provider.get_adjusted_daily_bars(query, "raw")
    # Toss's US adjusted candles are unchecked.
    us = TossMarketDataProvider(
        TossConfig(client_id="us-client", client_secret="test-secret-not-real"),
        transport=httpx.MockTransport(Toss()),
        now=lambda: NOW,
    )
    us_query = BarsQuery("us:aapl:xnas:2026", "1d", query.start_utc, query.end_utc)
    with pytest.raises(InvalidBarsQueryError, match="unchecked"):
        us.get_adjusted_daily_bars(us_query, "split_adjusted")


def test_korean_adjusted_bars_are_routed_and_never_cached(tmp_path: Path) -> None:
    us = FakeMarketDataProvider.from_path()
    cached = CachedMarketDataProvider(korean(Toss()), LocalMarketDataStore(tmp_path))
    router = MarketRouter(us, lambda: cached)
    query = BarsQuery(
        SAMSUNG,
        "1d",
        datetime(2026, 10, 19, tzinfo=timezone.utc),
        datetime(2026, 10, 21, tzinfo=timezone.utc),
    )
    result = router.get_adjusted_daily_bars(query, "split_adjusted")
    assert result.coverage["source_id"] == "toss-kr"
    assert [bar["close"] for bar in result.bars] == ["4180"]
    assert not list(tmp_path.rglob("*.parquet"))


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


def test_ledger_records_of_korean_listings_must_be_in_krw() -> None:
    from packages.contracts.validator import validation_errors

    record = json.loads(Path("tests/fixtures/contracts/valid/manual-execution.json").read_text())
    korean = {**record, "instrument_id": SAMSUNG, "currency": "KRW"}
    assert validation_errors(korean) == []
    assert "kr:005930:xkrx:2026 trades in KRW" in validation_errors({**korean, "currency": "USD"})


def test_manual_entries_for_korean_stocks_use_kst_and_krw(tmp_path: Path) -> None:
    from services.journal.store import JournalStore
    from services.market_data.symbol_directory import SymbolDirectory

    korean_dir = SymbolDirectory(
        tmp_path, "toss-kr", lambda: [{"symbol": "005930", "name": "삼성전자", "venue": "XKRX"}]
    )
    korean_dir.search("삼성")  # fetch and keep the list
    app = create_app(
        FakeMarketDataProvider.from_path(),
        journal_store=JournalStore(tmp_path / "journal"),
        korean_directory=korean_dir,
        toss_session=lambda: listing_session(),
        clock=lambda: NOW,
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/trades/manual",
            json={
                "execution_id": "kr-fill-1",
                "instrument_id": SAMSUNG,
                "side": "buy",
                "quantity": "10",
                "price": "211000",
                "time_precision": "minute",
                # Exchange-local time: 09:05 KST.
                "executed_date_et": "2026-10-20",
                "executed_time_et": "09:05:00",
                "selected_interval_start_utc": "2026-10-20T00:05:00Z",
                "selected_interval_end_utc": "2026-10-20T00:06:00Z",
            },
        )
        assert response.status_code == 201, response.text
        stored = response.json()
        assert (stored["currency"], stored["executed_at_utc"]) == ("KRW", "2026-10-20T00:05:00Z")


def test_realtime_relays_korean_codes_on_kr_topics_and_dates_ticks_in_kst(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from services.market_data import toss_stream
    from services.market_data.tick_store import TickStore
    from services.market_data.toss_stream import RealtimeHub

    monkeypatch.setattr(toss_stream, "DECLARE_INTERVAL", 0.0)
    monkeypatch.setattr(toss_stream, "SYNC_DELAY", 0.0)
    store = TickStore(tmp_path)
    sent: list[str] = []
    incoming: asyncio.Queue[str] = asyncio.Queue()

    class Connection:
        async def send(self, message: str) -> None:
            sent.append(message)

        async def recv(self) -> str:
            return await incoming.get()

        async def close(self) -> None:
            pass

    async def connect(_url: str, _headers: dict[str, str]) -> Connection:
        return Connection()

    async def scenario() -> dict[str, Any]:
        session = listing_session()
        hub = RealtimeHub(
            lambda: session,
            ticks=lambda: store,
            instrument_for=lambda code, currency: SAMSUNG if currency == "KRW" else None,
            sync_ledger=lambda _since: None,
            connect=connect,
            now=lambda: NOW,
        )
        client = hub.add_client(frozenset({"005930", "AAPL"}))
        for _ in range(100):
            if sent:
                break
            await asyncio.sleep(0.01)
        await incoming.put(
            json.dumps(
                {
                    "type": "message",
                    "topic": "trade:kr:005930",
                    "data": {
                        "price": "211000",
                        "volume": "3",
                        "timestamp": "2026-10-20T08:30:00.000+09:00",
                        "currency": "KRW",
                    },
                }
            )
        )
        event: dict[str, Any] = {}
        while event.get("event") != "trade":
            event = await asyncio.wait_for(client.queue.get(), 2)
        hub.remove_client(client)
        await incoming.put("{}")
        await asyncio.wait_for(hub.stopped(), 5)
        return event

    event = asyncio.run(scenario())
    declared = json.loads(sent[0])
    assert {"type": "trade:kr", "codes": ["005930"]} in declared
    assert {"type": "trade:us", "codes": ["AAPL"]} in declared
    validate_document(event)
    assert (event["source_id"], event["currency"], event["instrument_id"]) == (
        "toss-kr",
        "KRW",
        SAMSUNG,
    )
    # 08:30 KST on the 20th is 23:30Z on the 19th: the tick is filed under the 20th.
    assert [row["price"] for row in store.read("toss-kr", "005930", "2026-10-20")] == ["211000"]
