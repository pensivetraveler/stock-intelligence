"""Toss account view and order-execution sync into the broker ledger (no network).

Payloads follow the official OpenAPI 1.2.24 examples.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient

from packages.contracts.validator import validate_document
from services.api.main import create_app
from services.journal.store import JournalStore
from services.market_data.fake import FakeMarketDataProvider
from services.market_data.toss_account import account_snapshot, sync_executions
from services.market_data.toss_session import TossCredentials, TossSession

NOW = datetime(2026, 10, 20, 13, 0, tzinfo=timezone.utc)
ACCOUNT_NO = "12345678901"


def order(
    order_id: str, symbol: str, currency: str, filled: str, price: str | None
) -> dict[str, Any]:
    return {
        "orderId": order_id,
        "symbol": symbol,
        "side": "BUY",
        "orderType": "LIMIT",
        "timeInForce": "DAY",
        "status": "FILLED" if filled != "0" else "CANCELED",
        "price": price or "1",
        "quantity": filled,
        "orderAmount": None,
        "currency": currency,
        "orderedAt": "2026-10-16T22:30:01+09:00",
        "canceledAt": None,
        "execution": {
            "filledQuantity": filled,
            "averageFilledPrice": price,
            "filledAmount": None,
            "commission": "0.25",
            "tax": "0",
            "filledAt": "2026-10-16T22:30:05+09:00" if price else None,
            "settlementDate": "2026-10-19",
        },
    }


class Broker:
    def __init__(self) -> None:
        self.closed_pages = [
            {
                "orders": [
                    order("o-aapl", "AAPL", "USD", "10", "250.10"),
                    order("o-kr", "005930", "KRW", "5", "70000"),
                ],
                "nextCursor": "page-2",
                "hasNext": True,
            },
            {
                "orders": [
                    order("o-none", "MSFT", "USD", "0", None),
                    order("o-otc", "ZZZZ", "USD", "1", "1.5"),
                ],
                "nextCursor": None,
                "hasNext": False,
            },
        ]
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, params = request.url.path, request.url.params
        if path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if path == "/api/v1/accounts":
            body: Any = [{"accountNo": ACCOUNT_NO, "accountSeq": 1, "accountType": "BROKERAGE"}]
        elif path == "/api/v1/orders" and params["status"] == "CLOSED":
            body = self.closed_pages[1 if params.get("cursor") == "page-2" else 0]
        elif path == "/api/v1/orders":
            body = {
                "orders": [order("o-open", "NVDA", "USD", "0", None)],
                "nextCursor": None,
                "hasNext": False,
            }
        elif path == "/api/v1/holdings":
            body = {
                "totalPurchaseAmount": {"krw": "0", "usd": "1553"},
                "marketValue": {"amount": {"krw": "0", "usd": "1785"}},
                "profitLoss": {"amount": {"krw": "0", "usd": "232"}, "rate": "0.1494"},
                "dailyProfitLoss": {"amount": {"krw": "0", "usd": "25"}, "rate": "0.0142"},
                "items": [
                    {
                        "symbol": "AAPL",
                        "name": "Apple Inc.",
                        "marketCountry": "US",
                        "currency": "USD",
                        "quantity": "10",
                        "lastPrice": "178.5",
                        "averagePurchasePrice": "155.3",
                        "marketValue": {"purchaseAmount": "1553", "amount": "1785"},
                        "profitLoss": {"amount": "232", "rate": "0.1494"},
                        "dailyProfitLoss": {"amount": "25"},
                        "cost": {"commission": "3.57", "tax": "10"},
                    }
                ],
            }
        elif path == "/api/v1/buying-power":
            if params["currency"] == "KRW":
                return httpx.Response(500, text="oops")
            body = {"currency": "USD", "cashBuyingPower": "5000.5"}
        elif path == "/api/v1/commissions":
            body = [
                {
                    "marketCountry": "US",
                    "commissionRate": "0.001",
                    "startDate": None,
                    "endDate": "2026-12-31",
                }
            ]
        elif path == "/api/v1/conditional-orders":
            body = {"conditionalOrders": [], "nextCursor": None, "hasNext": False}
        elif path == "/api/v1/sellable-quantity":
            body = {"sellableQuantity": "7"}
        else:
            return httpx.Response(404, json={"error": {"code": "not-found"}})
        return httpx.Response(200, text=json.dumps({"result": body}))


def session(broker: Broker) -> TossSession:
    return TossSession(
        TossCredentials("account-client", "test-secret-not-real"),
        transport=httpx.MockTransport(broker),
        sleep=lambda _seconds: None,
    )


def instrument_for(symbol: str, currency: str) -> str | None:
    known = {
        ("AAPL", "USD"): "us:aapl:xnas:2026",
        ("MSFT", "USD"): "us:msft:xnas:2026",
        ("005930", "KRW"): "kr:005930:xkrx:2026",
    }
    return known.get((symbol, currency))


def test_account_view_masks_the_account_and_reports_failed_parts() -> None:
    snapshot = account_snapshot(session(Broker()), lambda: NOW)
    validate_document(snapshot)
    assert ACCOUNT_NO not in json.dumps(snapshot)
    account = snapshot["accounts"][0]
    assert account["account_suffix"] == "8901"
    assert account["holdings"][0]["average_purchase_price"] == "155.3"
    assert account["summary"]["market_value"] == {"krw": "0", "usd": "1785"}
    assert account["buying_power"] == {"usd": "5000.5", "krw": None}
    assert account["errors"] and account["errors"][0].startswith("buying power KRW")
    assert account["open_orders"][0]["symbol"] == "NVDA"


def test_sync_stores_each_filled_us_order_once_as_an_order_aggregate(tmp_path: Path) -> None:
    broker = Broker()
    store = JournalStore(tmp_path)
    first = sync_executions(session(broker), store, instrument_for, lambda: NOW)
    validate_document(first)
    assert (first["orders_seen"], first["stored_new"]) == (4, 2)
    assert first["skipped"] == {
        "not_filled": 1,
        "unsupported_currency": 0,
        "unknown_instrument": 1,
    }
    assert [item["orders"] for item in first["unknown_symbols"]] == [1]
    # The Korean order is stored in KRW under its Korean listing.
    korean = next(row for row in store.broker_executions() if row["currency"] == "KRW")
    assert (korean["instrument_id"], korean["price"]) == ("kr:005930:xkrx:2026", "70000")
    [stored] = [row for row in store.broker_executions() if row["currency"] == "USD"]
    assert stored["granularity"] == "order_aggregate"
    assert stored["time_meaning"] == "reported_last_fill"
    assert (stored["quantity"], stored["price"], stored["fees"]) == ("10", "250.10", "0.25")
    assert stored["executed_at_utc"] == "2026-10-16T13:30:05Z"
    assert ACCOUNT_NO not in json.dumps(stored)

    again = sync_executions(session(broker), store, instrument_for, lambda: NOW)
    assert (again["stored_new"], again["already_stored"]) == (0, 2)

    # A changed report for a stored order is flagged, never written over.
    changed = Broker()
    pages = copy.deepcopy(changed.closed_pages)
    pages[0]["orders"][0]["execution"]["averageFilledPrice"] = "250.11"
    changed.closed_pages = pages
    third = sync_executions(session(changed), store, instrument_for, lambda: NOW)
    assert third["conflicts"] == ["o-aapl"]
    assert (
        next(row["price"] for row in store.broker_executions() if row["currency"] == "USD")
        == "250.10"
    )


def test_broker_account_api_and_ledger(tmp_path: Path) -> None:
    broker = Broker()
    toss = session(broker)
    app = create_app(
        FakeMarketDataProvider.from_path(),
        journal_store=JournalStore(tmp_path),
        toss_session=lambda: toss,
        clock=lambda: NOW,
    )
    with TestClient(app) as client:
        account = client.get("/v1/brokers/toss/account")
        assert account.status_code == 200
        validate_document(account.json())
        sellable = client.get(
            "/v1/brokers/toss/sellable", params={"account_seq": 1, "symbol": "aapl"}
        )
        assert sellable.json()["sellable_quantity"] == "7"
        synced = client.post("/v1/brokers/toss/sync")
        assert synced.status_code == 200
        ledger = client.get("/v1/trades/broker").json()
        validate_document(ledger)
        assert ledger["ledger"] == "broker_api"
        assert ledger["positions"][0]["open_quantity"] == "10"
    # Only reads were sent to Toss.
    assert {request.method for request in broker.requests} == {"GET", "POST"}
    assert [r.url.path for r in broker.requests if r.method == "POST"] == ["/oauth2/token"]
