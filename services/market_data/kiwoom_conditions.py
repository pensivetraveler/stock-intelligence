"""Kiwoom US condition search (조건검색), read-only, over Kiwoom's WebSocket (ADR 0048).

Spec: usa20280 (GCNSRLST, the conditions saved in 영웅문Global) and usa20281 (GCNSRREQ
with search_type 0, one run of a condition), both on /api/us/websocket. A connection
sends LOGIN with the REST access token first and echoes every PING.

Live check, 2026-11-04 (user's key, through kwcli): 76 conditions with gaps in `seq`;
condition 42 answered 10 rows in one page. Replies were named GCNSRLST and GCNSRREQ,
while the spec names them GCNSRLT and CNSRREQ, so both are accepted. Numbers are
zero-padded with their sign ("000005.1600", "-00000.0265"). A row carries no time, and
`stex_tp` (ND, NY, NA) does not tell NYSE from NYSE Arca, so it is reported, never used
as a venue. Later that day three conditions were saved above the others and every `seq`
moved by four, so conditions are run by name and their `seq` is looked up each time.

usa20290 (GCNSRREQ with search_type 1) answers the condition's tickers and then keeps
sending REAL messages of type S2 as tickers enter (843 "I") or leave (843 "D"); usa20291
(GCNSRCLR) stops them (ADR 0051). The spec documents no time zone for field 20 or the
meaning of 841 and 907, so each event keeps its raw values.

Only LOGIN, PING echoes, GCNSRLST, GCNSRREQ with search_type 0 or 1, and GCNSRCLR can be
sent: every order call is out of reach.
"""

from __future__ import annotations

import contextlib
import json
import time
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

# The user's choice (2026-11-04): the conditions SIW uses, saved in 영웅문Global.
CONNECTED_CONDITIONS = ("SIW_전일신고거래량", "SIW_당일급등주", "SIW_전일급등주")
REAL_URL = "wss://api.kiwoom.com:10000/api/us/websocket"
DEMO_URL = "wss://mockapi.kiwoom.com:10000/api/us/websocket"
LIST_REPLIES = frozenset({"GCNSRLST", "GCNSRLT"})
SEARCH_REPLIES = frozenset({"GCNSRREQ", "CNSRREQ"})
# The spec's usa20291 example answers a GCNSRCLR with trnm GCNSRREQ.
CLEAR_REPLIES = frozenset({"GCNSRCLR", "GCNSRREQ"})
RATE_LIMIT_CODES = {1700, 1701, 1702}
MAX_PAGES = 20
MAX_SKIPPED = 50  # unrelated messages tolerated while waiting for one reply
TIMEOUT_SECONDS = 15.0
# Kiwoom sends a PING about every 10 s (seen live on 2026-11-05), so a silent minute
# means the connection is gone even if the socket has not said so.
SILENCE_SECONDS = 60.0
EVENT_KINDS: dict[str, Literal["enter", "leave"]] = {"I": "enter", "D": "leave"}
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
    """No condition of that name is saved in 영웅문Global."""


class DuplicateConditionError(InvalidBarsQueryError):
    """More than one condition is saved under that name."""


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
class ConditionTicker:
    """One ticker of a realtime condition list: only the code and Kiwoom's exchange code."""

    symbol: str
    kiwoom_exchange: str


