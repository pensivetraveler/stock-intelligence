"""Toss realtime relay, tick store and the /v1/realtime endpoint (no network).

Frames follow the official AsyncAPI 1.2.2 examples.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from packages.contracts.validator import validate_document
from services.api.main import create_app
from services.market_data import toss_stream
from services.market_data.fake import FakeMarketDataProvider
from services.market_data.tick_store import Tick, TickStore
from services.market_data.toss_session import TossCredentials, TossSession
from services.market_data.toss_stream import RealtimeHub

NOW = datetime(2026, 10, 20, 14, 0, tzinfo=timezone.utc)


def test_ticks_are_kept_apart_marked_lossy_and_grouped_by_new_york_date(tmp_path: Path) -> None:
    store = TickStore(tmp_path, monotonic=lambda: 0.0)
    for at, price in (("2026-10-20T13:30:00.120Z", "250.10"), ("2026-10-21T01:00:00.000Z", "251")):
        store.add(Tick("toss-us", "AAPL", "us:aapl:xnas:2026", price, "5", "USD", at, at))
    assert store.flush() == 2
    # 01:00Z on the 21st is still the evening of the 20th in New York.
    rows = store.read("toss-us", "AAPL", "2026-10-20")
    assert [row["price"] for row in rows] == ["250.10", "251"]
    assert {row["completeness"] for row in rows} == {"lossy_stream"}
    assert list((tmp_path / "ticks").rglob("*.tmp")) == []
    # Path parts are percent-encoded, so "../" cannot leave the tick folder.
    assert store.read("toss-us", "../../etc", "2026-10-20") == []


class FakeConnection:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.incoming: asyncio.Queue[str] = asyncio.Queue()
        self.closed = False

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        return await self.incoming.get()

    async def close(self) -> None:
        self.closed = True


def toss_session() -> TossSession:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(200, json={"result": [{"accountNo": "12345678901", "accountSeq": 3}]})

    return TossSession(
        TossCredentials("stream-client", "test-secret-not-real"),
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
    )


def test_hub_declares_relays_stores_ticks_and_resyncs_the_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(toss_stream, "SYNC_DELAY", 0.0)
    monkeypatch.setattr(toss_stream, "DECLARE_INTERVAL", 0.0)
    store = TickStore(tmp_path)
    syncs: list[str] = []
    connection = FakeConnection()
    headers: list[dict[str, str]] = []

    async def connect(url: str, given: dict[str, str]) -> FakeConnection:
        headers.append(given)
        return connection

    def sync(since: str) -> dict[str, Any]:
        syncs.append(since)
        return {"stored_new": 1, "conflicts": []}

    async def scenario() -> list[dict[str, Any]]:
        session = toss_session()
        hub = RealtimeHub(
            lambda: session,
            ticks=lambda: store,
            instrument_for={"AAPL": "us:aapl:xnas:2026"}.get,
            sync_ledger=sync,
            connect=connect,
            now=lambda: NOW,
        )
        client = hub.add_client(frozenset({"AAPL"}))
        other = hub.add_client(frozenset({"TSLA"}))
        for _ in range(50):
            if connection.sent:
                break
            await asyncio.sleep(0.01)
        for frame in (
            {"type": "subscriptions", "subscribed": ["trade:us:AAPL"], "rejected": []},
            {
                "type": "message",
                "topic": "trade:us:AAPL",
                "data": {
                    "price": "243.26",
                    "volume": "8",
                    "timestamp": "2026-10-20T23:30:00.000+09:00",
                    "currency": "USD",
                },
            },
            {
                "type": "message",
                "topic": "orderbook:us:AAPL",
                "data": {
                    "timestamp": "2026-10-20T23:30:00.000+09:00",
                    "currency": "USD",
                    "asks": [{"price": "243.3", "volume": "5"}],
                    "bids": [],
                },
            },
            {
                "type": "message",
                "topic": "personal:order:3",
                "data": {
                    "event": "FILL",
                    "accountSeq": "3",
                    "order": {
                        "orderId": "o1",
                        "symbol": "AAPL",
                        "side": "BUY",
                        "status": "FILLED",
                        "execution": {"filledQuantity": "10"},
                    },
                },
            },
        ):
            await connection.incoming.put(json.dumps(frame))
        events: list[dict[str, Any]] = []
        while len(events) < 7:
            events.append(await asyncio.wait_for(client.queue.get(), 2))
        assert all(event["event"] != "trade" for event in _drain(other))  # filtered by symbol
        hub.remove_client(client)
        hub.remove_client(other)
        await connection.incoming.put("{}")
        await asyncio.wait_for(hub.stopped(), 5)  # includes the final tick flush
        return events

    events = asyncio.run(scenario())
    assert headers == [{"Authorization": "Bearer t"}]
    declared = json.loads(connection.sent[0])
    assert {"type": "trade:us", "codes": ["AAPL", "TSLA"]} in declared
    assert {"type": "personal:order", "codes": ["3"]} in declared
    for event in events:
        validate_document(event)
    kinds = [event["event"] for event in events]
    assert {"trade", "orderbook", "order", "ledger_synced"} <= set(kinds)
    trade = next(event for event in events if event["event"] == "trade")
    assert trade["exchange_time_utc"] == "2026-10-20T14:30:00.000Z"
    assert syncs and syncs[0] == "2026-10-16"  # recent orders only (KST dates)
    assert connection.closed
    assert [row["price"] for row in store.read("toss-us", "AAPL", "2026-10-20")] == ["243.26"]


def _drain(client: toss_stream.Client) -> list[dict[str, Any]]:
    items = []
    while not client.queue.empty():
        items.append(client.queue.get_nowait())
    return items


def test_realtime_endpoint_rejects_other_origins_and_reports_a_missing_key() -> None:
    app = create_app(FakeMarketDataProvider.from_path(), toss_session=lambda: None)
    with TestClient(app) as client:
        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect(
                "/v1/realtime", headers={"origin": "https://evil.example"}
            ) as socket,
        ):
            socket.receive_json()
        with client.websocket_connect(
            "/v1/realtime", headers={"origin": "http://127.0.0.1:5173"}
        ) as socket:
            socket.send_json({"symbols": ["aapl", "bad symbol!"]})
            first = socket.receive_json()
            validate_document(first)
            assert first["event"] == "status"
            second = socket.receive_json()
            assert second["detail"] == "no enabled real-account Toss key"
