"""A record of Alpaca SIP trading statuses, for halts in the stage 3 flow watch (ADR 0057 §3).

Alpaca keeps no history of statuses (no REST endpoint), so a halt is known only if
something listened while it happened. `siw-collector watch-halts` subscribes to the SIP
stream's `statuses` channel for every symbol (`["*"]`) from just before premarket opens to
the end of the aftermarket on US session days and appends to
`SIW_DATA_DIR/screeners/sip_status_watch.sqlite3`:

- one *listen* per connection, with when it started, the last time the process was alive
  (`last_heard_utc`, written every HEARTBEAT_SECONDS even when the market is quiet), and
  how it ended. A listen that ends without its end being recorded (the process was killed,
  the machine slept) is closed as `interrupted` at its last heartbeat the next time the
  store opens, so a gap in the record is never mistaken for a session without halts;
- one row per *change* of a symbol's status within a listen. A CTA halt repeats every five
  seconds while it lasts (measured 2026-11-12); a repeat only bumps `repeats` and
  `last_seen_utc` of the row it repeats, and keeps the exchange time `t` of its first and
  last repeat (`first_repeat_exchange_time`, `last_repeat_exchange_time`). A message with
  the same status code and no reason code repeats the latest change whatever its reason:
  a CTA LULD pause arrives once with reason `M` and then repeats without a reason
  (measured 2026-11-13), and is one change, not two.

Only status messages are kept: no trade, quote or bar. Nothing here places an order.
The key is the first enabled Alpaca key in the SIW key store; it is sent to Alpaca only
and never written to the store or the log.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
import threading
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from services.market_data.calendar import NEW_YORK, CalendarError, ExchangeCalendar

URL = "wss://stream.data.alpaca.markets/v2/sip"
MAX_DAYS_AHEAD = 14
MIN_RETRY_SECONDS = 5.0
MAX_RETRY_SECONDS = 300.0
STEADY_SECONDS = 300.0
# The process writes that it is alive this often, quiet market or not.
HEARTBEAT_SECONDS = 30.0
# How often the receive loop wakes up to check the clock and the stop flag.
POLL_SECONDS = 1.0
# Listening starts this long before premarket opens, so the record covers the open.
LEAD_SECONDS = 120.0
# Alpaca stream errors that retrying cannot fix: auth failed, insufficient subscription.
FATAL_CODES = frozenset({402, 409})

SCHEMA = """
CREATE TABLE IF NOT EXISTS listens (
    listen_id INTEGER PRIMARY KEY,
    feed TEXT NOT NULL,
    started_at_utc TEXT NOT NULL,
    last_heard_utc TEXT NOT NULL,
    ended_at_utc TEXT,
    end_reason TEXT
);
CREATE TABLE IF NOT EXISTS status_changes (
    change_id INTEGER PRIMARY KEY,
    listen_id INTEGER NOT NULL REFERENCES listens(listen_id),
    symbol TEXT NOT NULL,
    tape TEXT,
    status_code TEXT NOT NULL,
    status_message TEXT,
    reason_code TEXT,
    reason_message TEXT,
    exchange_time TEXT NOT NULL,
    received_at_utc TEXT NOT NULL,
    last_seen_utc TEXT NOT NULL,
    repeats INTEGER NOT NULL DEFAULT 0,
    first_repeat_exchange_time TEXT,
    last_repeat_exchange_time TEXT,
    raw TEXT
);
CREATE INDEX IF NOT EXISTS status_changes_by_symbol ON status_changes (symbol, received_at_utc);
CREATE INDEX IF NOT EXISTS listens_by_time ON listens (started_at_utc);
"""
# Columns added after the first stores were written (2026-11-14); added in place on open.
ADDED_COLUMNS = (
    ("status_changes", "first_repeat_exchange_time", "TEXT"),
    ("status_changes", "last_repeat_exchange_time", "TEXT"),
)


def _utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def store_path(data_dir: Path) -> Path:
    return data_dir / "screeners" / "sip_status_watch.sqlite3"


class StatusWatchStore:
    """Append-only: listens are opened, kept alive and closed; changes are only added (a
    repeat of the latest change only moves its `last_seen_utc`)."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.executescript(SCHEMA)
        for table, column, kind in ADDED_COLUMNS:
            present = {row["name"] for row in self._db.execute(f"PRAGMA table_info({table})")}
            if column not in present:
                self._db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
        self._lock = threading.Lock()
        # Per listen: symbol -> (status code, reason code, change_id) of its latest change.
        self._latest: dict[str, tuple[str, str, int]] = {}
        self._close_interrupted()

    @classmethod
    def in_data_dir(cls, data_dir: Path) -> StatusWatchStore:
        return cls(store_path(data_dir))

    def close(self) -> None:
        self._db.close()

    def _close_interrupted(self) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE listens SET end_reason = 'interrupted', ended_at_utc = last_heard_utc "
                "WHERE ended_at_utc IS NULL"
            )

    def start(self, feed: str, at: datetime) -> int:
        with self._lock:
            self._latest = {}
            cursor = self._db.execute(
                "INSERT INTO listens (feed, started_at_utc, last_heard_utc) VALUES (?, ?, ?)",
                (feed, _utc(at), _utc(at)),
            )
            return int(cursor.lastrowid or 0)

    def heard(self, listen_id: int, at: datetime) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE listens SET last_heard_utc = ? "
                "WHERE listen_id = ? AND ended_at_utc IS NULL",
                (_utc(at), listen_id),
            )

    def record(self, listen_id: int, message: Mapping[str, Any], at: datetime) -> bool:
        """Add one status message. Returns True when it changed the symbol's status."""
        symbol = str(message.get("S", "")).strip()
        code = str(message.get("sc", "")).strip()
        if not symbol or not code or "t" not in message:
            return False
        reason = str(message.get("rc", "") or "").strip()
        received = _utc(at)
        with self._lock:
            latest = self._latest.get(symbol)
            if latest is not None and latest[0] == code and reason in ("", latest[1]):
                exchange_time = str(message["t"])
                self._db.execute(
                    "UPDATE status_changes SET repeats = repeats + 1, last_seen_utc = ?, "
                    "first_repeat_exchange_time = COALESCE(first_repeat_exchange_time, ?), "
                    "last_repeat_exchange_time = ? WHERE change_id = ?",
                    (received, exchange_time, exchange_time, latest[2]),
                )
                return False
            cursor = self._db.execute(
                "INSERT INTO status_changes (listen_id, symbol, tape, status_code, "
                "status_message, reason_code, reason_message, exchange_time, received_at_utc, "
                "last_seen_utc, raw) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    listen_id,
                    symbol,
                    str(message.get("z", "") or "") or None,
                    code,
                    str(message.get("sm", "") or "") or None,
                    reason or None,
                    str(message.get("rm", "") or "") or None,
                    str(message["t"]),
                    received,
                    received,
                    json.dumps(dict(message), ensure_ascii=False, sort_keys=True),
                ),
            )
            self._latest[symbol] = (code, reason, int(cursor.lastrowid or 0))
            self._db.execute(
                "UPDATE listens SET last_heard_utc = ? WHERE listen_id = ?", (received, listen_id)
            )
        return True

    def end(self, listen_id: int, at: datetime, reason: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE listens SET ended_at_utc = ?, last_heard_utc = ?, end_reason = ? "
                "WHERE listen_id = ? AND ended_at_utc IS NULL",
                (_utc(at), _utc(at), reason[:300], listen_id),
            )

    def export(self, start: datetime, end: datetime) -> dict[str, Any]:
        """Every listen that overlaps [start, end) and every change received in it."""
        low, high = _utc(start), _utc(end)
        with self._lock:
            listens = self._db.execute(
                "SELECT * FROM listens WHERE started_at_utc < ? AND last_heard_utc >= ? "
                "ORDER BY listen_id",
                (high, low),
            ).fetchall()
            changes = self._db.execute(
                "SELECT * FROM status_changes WHERE received_at_utc >= ? AND received_at_utc < ? "
                "ORDER BY change_id",
                (low, high),
            ).fetchall()
        return {
            "kind": "sip_status_watch_export",
            "from_utc": low,
            "to_utc": high,
            "listens": [dict(item) for item in listens],
            "changes": [
                {key: value for key, value in dict(item).items() if key != "raw"}
                for item in changes
            ],
        }


