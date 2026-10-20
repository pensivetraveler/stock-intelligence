"""Toss stock info, exchange rate, market hours and Korean names (no network).

Payloads follow the official OpenAPI 1.2.24 examples and a live check on 2026-10-20.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient

from packages.contracts.validator import validate_document
from services.api.main import create_app
from services.market_data.calendar import UsEquityCalendar
from services.market_data.fake import FakeMarketDataProvider
from services.market_data.symbol_directory import SymbolDirectory
from services.market_data.toss_info import (
    exchange_rate,
    korean_names,
    market_calendar,
    stock_info,
)
from services.market_data.toss_session import TossCredentials, TossSession

SECRET = "test-secret-not-real"
NOW = datetime(2026, 10, 20, 11, 30, tzinfo=timezone.utc)
CALENDAR = UsEquityCalendar("XNAS")


def us_day(day: str, next_day: str) -> dict[str, Any]:
    return {
        "date": day,
        "dayMarket": {"startTime": f"{day}T09:00:00+09:00", "endTime": f"{day}T17:00:00+09:00"},
        "preMarket": {"startTime": f"{day}T17:00:00+09:00", "endTime": f"{day}T22:30:00+09:00"},
        "regularMarket": {
            "startTime": f"{day}T22:30:00+09:00",
            "endTime": f"{next_day}T05:00:00+09:00",
        },
        # Toss's after-hours session ends at 19:50 ET, ten minutes before the local one.
        "afterMarket": {
            "startTime": f"{next_day}T05:00:00+09:00",
            "endTime": f"{next_day}T08:50:00+09:00",
        },
    }


ROUTES: dict[str, Any] = {
    "/api/v1/stocks": {
        "result": [
            {
                "symbol": "AAPL",
                "name": "애플",
                "englishName": "Apple",
                "isinCode": "US0378331005",
                "market": "NASDAQ",
                "securityType": "STOCK",
                "isCommonShare": True,
                "status": "ACTIVE",
                "currency": "USD",
                "listDate": "1980-12-12",
                "delistDate": None,
                "sharesOutstanding": "14702703000",
                "leverageFactor": None,
                "koreanMarketDetail": None,
            }
        ]
    },
    "/api/v1/stocks/AAPL/warnings": {
        "result": [
            {
                "warningType": "VI_STATIC",
                "exchange": "NASDAQ",
                "startDate": "2026-10-20",
                "endDate": None,
            }
        ]
    },
    "/api/v1/exchange-rate": {
        "result": {
            "baseCurrency": "USD",
            "quoteCurrency": "KRW",
            "rate": "1331.27",
            "midRate": "1330.77",
            "basisPoint": "4",
            "rateChangeType": "DOWN",
            "validFrom": "2026-10-20T20:21:52.000+09:00",
            "validUntil": "2026-10-20T20:26:51.000+09:00",
        }
    },
    "/api/v1/market-calendar/US": {
        "result": {
            "previousBusinessDay": us_day("2026-10-19", "2026-10-20"),
            "today": us_day("2026-10-20", "2026-10-21"),
            "nextBusinessDay": us_day("2026-10-21", "2026-10-22"),
        }
    },
}


def listing(market: str) -> dict[str, Any]:
    names = {
        "NASDAQ": [{"symbol": "AAPL", "name": "애플"}, {"symbol": "QQQ", "name": "QQQ"}],
        "NYSE": [{"symbol": "BRK.B", "name": "버크셔 해서웨이 B"}],
        "AMEX": [{"symbol": "SPY", "name": "SPY"}],
    }
    return {"result": names[market]}


class Server:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if request.url.path == "/api/v1/stocks/all":
            return httpx.Response(200, json=listing(request.url.params["market"]))
        body = ROUTES.get(request.url.path)
        if body is None:
            return httpx.Response(404, json={"error": {"code": "stock-not-found"}})
        return httpx.Response(200, text=json.dumps(body))


def session() -> TossSession:
    return TossSession(
        TossCredentials("info-client", SECRET),
        transport=httpx.MockTransport(Server()),
        sleep=lambda _seconds: None,
    )


def test_stock_info_carries_reference_data_and_active_warnings() -> None:
    info = stock_info(session(), "us:aapl:xnas:2026", "AAPL", lambda: NOW)
    validate_document(info)
    assert (info["name"], info["english_name"], info["isin"]) == ("애플", "Apple", "US0378331005")
    assert info["shares_outstanding"] == "14702703000"
    assert info["warnings"] == [
        {"type": "VI_STATIC", "exchange": "NASDAQ", "start_date": "2026-10-20", "end_date": None}
    ]


def test_exchange_rate_is_exact_and_utc() -> None:
    rate = exchange_rate(session(), "USD", "KRW", lambda: NOW)
    validate_document(rate)
    assert rate["rate"] == "1331.27"
    assert rate["valid_from_utc"] == "2026-10-20T11:21:52Z"


def test_market_hours_are_compared_with_the_local_calendar() -> None:
    hours = market_calendar(session(), "US", date(2026, 10, 20), CALENDAR, lambda: NOW)
    validate_document(hours)
    today = next(day for day in hours["days"] if day["role"] == "today")
    by_name = {item["name"]: item for item in today["sessions"]}
    assert by_name["regular"]["start_utc"] == "2026-10-20T13:30:00Z"
    assert by_name["regular"]["agrees"] is True
    assert by_name["pre_market"]["agrees"] is True
    after = by_name["after_market"]
    assert (after["end_utc"], after["local_end_utc"], after["agrees"]) == (
        "2026-10-20T23:50:00Z",
        "2026-10-21T00:00:00Z",
        False,
    )
    assert by_name["day_market"]["agrees"] is None  # no local overnight session


def test_korean_names_skip_tickers_repeated_as_names(tmp_path: Path) -> None:
    names = korean_names(session())
    assert names == {"AAPL": "애플", "BRK.B": "버크셔 해서웨이 B"}

    def assets() -> list[dict[str, str]]:
        return [
            {"symbol": "AAPL", "name": "Apple Inc. Common Stock", "venue": "XNAS"},
            {"symbol": "BRK.B", "name": "Berkshire Hathaway Class B", "venue": "XNYS"},
        ]

    directory = SymbolDirectory(tmp_path, "alpaca-sip", assets, local_names=lambda: names)
    # An exact Korean name comes first, like an exact ticker.
    assert directory.search("애플").entries[0].symbol == "AAPL"
    found = directory.search("버크셔")
    assert [(entry.symbol, entry.local_name) for entry in found.entries] == [
        ("BRK.B", "버크셔 해서웨이 B")
    ]
    # Kept on disk with the directory.
    reloaded = SymbolDirectory(tmp_path, "alpaca-sip", assets)
    assert reloaded.known()["us:aapl:xnas:2026"].local_name == "애플"


def test_info_endpoints() -> None:
    toss = session()
    app = create_app(
        FakeMarketDataProvider.from_path(), toss_session=lambda: toss, clock=lambda: NOW
    )
    with TestClient(app) as client:
        for path, params in (
            ("/v1/market/stock-info", {"instrument_id": "us:aapl:xnas:2026"}),
            ("/v1/market/exchange-rate", {}),
            ("/v1/market/broker-calendar", {"market": "US", "date": "2026-10-20"}),
        ):
            response = client.get(path, params=params)
            assert response.status_code == 200, (path, response.text)
            validate_document(response.json())
        missing = client.get("/v1/market/stock-info", params={"instrument_id": "us:zzzz:xnas:2026"})
        assert missing.status_code == 422


def test_a_list_saved_without_korean_names_is_refreshed_once(tmp_path: Path) -> None:
    calls: list[int] = []

    def assets() -> list[dict[str, str]]:
        calls.append(1)
        return [{"symbol": "AAPL", "name": "Apple Inc. Common Stock", "venue": "XNAS"}]

    SymbolDirectory(tmp_path, "alpaca-sip", assets, now=lambda: NOW).search("a")
    upgraded = SymbolDirectory(
        tmp_path, "alpaca-sip", assets, local_names=lambda: {"AAPL": "애플"}, now=lambda: NOW
    )
    assert [e.local_name for e in upgraded.search("애플").entries] == ["애플"]
    upgraded.search("애플")
    assert len(calls) == 2  # once for the old list, once to add the names; then cached

    no_names = SymbolDirectory(
        tmp_path / "other", "alpaca-sip", assets, local_names=dict, now=lambda: NOW
    )
    no_names.search("a")
    no_names.search("a")
    assert len(calls) == 3  # no Toss key: tried once, not on every search