@dataclass(frozen=True)
class ConditionEvent:
    """A ticker entering or leaving a watched condition, as Kiwoom sent it."""

    kind: Literal["enter", "leave"]
    symbol: str
    kiwoom_exchange: str
    kiwoom_time: str | None  # field 20 (HHMMSS); its time zone is not documented
    values: dict[str, str]  # every field of the event, unchanged


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

    def run(self, name: str) -> ConditionRun:
        """Run the condition saved under `name`. Its `seq` is looked up on the same
        connection: Kiwoom renumbers every condition when one is added above it (seen
        live on 2026-11-04), so a `seq` kept from an earlier call can name another one."""
        with self._open() as socket:
            condition = _find(socket, name)
            rows: list[ConditionRow] = []
            body = {
                "trnm": "GCNSRREQ",
                "seq": condition.seq,
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

    def watch(
        self,
        name: str,
        *,
        on_list: Callable[[Condition, tuple[ConditionTicker, ...]], None],
        on_event: Callable[[ConditionEvent], None],
        until: Callable[[], bool],
    ) -> None:
        """Subscribe to the condition saved under `name` (usa20290) and report its list,
        then every enter/leave event, until `until()` is true. The subscription is then
        cleared (usa20291) and the connection closed. A lost connection raises
        ProviderUnavailableError; the caller decides when to watch again."""
        with self._open() as socket:
            condition = _find(socket, name)
            reply = socket.ask(
                {"trnm": "GCNSRREQ", "seq": condition.seq, "search_type": "1"}, SEARCH_REPLIES
            )
            on_list(condition, tuple(_ticker(item) for item in _list(reply.get("data"))))
            try:
                while not until():
                    message = socket.next_message(timeout=1.0)
                    if message is not None and str(message.get("trnm", "")).upper() == "REAL":
                        for item in _list(message.get("data")):
                            event = _event(item, condition.seq)
                            if event is not None:
                                on_event(event)
            finally:
                # Best effort: the connection may already be gone.
                with contextlib.suppress(Exception):
                    socket.ask({"trnm": "GCNSRCLR", "seq": condition.seq}, CLEAR_REPLIES)

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
    def __init__(self, connection: Connection, clock: Callable[[], float] = time.monotonic) -> None:
        self._connection = connection
        self._clock = clock
        self._heard_at = clock()

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

    def next_message(self, timeout: float) -> dict[str, Any] | None:
        """The next message other than a PING, or None when nothing came in `timeout`.
        Raises ProviderUnavailableError after SILENCE_SECONDS without any message."""
        try:
            raw = self._connection.recv(timeout=timeout)
        except TimeoutError:
            if self._clock() - self._heard_at > SILENCE_SECONDS:
                raise ProviderUnavailableError("Kiwoom's WebSocket went silent") from None
            return None
        except Exception as exc:  # websockets raises its own classes
            raise ProviderUnavailableError(
                f"Kiwoom's WebSocket closed: {type(exc).__name__}"
            ) from exc
        self._heard_at = self._clock()
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        if text.strip().upper() == "PING":
            self._write(text)
            return None
        try:
            message = json.loads(text)
        except ValueError as exc:
            raise ProviderDataError("Kiwoom sent a message that is not JSON") from exc
        if not isinstance(message, dict):
            return None
        if str(message.get("trnm", "")).upper() == "PING":
            self._send(message)
            return None
        return message

    def _send(self, body: dict[str, str]) -> None:
        name = body.get("trnm")
        if name not in {"LOGIN", "PING", "GCNSRLST", "GCNSRREQ", "GCNSRCLR"} or (
            name == "GCNSRREQ" and body.get("search_type") not in {"0", "1"}
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
        deadline = self._clock() + TIMEOUT_SECONDS
        skipped = 0
        while skipped < MAX_SKIPPED:
            left = deadline - self._clock()
            if left <= 0:
                raise ProviderUnavailableError("Kiwoom did not answer in time")
            try:
                raw = self._connection.recv(timeout=left)
            except TimeoutError as exc:
                raise ProviderUnavailableError("Kiwoom did not answer in time") from exc
            except Exception as exc:  # websockets raises its own classes
                raise ProviderUnavailableError(
                    f"Kiwoom's WebSocket closed: {type(exc).__name__}"
                ) from exc
            self._heard_at = self._clock()
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
            else:
                skipped += 1
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


def _find(socket: _Socket, name: str) -> Condition:
    matches = [item for item in _conditions(socket) if item.name == name]
    if not matches:
        raise ConditionNotFoundError(f"no Kiwoom condition is saved as {name!r}")
    if len(matches) > 1:
        raise DuplicateConditionError(
            f"{len(matches)} Kiwoom conditions are saved as {name!r}; rename one"
        )
    return matches[0]


def _ticker(item: object) -> ConditionTicker:
    if not isinstance(item, dict):
        raise ProviderDataError("Kiwoom returned a condition ticker that is not an object")
    symbol = str(item.get("jmcode", "")).strip().upper()
    if not symbol:
        raise ProviderDataError("Kiwoom returned a condition ticker without a code")
    exchange = item.get("stex_tp", item.get("stexTp", ""))
    return ConditionTicker(symbol, str(exchange).strip()[:8])


def _event(item: object, seq: str) -> ConditionEvent | None:
    """An S2 enter/leave event of condition `seq`, or None for anything else."""
    if not isinstance(item, dict) or str(item.get("type", "")).upper() != "S2":
        return None
    raw = item.get("values")
    if not isinstance(raw, dict):
        raise ProviderDataError("Kiwoom sent a condition event without values")
    values = {str(key): str(value)[:200] for key, value in raw.items()}
    # 841 carried the condition's seq in the spec example; skip events of another one.
    if values.get("841", seq).strip() != seq:
        return None
    kind = EVENT_KINDS.get(values.get("843", "").strip().upper())
    if kind is None:
        raise ProviderDataError(f"Kiwoom sent a condition event of kind {values.get('843')!r}")
    symbol = (values.get("9001") or str(item.get("item", ""))).strip().upper()
    if not symbol:
        raise ProviderDataError("Kiwoom sent a condition event without a ticker")
    kiwoom_time = values.get("20", "").strip() or None
    return ConditionEvent(
        kind=kind,
        symbol=symbol,
        kiwoom_exchange=str(item.get("stexTp", item.get("stex_tp", ""))).strip()[:8],
        kiwoom_time=kiwoom_time,
        values=values,
    )


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