class Connection(Protocol):
    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...

    async def close(self) -> None: ...


Connect = Callable[[str], Awaitable[Connection]]


async def default_connect(url: str) -> Connection:
    import websockets

    connection: Connection = await websockets.connect(url, open_timeout=20, close_timeout=5)
    return connection


class StreamError(Exception):
    def __init__(self, code: int | None, message: str) -> None:
        super().__init__(f"{code}: {message}" if code is not None else message)
        self.code = code


def _messages(raw: str | bytes) -> list[dict[str, Any]]:
    payload = json.loads(raw)
    items = payload if isinstance(payload, list) else [payload]
    return [item for item in items if isinstance(item, dict)]


def _raise_on_error(messages: list[dict[str, Any]]) -> None:
    for item in messages:
        if item.get("T") == "error":
            code = item.get("code")
            raise StreamError(int(code) if isinstance(code, int) else None, str(item.get("msg")))


async def _expect(connection: Connection, wanted: str, timeout: float) -> list[dict[str, Any]]:
    """Messages up to the first of type `wanted` (`success` with that msg, or `subscription`)."""
    seen: list[dict[str, Any]] = []
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        left = deadline - asyncio.get_running_loop().time()
        if left <= 0:
            raise StreamError(None, f"no {wanted!r} from the stream within {timeout:.0f}s")
        messages = _messages(await asyncio.wait_for(connection.recv(), timeout=left))
        _raise_on_error(messages)
        seen += messages
        for item in messages:
            if wanted == "subscription" and item.get("T") == "subscription":
                return seen
            if item.get("T") == "success" and item.get("msg") == wanted:
                return seen


