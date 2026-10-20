"""One Toss realtime WebSocket shared by every screen of the app (AsyncAPI 1.2.2).

The access token stays in this process (a browser could not send the Authorization header
anyway), so the API keeps the only Toss connection and relays events to the app's own
`/v1/realtime` clients. It runs only while at least one client is connected.

Toss rules followed here:
- subscriptions are declared as one JSON array that replaces the previous one; at most
  100 topics per connection (we cap symbols so trade + orderbook stay inside it) and at
  most 5 declarations a second;
- a text `PING` every 60 seconds, or Toss closes an idle connection after 180;
- reconnect with exponential backoff and jitter, closing the old connection first;
- trade and order book frames have no sequence numbers and can be dropped; order events
  are lossless only within one connection, so after any (re)connect and after an order
  closes, the broker ledger is resynced from REST.

Trade ticks are stored by `TickStore` (marked `lossy_stream`); nothing here builds bars.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from services.market_data.tick_store import Tick, TickStore, now_utc_text
from services.market_data.toss_session import TossSession

LOG = logging.getLogger(__name__)
URL = "wss://openapi-ws.tossinvest.com/ws/v1"
SOURCE_ID = "toss-us"
# Per market: Toss topic suffix, source ID and currency. Korean codes start with a digit
# and US tickers with a letter, so a symbol's market is never ambiguous.
MARKETS = {"us": ("toss-us", "USD"), "kr": ("toss-kr", "KRW")}


def market_key(symbol: str) -> str:
    return "kr" if symbol[:1].isdigit() else "us"


MAX_SYMBOLS = 45  # trade + orderbook per symbol, plus account topics, stay under 100
PING_SECONDS = 60.0
FLUSH_SECONDS = 10.0
DECLARE_INTERVAL = 0.25  # well under 5 declarations a second
SYNC_DELAY = 3.0  # wait for a burst of order events before resyncing
CLOSED_STATUSES = frozenset(
    {"FILLED", "CANCELED", "REJECTED", "REPLACED", "EXPIRED", "PARTIAL_CANCELED"}
)
SEOUL = ZoneInfo("Asia/Seoul")


class Connection(Protocol):
    async def send(self, message: str) -> None: ...

    async def recv(self) -> str: ...

    async def close(self) -> None: ...


Connect = Callable[[str, dict[str, str]], Awaitable[Connection]]


async def default_connect(url: str, headers: dict[str, str]) -> Connection:
    from websockets.asyncio.client import connect

    return await connect(url, additional_headers=headers, open_timeout=10, max_size=2**20)  # type: ignore[return-value]


@dataclass(eq=False)
class Client:
    symbols: frozenset[str] = frozenset()
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=lambda: asyncio.Queue(maxsize=500))

    def put(self, event: dict[str, Any]) -> None:
        if self.queue.full():
            # A slow screen loses its oldest event, never the stream.
            with contextlib.suppress(asyncio.QueueEmpty):
                self.queue.get_nowait()
        self.queue.put_nowait(event)


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _event(kind: str, **fields: Any) -> dict[str, Any]:
    return {"kind": "realtime_event", "schema_version": "0.1", "event": kind, **fields}


def _utc(value: object) -> str | None:
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        return None
    return stamp.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _decimal(value: object) -> str | None:
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return format(number, "f") if number.is_finite() and number >= 0 else None


class RealtimeHub:
    def __init__(
        self,
        session: Callable[[], TossSession | None],
        *,
        ticks: Callable[[], TickStore | None],
        instrument_for: Callable[[str, str], str | None],
        sync_ledger: Callable[[str], dict[str, Any] | None],
        connect: Connect = default_connect,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        url: str = URL,
    ) -> None:
        self._session = session
        self._ticks = ticks
        self._instrument_for = instrument_for
        self._sync_ledger = sync_ledger
        self._connect = connect
        self._now = now
        self._url = url
        self._clients: set[Client] = set()
        self._task: asyncio.Task[None] | None = None
        self._resubscribe = asyncio.Event()
        self._sync_task: asyncio.Task[None] | None = None
        self.connected = False

    # --- clients -------------------------------------------------------------------

    def add_client(self, symbols: frozenset[str]) -> Client:
        client = Client(symbols)
        self._clients.add(client)
        client.put(self._status(self.connected, None))
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
        self._resubscribe.set()
        return client

    def update_client(self, client: Client, symbols: frozenset[str]) -> None:
        client.symbols = symbols
        self._resubscribe.set()

    def remove_client(self, client: Client) -> None:
        self._clients.discard(client)
        self._resubscribe.set()

    async def stopped(self) -> None:
        """Wait until the connection loop has ended (after the last client left)."""
        if self._task is not None:
            await self._task

    def symbols(self) -> list[str]:
        merged = sorted({symbol for client in self._clients for symbol in client.symbols})
        return merged[:MAX_SYMBOLS]

    def _broadcast(self, event: dict[str, Any], symbol: str | None = None) -> None:
        for client in list(self._clients):
            if symbol is None or symbol in client.symbols:
                client.put(event)

    def _status(self, connected: bool, detail: str | None, **extra: Any) -> dict[str, Any]:
        return _event(
            "status",
            connected=connected,
            detail=detail,
            subscribed=extra.get("subscribed", []),
            rejected=extra.get("rejected", []),
        )

    # --- connection loop -------------------------------------------------------------

    async def _run(self) -> None:
        attempt = 0
        while self._clients:
            session = self._session()
            if session is None:
                self._broadcast(self._status(False, "no enabled real-account Toss key"))
                return
            try:
                await self._serve(session)
                attempt = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # network, auth or protocol: retry with backoff
                LOG.info("Toss realtime connection ended: %s", type(exc).__name__)
                self._broadcast(self._status(False, f"{type(exc).__name__}: {str(exc)[:160]}"))
            self.connected = False
            if not self._clients:
                break
            delay = min(60.0, 2.0**attempt) * (0.5 + random.random() / 2)
            attempt += 1
            await asyncio.sleep(delay)
        await self._flush_ticks()

    async def _serve(self, session: TossSession) -> None:
        token = await asyncio.to_thread(session.token.get)
        accounts = await asyncio.to_thread(session.get, "/api/v1/accounts")
        account_codes = [
            str(item["accountSeq"])
            for item in (accounts if isinstance(accounts, list) else [])
            if isinstance(item, dict) and item.get("accountSeq") is not None
        ]
        connection = await self._connect(self._url, {"Authorization": f"Bearer {token}"})
        self.connected = True
        self._schedule_sync()  # events missed while disconnected come from REST
        tasks = [
            asyncio.create_task(self._declare_loop(connection, account_codes)),
            asyncio.create_task(self._ping_loop(connection)),
            asyncio.create_task(self._flush_loop()),
        ]
        try:
            while self._clients:
                message = await connection.recv()
                self._dispatch(message, session)
        finally:
            for task in tasks:
                task.cancel()
            await connection.close()

    async def _declare_loop(self, connection: Connection, accounts: list[str]) -> None:
        declared: list[dict[str, Any]] | None = None
        self._resubscribe.set()
        while True:
            await self._resubscribe.wait()
            self._resubscribe.clear()
            symbols = self.symbols()
            declaration: list[dict[str, Any]] = []
            for key in MARKETS:
                codes = [symbol for symbol in symbols if market_key(symbol) == key]
                if codes:
                    declaration += [
                        {"type": f"trade:{key}", "codes": codes},
                        {"type": f"orderbook:{key}", "codes": codes},
                    ]
            if accounts:
                declaration.append({"type": "personal:order", "codes": accounts})
            if declaration != declared:
                await connection.send(json.dumps(declaration))
                declared = declaration
            await asyncio.sleep(DECLARE_INTERVAL)

    async def _ping_loop(self, connection: Connection) -> None:
        while True:
            await asyncio.sleep(PING_SECONDS)
            await connection.send("PING")

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_SECONDS)
            await self._flush_ticks()

    async def _flush_ticks(self) -> None:
        store = self._ticks()
        if store is not None:
            await asyncio.to_thread(store.flush)

    # --- frames -------------------------------------------------------------------

    def _dispatch(self, message: str, session: TossSession) -> None:
        try:
            frame = json.loads(message)
        except ValueError:
            return
        if not isinstance(frame, dict):
            return
        kind = frame.get("type")
        if kind == "subscriptions":
            self._broadcast(
                self._status(
                    True,
                    None,
                    subscribed=[str(item) for item in frame.get("subscribed") or []][:100],
                    rejected=[
                        str(item.get("topic") if isinstance(item, dict) else item)
                        for item in frame.get("rejected") or []
                    ][:100],
                )
            )
        elif kind == "error":
            error = _as_dict(frame.get("error"))
            code = str(error.get("code", "error"))
            self._broadcast(self._status(code != "server-shutdown", code[:160]))
            if code == "server-shutdown":
                raise ConnectionError("Toss is restarting its realtime server")
        elif kind == "message":
            topic = str(frame.get("topic", ""))
            data = _as_dict(frame.get("data"))
            channel, _, rest = topic.partition(":")
            key, _, symbol = rest.partition(":")
            if channel == "trade" and key in MARKETS and symbol:
                self._trade(symbol, data, key)
            elif channel == "orderbook" and key in MARKETS and symbol:
                self._orderbook(symbol, data, key)
            elif topic.startswith("personal:order:"):
                self._order(data, session)

    def _trade(self, symbol: str, data: dict[str, Any], key: str) -> None:
        price, volume = _decimal(data.get("price")), _decimal(data.get("volume"))
        at = _utc(data.get("timestamp"))
        if price is None or volume is None or at is None:
            return
        source_id, default_currency = MARKETS[key]
        received = now_utc_text(self._now)
        currency = str(data.get("currency") or default_currency)[:3]
        instrument_id = self._instrument_for(symbol, currency)
        store = self._ticks()
        if store is not None:
            store.add(Tick(source_id, symbol, instrument_id, price, volume, currency, at, received))
        self._broadcast(
            _event(
                "trade",
                source_id=source_id,
                symbol=symbol,
                instrument_id=instrument_id,
                price=price,
                volume=volume,
                currency=currency,
                exchange_time_utc=at,
                received_at_utc=received,
            ),
            symbol,
        )

    def _orderbook(self, symbol: str, data: dict[str, Any], key: str) -> None:
        at = _utc(data.get("timestamp"))
        if at is None:
            return
        source_id, default_currency = MARKETS[key]
        currency = str(data.get("currency") or default_currency)[:3]

        def levels(items: object) -> list[dict[str, str]]:
            out = []
            for item in items if isinstance(items, list) else []:
                if isinstance(item, dict):
                    price, volume = _decimal(item.get("price")), _decimal(item.get("volume"))
                    if price is not None and volume is not None:
                        out.append({"price": price, "volume": volume})
            return out

        self._broadcast(
            _event(
                "orderbook",
                source_id=source_id,
                symbol=symbol,
                instrument_id=self._instrument_for(symbol, currency),
                currency=currency,
                exchange_time_utc=at,
                asks=levels(data.get("asks")),
                bids=levels(data.get("bids")),
            ),
            symbol,
        )

    def _order(self, data: dict[str, Any], session: TossSession) -> None:
        order = _as_dict(data.get("order"))
        execution = _as_dict(order.get("execution"))
        status = str(order.get("status") or "UNKNOWN")[:40]
        self._broadcast(
            _event(
                "order",
                broker_id="toss",
                account_ref=f"toss-{session.key_ref}-{data.get('accountSeq', '?')}",
                order_event=str(data.get("event") or "UNKNOWN")[:40],
                symbol=str(order.get("symbol") or "?")[:32],
                side=str(order.get("side") or "?")[:10],
                status=status,
                filled_quantity=_decimal(execution.get("filledQuantity")),
            )
        )
        if status in CLOSED_STATUSES or data.get("event") == "FILL":
            self._schedule_sync()

    # --- ledger -------------------------------------------------------------------

    def _schedule_sync(self) -> None:
        if self._sync_task is not None and not self._sync_task.done():
            return
        self._sync_task = asyncio.create_task(self._sync_soon())

    async def _sync_soon(self) -> None:
        await asyncio.sleep(SYNC_DELAY)
        # Recent orders only (KST order dates), with a margin for time zones and weekends.
        since = (self._now().astimezone(SEOUL) - timedelta(days=4)).date().isoformat()
        try:
            result = await asyncio.to_thread(self._sync_ledger, since)
        except Exception as exc:
            self._broadcast(self._status(self.connected, f"ledger sync failed: {str(exc)[:120]}"))
            return
        if result is not None:
            self._broadcast(
                _event(
                    "ledger_synced",
                    stored_new=int(result["stored_new"]),
                    conflicts=len(result["conflicts"]),
                )
            )
