"""Kiwoom US condition search (조건검색), read-only, over Kiwoom's WebSocket (ADR 0048).

Spec: usa20280 (GCNSRLST, the conditions saved in 영웅문Global) and usa20281 (GCNSRREQ
with search_type 0, one run of a condition), both on /api/us/websocket. A connection
sends LOGIN with the REST access token first and echoes every PING.

Live check, 2026-11-04 (user's key, through kwcli): 76 conditions with gaps in `seq`;
condition 42 answered 10 rows in one page. Replies were named GCNSRLST and GCNSRREQ,
while the spec names them GCNSRLT and CNSRREQ, so both are accepted. Numbers are
zero-padded with their sign ("000005.1600", "-00000.0265"). A row carries no time, and
`stex_tp` (ND, NY, NA) does not tell NYSE from NYSE Arca, so it is reported, never used
as a venue.

Only LOGIN, PING echoes, GCNSRLST and GCNSRREQ with search_type 0 can be sent: realtime
condition events and every order call are out of reach.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Protocol

from services.market_data.provider import (
    InstrumentNotFoundError,
    InvalidBarsQueryError,
    ProviderAuthError,
    ProviderDataError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)

REAL_URL = "wss://api.kiwoom.com:10000/api/us/websocket"
DEMO_URL = "wss://mockapi.kiwoom.com:10000/api/us/websocket"
LIST_REPLIES = frozenset({"GCNSRLST", "GCNSRLT"})
SEARCH_REPLIES = frozenset({"GCNSRREQ", "CNSRREQ"})
RATE_LIMIT_CODES = {1700, 1701, 1702}
MAX_PAGES = 20
MAX_SKIPPED = 50  # unrelated messages tolerated while waiting for one reply
TIMEOUT_SECONDS = 15.0
# Row fields of usa20281: Kiwoom's FID -> our name.
NUMBER_FIELDS = {
    "10": "last",
    "11": "change",
    "12": "change_percent",
    "13": "volume",
    "16": "open",
    "17": "high",
    "18": "low",
}


class Connection(Protocol):
    def send(self, message: str) -> None: ...

    def recv(self, timeout: float | None = None) -> str | bytes: ...

    def close(self) -> None: ...


Connect = Callable[[str], Connection]


def default_connect(url: str) -> Connection:
    from websockets.exceptions import WebSocketException
    from websockets.sync.client import connect

    try:
        # Kiwoom keeps the connection alive with its own PING messages (echoed below).
        return connect(url, open_timeout=10, close_timeout=3, ping_interval=None, max_size=2**22)
    except (OSError, TimeoutError, WebSocketException) as exc:
        raise ProviderUnavailableError(
            f"could not reach Kiwoom's WebSocket: {type(exc).__name__}"
        ) from exc


class ConditionNotFoundError(InstrumentNotFoundError):
    """The `seq` is not among the conditions saved in 영웅문Global."""


@dataclass(frozen=True)
class Condition:
    seq: str
    name: str


@dataclass(frozen=True)
class ConditionRow:
    symbol: str
    name: str
    kiwoom_exchange: str
    sector: str | None
    numbers: dict[str, str]  # NUMBER_FIELDS values -> exact decimal text


@dataclass(frozen=True)
class ConditionRun:
    condition: Condition
    rows: tuple[ConditionRow, ...]
    complete: bool  # False when MAX_PAGES was reached with more pages left


class KiwoomConditionSearch:
    """Opens one connection per call, logs in, asks, and closes."""

    def __init__(
        self,
        token: Callable[[bool], str],
        environment: Literal["real", "demo"] = "real",
        *,
        connect: Connect = default_connect,
    ) -> None:
        self._token = token
        self._url = DEMO_URL if environment == "demo" else REAL_URL
        self._connect = connect

    def conditions(self) -> list[Condition]:
        with self._open() as socket:
            return _conditions(socket)

    def run(self, seq: str) -> ConditionRun:
        with self._open() as socket:
            # The spec asks for the list before a realtime run; asking first here also
            # gives the condition's name and refuses a `seq` that does not exist.
            condition = next((item for item in _conditions(socket) if item.seq == seq), None)
            if condition is None:
                raise ConditionNotFoundError(f"no saved Kiwoom condition has seq {seq!r}")
            rows: list[ConditionRow] = []
            body = {
                "trnm": "GCNSRREQ",
                "seq": seq,
                "search_type": "0",
                "cont_yn": "N",
                "next_key": "",
            }
            for _page in range(MAX_PAGES):
                reply = socket.ask(body, SEARCH_REPLIES)
                rows.extend(_row(item) for item in _list(reply.get("data")))
                next_key = str(reply.get("next_key") or "")
                if str(reply.get("cont_yn", "N")).upper() != "Y" or not next_key:
                    return ConditionRun(condition, tuple(rows), complete=True)
                body = {**body, "cont_yn": "Y", "next_key": next_key}
            return ConditionRun(condition, tuple(rows), complete=False)

    def _open(self) -> _Socket:
        for attempt in range(2):
            socket = _Socket(self._connect(self._url))
            try:
                code, message = socket.login(self._token(attempt > 0))
            except BaseException:
                socket.close()
                raise
            if code == 0:
                return socket
            socket.close()
            if code in RATE_LIMIT_CODES:
                raise ProviderRateLimitError("Kiwoom request limit reached; retry later")
            # An expired or revoked token: reissue it once, as the REST adapters do.
            if attempt == 1:
                raise ProviderAuthError(f"Kiwoom refused the WebSocket login ({code}): {message}")
        raise AssertionError("unreachable")


class _Socket:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def __enter__(self) -> _Socket:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        # Closing must never hide the error that ended the call.
        with contextlib.suppress(Exception):
            self._connection.close()

    def login(self, token: str) -> tuple[int, str]:
        self._send({"trnm": "LOGIN", "token": token})
        reply = self._wait(frozenset({"LOGIN"}))
        return _code(reply), str(reply.get("return_msg", ""))[:200]

    def ask(self, body: dict[str, str], replies: frozenset[str]) -> dict[str, Any]:
        self._send(body)
        reply = self._wait(replies)
        code = _code(reply)
        if code in RATE_LIMIT_CODES:
            raise ProviderRateLimitError("Kiwoom request limit reached; retry later")
        if code != 0:
            message = str(reply.get("return_msg", ""))[:200]
            raise InvalidBarsQueryError(f"Kiwoom refused the condition request ({code}): {message}")
        return reply

    def _send(self, body: dict[str, str]) -> None:
        if body.get("trnm") not in {"LOGIN", "PING", "GCNSRLST", "GCNSRREQ"} or (
            body.get("trnm") == "GCNSRREQ" and body.get("search_type") != "0"
        ):
            raise ProviderDataError(f"{body.get('trnm')} is not on the read-only allowlist")
        self._write(json.dumps(body, ensure_ascii=False))

    def _write(self, text: str) -> None:
        try:
            self._connection.send(text)
        except Exception as exc:  # websockets raises its own classes
            raise ProviderUnavailableError(
                f"Kiwoom's WebSocket closed: {type(exc).__name__}"
            ) from exc

    def _wait(self, replies: frozenset[str]) -> dict[str, Any]:
        for _skipped in range(MAX_SKIPPED):
            try:
                raw = self._connection.recv(timeout=TIMEOUT_SECONDS)
            except TimeoutError as exc:
                raise ProviderUnavailableError("Kiwoom did not answer in time") from exc
            except Exception as exc:  # websockets raises its own classes
                raise ProviderUnavailableError(
                    f"Kiwoom's WebSocket closed: {type(exc).__name__}"
                ) from exc
            text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            if text.strip().upper() == "PING":
                self._write(text)
                continue
            try:
                message = json.loads(text)
            except ValueError as exc:
                raise ProviderDataError("Kiwoom sent a message that is not JSON") from exc
            if not isinstance(message, dict):
                continue
            name = str(message.get("trnm", "")).upper()
            if name == "PING":
                self._send(message)
            elif name in replies:
                return message
        raise ProviderDataError("Kiwoom never answered the condition request")


def _conditions(socket: _Socket) -> list[Condition]:
    reply = socket.ask({"trnm": "GCNSRLST"}, LIST_REPLIES)
    found: list[Condition] = []
    for item in _list(reply.get("data")):
        pair = [item.get("seq"), item.get("name")] if isinstance(item, dict) else item
        if not isinstance(pair, list) or len(pair) < 2:
            raise ProviderDataError("Kiwoom returned a condition without seq and name")
        seq, name = str(pair[0]).strip(), str(pair[1]).strip()
        if not seq or not name:
            raise ProviderDataError("Kiwoom returned a condition without seq and name")
        found.append(Condition(seq, name))
    return found


def _row(item: object) -> ConditionRow:
    if not isinstance(item, dict):
        raise ProviderDataError("Kiwoom returned a condition row that is not an object")
    symbol = str(item.get("9001", "")).strip().upper()
    if not symbol:
        raise ProviderDataError("Kiwoom returned a condition row without a ticker")
    numbers: dict[str, str] = {}
    for fid, field in NUMBER_FIELDS.items():
        try:
            value = Decimal(str(item.get(fid, "")).replace(",", "").strip())
        except InvalidOperation as exc:
            raise ProviderDataError(f"Kiwoom returned {symbol} without a valid {field}") from exc
        if not value.is_finite():
            raise ProviderDataError(f"Kiwoom returned a non-finite {field} for {symbol}")
        numbers[field] = format(value, "f")
    sector = str(item.get("318", "")).strip()
    return ConditionRow(
        symbol=symbol,
        name=str(item.get("302", "")).strip()[:200],
        kiwoom_exchange=str(item.get("stex_tp", "")).strip()[:8],
        sector=sector[:200] or None,
        numbers=numbers,
    )


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _code(message: dict[str, Any]) -> int:
    try:
        return int(str(message.get("return_code", 0) or 0).strip())
    except ValueError:
        return -1