@dataclass(frozen=True)
class ListenOutcome:
    reason: str
    error: str | None = None
    fatal: bool = False
    changes: int = 0


async def listen_once(
    connect: Connect,
    key_id: str,
    secret_key: str,
    store: StatusWatchStore,
    *,
    until: datetime,
    stop: threading.Event,
    clock: Callable[[], datetime],
    url: str = URL,
) -> ListenOutcome:
    """One connection: authenticate, subscribe to every symbol's statuses, record until
    `until` or `stop`, and close the listen with why it ended."""
    listen_id: int | None = None
    changes = 0
    connection: Connection | None = None
    try:
        connection = await connect(url)
        await _expect(connection, "connected", 20)
        await connection.send(json.dumps({"action": "auth", "key": key_id, "secret": secret_key}))
        await _expect(connection, "authenticated", 20)
        await connection.send(json.dumps({"action": "subscribe", "statuses": ["*"]}))
        early = await _expect(connection, "subscription", 20)
        listen_id = store.start("sip", clock())
        for item in early:
            if item.get("T") == "s" and store.record(listen_id, item, clock()):
                changes += 1
        last_beat = clock()
        while not stop.is_set() and clock() < until:
            try:
                raw = await asyncio.wait_for(connection.recv(), timeout=POLL_SECONDS)
            except asyncio.TimeoutError:
                raw = None
            if raw is not None:
                messages = _messages(raw)
                _raise_on_error(messages)
                for item in messages:
                    if item.get("T") == "s" and store.record(listen_id, item, clock()):
                        changes += 1
            now = clock()
            if (now - last_beat).total_seconds() >= HEARTBEAT_SECONDS:
                store.heard(listen_id, now)
                last_beat = now
    except StreamError as exc:
        error = f"StreamError {exc}"
        if listen_id is not None:
            store.end(listen_id, clock(), error)
        return ListenOutcome("error", error, exc.code in FATAL_CODES, changes)
    except Exception as exc:  # noqa: BLE001
        # websockets raises ConnectionClosed (not an OSError) when the stream drops.
        error = f"{type(exc).__name__}: {str(exc)[:200]}"
        if listen_id is not None:
            store.end(listen_id, clock(), error)
        return ListenOutcome("error", error, False, changes)
    finally:
        if connection is not None:
            # Closing a dropped connection can fail; there is nothing left to do then.
            with contextlib.suppress(Exception):
                await connection.close()
    reason = "stopped" if stop.is_set() else "window_closed"
    if listen_id is not None:
        store.end(listen_id, clock(), reason)
    return ListenOutcome(reason, None, False, changes)


def window_around(moment: datetime, calendar: ExchangeCalendar) -> tuple[datetime, datetime]:
    """The listening window containing `moment`, or the next one: LEAD_SECONDS before
    premarket opens to the end of the aftermarket of a US session (04:00-20:00 New York
    time, 17:00 on an early close). Days without a session are skipped."""
    day = moment.astimezone(NEW_YORK).date()
    for _ in range(MAX_DAYS_AHEAD):
        window = calendar.window_for_date(day)
        if window is not None and moment < window.post_close_utc:
            return window.pre_open_utc - timedelta(seconds=LEAD_SECONDS), window.post_close_utc
        day += timedelta(days=1)
    raise CalendarError(f"no US session within {MAX_DAYS_AHEAD} days of {_utc(moment)}")


def run_halt_watch(
    key_id: str,
    secret_key: str,
    store: StatusWatchStore,
    *,
    stop: threading.Event,
    clock: Callable[[], datetime],
    report: Callable[[dict[str, object]], None],
    connect: Connect = default_connect,
    calendar: ExchangeCalendar | None = None,
) -> int:
    """Listen in every window until `stop` is set. Returns 2 when Alpaca refuses the key or
    the subscription (retrying cannot help), otherwise 0 when stopped."""
    calendar = calendar or ExchangeCalendar("XNYS")
    delay = MIN_RETRY_SECONDS
    while not stop.is_set():
        start, end = window_around(clock(), calendar)
        now = clock()
        if now < start:
            report({"status": "waiting", "window_start_utc": _utc(start)})
            stop.wait((start - now).total_seconds())
            continue
        report({"status": "listening", "window_end_utc": _utc(end)})
        began = clock()
        outcome = asyncio.run(
            listen_once(connect, key_id, secret_key, store, until=end, stop=stop, clock=clock)
        )
        if outcome.error is None:
            delay = MIN_RETRY_SECONDS
            report({"status": outcome.reason, "changes": outcome.changes})
            continue
        if outcome.fatal:
            report({"status": "error", "error": outcome.error})
            return 2
        if (clock() - began).total_seconds() >= STEADY_SECONDS:
            delay = MIN_RETRY_SECONDS
        report({"status": "retrying", "error": outcome.error, "retry_seconds": delay})
        stop.wait(delay)
        delay = min(delay * 2, MAX_RETRY_SECONDS)
    report({"status": "stopped"})
    return 0
