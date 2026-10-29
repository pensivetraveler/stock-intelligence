"""One 메리츠증권 realtime WebSocket for US quotes, relayed to the app's own clients.

Spec: openapi.imeritz.com (generated 2026-10-26), group "(실시간) 해외주식":
- one endpoint, wss://openapi.imeritz.com:29443/websocket; after connecting, each
  subscription is a JSON message `{"header": {"token": "Bearer …", "tr_type": "1"},
  "body": {"tr_cd": …, "tr_key": "OQ@NVDA"}}`; "2" in `tr_type` unsubscribes;
- trades: `ohhts_exst_cntg` (US regular session), `ohhts_exst_cnta` (after-hours) and
  `ohhts_exst_cntd` (daytime session, 주간거래); order book: `ohhts_exst_aspr` (10 levels);
- every message has `header.tr_cd`/`tr_key` and a body with both local
  (`acpl_date`/`acpl_hour`) and Korean (`korea_date`/`korea_hour`) times. The Korean time
  is used: it names its zone, so it converts to UTC without a guess;
- more sessions than allowed answer WSC00110; the spec gives no number, so few symbols
  are subscribed (MAX_SYMBOLS).

Not used: the order notification service (`ohhts_cntg_exst`), whose messages carry the
account number and holder name. The relay carries market data only. Ticks are stored
as `lossy_stream` like Toss's; nothing here builds bars.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from services.market_data.instruments import parse_instrument
from services.market_data.meritz_session import EXCHANGE_CODES, WS_URL, MeritzSession
from services.market_data.tick_store import Tick, TickStore, now_utc_text
from services.market_data.toss_stream import Client, Connect, _as_dict, _event, default_connect

LOG = logging.getLogger(__name__)
SOURCE_ID = "meritz-us"
SEOUL = ZoneInfo("Asia/Seoul")
TRADE_CODES = ("ohhts_exst_cntg", "ohhts_exst_cnta", "ohhts_exst_cntd")
ORDERBOOK_CODE = "ohhts_exst_aspr"
MAX_SYMBOLS = 10  # four subscriptions each; the session limit is not published
SUBSCRIBE_INTERVAL = 0.2
FLUSH_SECONDS = 10.0


def _decimal(value: object) -> str | None:
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except InvalidOperation:
        return None
    return format(number, "f") if number.is_finite() and number >= 0 else None


def korea_time(body: dict[str, Any]) -> str | None:
    """`korea_date` + `korea_hour` as a UTC timestamp; None when missing or unreadable."""
    try:
        local = datetime.strptime(f"{body['korea_date']}{body['korea_hour']}", "%Y%m%d%H%M%S")
    except (KeyError, ValueError):
        return None
    return (
        local.replace(tzinfo=SEOUL)
        .astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class MeritzRealtimeHub:
    """The same client interface as the Toss hub, for `/v1/realtime?source=meritz`."""

    def __init__(
        self,
        session: Callable[[], MeritzSession | None],
        *,
        ticks: Callable[[], TickStore | None],
        instrument_for: Callable[[str, str], str | None],
        connect: Connect = default_connect,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        url: str = WS_URL,
    ) -> None:
        self._session = session
        self._ticks = ticks
        self._instrument_for = instrument_for
        self._connect = connect
        self._now = now
        self._url = url
        self._clients: set[Client] = set()
        self._task: asyncio.Task[None] | None = None
        self._resubscribe = asyncio.Event()
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

    def _key(self, symbol: str) -> str | None:
        """ "OQ@AAPL" for a ticker the app knows on a venue Meritz quotes."""
        instrument_id = self._instrument_for(symbol, "USD")
        parsed = parse_instrument(instrument_id) if instrument_id else None
        if parsed is None or parsed.venue not in EXCHANGE_CODES:
            return None
        return f"{EXCHANGE_CODES[parsed.venue]}@{symbol}"

    # --- connection loop -------------------------------------------------------------

    async def _run(self) -> None:
        attempt = 0
        while self._clients:
            session = self._session()
            if session is None:
                self._broadcast(self._status(False, "no enabled real-account Meritz key"))
                return
            try:
                await self._serve(session)
                attempt = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # network, auth or protocol: retry with backoff
                LOG.info("Meritz realtime connection ended: %s", type(exc).__name__)
                self._broadcast(self._status(False, f"{type(exc).__name__}: {str(exc)[:160]}"))
            self.connected = False
            if not self._clients:
                break
            delay = min(60.0, 2.0**attempt) * (0.5 + random.random() / 2)
            attempt += 1
            await asyncio.sleep(delay)
        store = self._ticks()
        if store is not None:
            await asyncio.to_thread(store.flush)

    async def _serve(self, session: MeritzSession) -> None:
        token = await asyncio.to_thread(session.token.get)
        connection = await self._connect(self._url, {})
        self.connected = True
        tasks = [
            asyncio.create_task(self._subscribe_loop(connection, token)),
            asyncio.create_task(self._flush_loop()),
        ]
        try:
            while self._clients:
                self._dispatch(await connection.recv())
        finally:
            for task in tasks:
                task.cancel()
            await connection.close()

    async def _subscribe_loop(self, connection: Any, token: str) -> None:
        active: dict[str, str] = {}  # tr_key -> symbol
        self._resubscribe.set()
        while True:
            await self._resubscribe.wait()
            self._resubscribe.clear()
            wanted: dict[str, str] = {}
            rejected: list[str] = []
            for symbol in self.symbols():
                key = self._key(symbol)
                if key is None:
                    rejected.append(symbol)
                else:
                    wanted[key] = symbol
            for key in sorted(set(active) - set(wanted)):
                for code in (*TRADE_CODES, ORDERBOOK_CODE):
                    await connection.send(self._message(token, "2", code, key))
                    await asyncio.sleep(SUBSCRIBE_INTERVAL)
            for key in sorted(set(wanted) - set(active)):
                for code in (*TRADE_CODES, ORDERBOOK_CODE):
                    await connection.send(self._message(token, "1", code, key))
                    await asyncio.sleep(SUBSCRIBE_INTERVAL)
            active = wanted
            self._broadcast(
                self._status(True, None, subscribed=sorted(active), rejected=rejected[:100])
            )

    @staticmethod
    def _message(token: str, kind: str, code: str, key: str) -> str:
        return json.dumps(
            {
                "header": {"token": f"Bearer {token}", "tr_type": kind},
                "body": {"tr_cd": code, "tr_key": key},
            }
        )

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_SECONDS)
            store = self._ticks()
            if store is not None:
                await asyncio.to_thread(store.flush)

    # --- frames -------------------------------------------------------------------

    def _dispatch(self, message: str) -> None:
        try:
            frame = json.loads(message)
        except ValueError:
            return
        if not isinstance(frame, dict):
            return
        header = _as_dict(frame.get("header"))
        body = _as_dict(frame.get("body"))
        code = str(header.get("tr_cd") or "")
        key = str(header.get("tr_key") or "")
        symbol = key.partition("@")[2].upper()
        error = str(body.get("rsp_cd") or header.get("rsp_cd") or "")
        if error and error not in ("0000", "00000"):
            self._broadcast(self._status(self.connected, f"{error}: {key or code}"[:160]))
            return
        if not symbol:
            return
        if code in TRADE_CODES:
            self._trade(symbol, body)
        elif code == ORDERBOOK_CODE:
            self._orderbook(symbol, body)

    def _trade(self, symbol: str, body: dict[str, Any]) -> None:
        price, volume, at = _decimal(body.get("prpr")), _decimal(body.get("cnqn")), korea_time(body)
        if price is None or volume is None or at is None:
            return
        received = now_utc_text(self._now)
        instrument_id = self._instrument_for(symbol, "USD")
        store = self._ticks()
        if store is not None:
            store.add(Tick(SOURCE_ID, symbol, instrument_id, price, volume, "USD", at, received))
        self._broadcast(
            _event(
                "trade",
                source_id=SOURCE_ID,
                symbol=symbol,
                instrument_id=instrument_id,
                price=price,
                volume=volume,
                currency="USD",
                exchange_time_utc=at,
                received_at_utc=received,
            ),
            symbol,
        )

    def _orderbook(self, symbol: str, body: dict[str, Any]) -> None:
        at = korea_time(body)
        if at is None:
            return

        def levels(side: str, rest: str) -> list[dict[str, str]]:
            out = []
            for level in range(1, 11):
                price, volume = (
                    _decimal(body.get(f"{side}{level}")),
                    _decimal(body.get(f"{rest}{level}")),
                )
                if price is not None and volume is not None and Decimal(price) > 0:
                    out.append({"price": price, "volume": volume})
            return out

        self._broadcast(
            _event(
                "orderbook",
                source_id=SOURCE_ID,
                symbol=symbol,
                instrument_id=self._instrument_for(symbol, "USD"),
                currency="USD",
                exchange_time_utc=at,
                asks=levels("askp", "seln_aspr_rsqn"),
                bids=levels("bidp", "shnu_aspr_rsqn"),
            ),
            symbol,
        )
