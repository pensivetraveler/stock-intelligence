"""Price levels of one closed session from Alpaca SIP trades, and their cache (ADR 0053).

One closed session is read once, whole, and kept as volume by exact trade price, split
into off-exchange (exchange code `D`, TRF-reported: ATS and broker internalization mixed,
no direction, report time not execution time) and lit (every other code). The trade
filters are check D's (ADR 0052 §5), so both checks count the same trades.

Only whole closed sessions are cached: a session cut by a budget or an error is thrown
away and read again later. Each cached document carries its SHA-256 and is checked again
when read.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from services.market_data.calendar import NEW_YORK, ExchangeCalendar, SessionWindow
from services.market_data.provider import MarketDataError
from services.screening.trade_conditions import (
    AVERAGE_PRICE_ON_TAPES_AB,
    CONDITION_POLICY_VERSION,
    LATE_REPORT,
    NOT_TRADES,
    OFF_EXCHANGE_CODE,
    OFF_MARKET_PRICE,
    REMOVED_UPDATES,
    SOURCE_ID,
)

#: 2: adds `off_exchange_trades` and `off_exchange_off_market_price_volume`, which check D
#: shows per session (ADR 0052 §5), so check D reads this cache too.
LEVELS_VERSION = "structure-session-levels-2"

TradePages = Callable[..., Iterator[list[dict[str, Any]]]]


def text(value: Decimal) -> str:
    rendered = format(value.normalize(), "f")
    return "0" if rendered in ("-0", "") else rendered


def utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical_sha256(value: Any) -> str:
    """SHA-256 of sorted, compact JSON. Every number in these documents is a string."""
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass
class _Reader:
    off: dict[Decimal, Decimal] = field(default_factory=dict)
    lit: dict[Decimal, Decimal] = field(default_factory=dict)
    trades: int = 0
    pages: int = 0
    total_volume: Decimal = Decimal(0)
    off_volume: Decimal = Decimal(0)
    off_trades: int = 0
    off_market_price_volume: Decimal = Decimal(0)
    off_exchange_off_market_volume: Decimal = Decimal(0)
    late_report_volume: Decimal = Decimal(0)
    removed_trades: int = 0
    last_price: Decimal | None = None
    last_time: str | None = None
    first_time: str | None = None

    def add(self, trade: dict[str, Any]) -> None:
        if trade.get("u") in REMOVED_UPDATES:
            self.removed_trades += 1
            return
        conditions = {str(item) for item in trade.get("c") or []}
        if conditions & NOT_TRADES:
            return
        price = Decimal(str(trade["p"]))
        size = Decimal(str(trade["s"]))
        if price <= 0 or size <= 0:
            return
        self.trades += 1
        self.total_volume += size
        stamp = str(trade["t"])
        if self.first_time is None or stamp < self.first_time:
            self.first_time = stamp
        off_exchange = trade.get("x") == OFF_EXCHANGE_CODE
        off_market = bool(conditions & OFF_MARKET_PRICE) or (
            AVERAGE_PRICE_ON_TAPES_AB in conditions and trade.get("z") in ("A", "B")
        )
        if off_exchange:
            self.off_volume += size
            self.off_trades += 1
            if conditions & LATE_REPORT:
                self.late_report_volume += size
            if off_market:
                self.off_exchange_off_market_volume += size
        if off_market:
            self.off_market_price_volume += size
            return
        if self.last_time is None or stamp >= self.last_time:
            self.last_price, self.last_time = price, stamp
        book = self.off if off_exchange else self.lit
        book[price] = book.get(price, Decimal(0)) + size


def session_document(
    symbol: str, window: SessionWindow, reader: _Reader, read_at: datetime
) -> dict[str, Any]:
    prices = sorted(set(reader.off) | set(reader.lit))
    document: dict[str, Any] = {
        "symbol": symbol,
        "session_date": window.session_date.isoformat(),
        "source_id": SOURCE_ID,
        "feed": "sip",
        "exchange_code": OFF_EXCHANGE_CODE,
        "levels_version": LEVELS_VERSION,
        "condition_policy_version": CONDITION_POLICY_VERSION,
        "range_utc": [utc(window.pre_open_utc), utc(window.post_close_utc)],
        "read_at_utc": utc(read_at),
        "pages": reader.pages,
        "trades": reader.trades,
        "total_volume": text(reader.total_volume),
        "off_exchange_volume": text(reader.off_volume),
        "off_exchange_trades": reader.off_trades,
        "off_market_price_volume": text(reader.off_market_price_volume),
        "off_exchange_off_market_price_volume": text(reader.off_exchange_off_market_volume),
        "late_report_volume": text(reader.late_report_volume),
        "removed_trades": reader.removed_trades,
        "last_price": None if reader.last_price is None else text(reader.last_price),
        "last_time_utc": reader.last_time,
        # [price, off-exchange volume, lit volume], prices as traded (not adjusted).
        "levels": [
            [
                text(price),
                text(reader.off.get(price, Decimal(0))),
                text(reader.lit.get(price, Decimal(0))),
            ]
            for price in prices
        ],
    }
    document["sha256"] = canonical_sha256(document)
    return document


def closed_sessions(
    calendar: ExchangeCalendar, known_until: datetime, count: int
) -> list[SessionWindow]:
    """The `count` latest sessions closed (post-close) by `known_until`, oldest first."""
    found: list[SessionWindow] = []
    day = known_until.astimezone(NEW_YORK).date()
    for _ in range(count * 2 + 15):
        window = calendar.window_for_date(day)
        if window is not None and window.post_close_utc <= known_until:
            found.append(window)
            if len(found) == count:
                break
        day -= timedelta(days=1)
    return list(reversed(found))


class SessionReadError(Exception):
    pass


def read_session(
    trade_pages: TradePages,
    symbol: str,
    window: SessionWindow,
    *,
    now: datetime,
    max_pages: int,
    max_seconds: float,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Every trade of one closed session (pre-open to post-close), or an error."""
    if window.post_close_utc > now:
        raise SessionReadError("the session has not closed")
    reader = _Reader()
    started = monotonic()
    pager = trade_pages(symbol, window.pre_open_utc, window.post_close_utc, sort="asc")
    try:
        for page in pager:
            reader.pages += 1
            for trade in page:
                reader.add(trade)
            if reader.pages >= max_pages or monotonic() - started >= max_seconds:
                raise SessionReadError(
                    f"stopped at {reader.pages} pages / {monotonic() - started:.0f} s "
                    f"(limit {max_pages} pages, {max_seconds:g} s)"
                )
    except MarketDataError as exc:
        raise SessionReadError(f"{type(exc).__name__}: {str(exc)[:200]}") from exc
    except (KeyError, ValueError, ArithmeticError, TypeError) as exc:
        raise SessionReadError(f"malformed trade from Alpaca: {type(exc).__name__}") from exc
    finally:
        close = getattr(pager, "close", None)
        if close is not None:
            close()
    return session_document(symbol, window, reader, now)


def latest_trade(
    trade_pages: TradePages, symbol: str, start: datetime, end: datetime
) -> tuple[Decimal, str] | None:
    """The latest market-priced trade in [start, end), from the newest page only."""
    if end <= start:
        return None
    reader = _Reader()
    pager = trade_pages(symbol, start, end, sort="desc")
    try:
        page = next(pager, None)
        for trade in page or []:
            reader.add(trade)
    finally:
        close = getattr(pager, "close", None)
        if close is not None:
            close()
    if reader.last_price is None or reader.last_time is None:
        return None
    return reader.last_price, reader.last_time


SCHEMA = """
CREATE TABLE IF NOT EXISTS session_levels (
    symbol TEXT NOT NULL,
    session_date TEXT NOT NULL,
    levels_version TEXT NOT NULL,
    condition_policy_version TEXT NOT NULL,
    read_at_utc TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    document TEXT NOT NULL,
    PRIMARY KEY (symbol, session_date, levels_version, condition_policy_version)
);
"""


class SessionCache:
    """Closed sessions' price levels in `SIW_DATA_DIR/screening_structure.sqlite3`."""

    def __init__(self, connection: sqlite3.Connection, lock: threading.Lock) -> None:
        self._db = connection
        self._lock = lock
        with self._lock:
            self._db.executescript(SCHEMA)

    def get(self, symbol: str, session_date: date) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT sha256, document FROM session_levels WHERE symbol = ? AND "
                "session_date = ? AND levels_version = ? AND condition_policy_version = ?",
                (symbol, session_date.isoformat(), LEVELS_VERSION, CONDITION_POLICY_VERSION),
            ).fetchone()
        if row is None:
            return None
        document = dict(json.loads(row[1]))
        body = {key: value for key, value in document.items() if key != "sha256"}
        if document.get("sha256") != row[0] or canonical_sha256(body) != row[0]:
            raise ValueError(f"cached session hash mismatch: {symbol} {session_date}")
        return document

    def put(self, document: dict[str, Any]) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO session_levels VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    document["symbol"],
                    document["session_date"],
                    document["levels_version"],
                    document["condition_policy_version"],
                    document["read_at_utc"],
                    document["sha256"],
                    json.dumps(document, ensure_ascii=False, sort_keys=True),
                ),
            )


@dataclass(frozen=True)
class FillLimits:
    #: A large cap session is 61~131 pages (AAPL, 2026-10-27~11-09); a busy day can be more.
    max_pages_per_session: int = 400
    max_seconds_per_session: float = 300.0
    #: Sessions read at once. Measured 2026-11-10 with 5 at once: about 0.53 s a page each
    #: (alone 0.42~0.67 s), 560 requests a minute against Alpaca's 10,000. Four leaves room
    #: for the charts and the other checks.
    workers: int = 4


class SessionFiller:
    """Reads missing closed sessions into the cache on background threads.

    Shared by check D and the structure check (one cache, one queue: a session is read
    once). The screen waits at most 60 s, and twenty sessions of a busy ticker take
    minutes, so a request starts the reading and returns; asking again shows the
    progress. A screen request goes ahead of the after-close prefetch (`background`).
    With `inline=True` (tests, command line) the reading happens in the caller.
    """

    def __init__(
        self,
        cache: SessionCache,
        trade_pages: Callable[[], TradePages | None],
        clock: Callable[[], datetime],
        limits: FillLimits | None = None,
        inline: bool = False,
    ) -> None:
        self._cache = cache
        self._trade_pages = trade_pages
        self._clock = clock
        self._limits = limits or FillLimits()
        self._inline = inline
        self._lock = threading.Lock()
        self._queue: list[tuple[str, SessionWindow]] = []
        self._queued: set[tuple[str, date]] = set()
        self._errors: dict[tuple[str, date], str] = {}
        self._threads: list[threading.Thread] = []

    def error(self, symbol: str, session_date: date) -> str | None:
        with self._lock:
            return self._errors.get((symbol, session_date))

    def pending(self, symbol: str) -> int:
        with self._lock:
            return sum(1 for queued_symbol, _ in self._queued if queued_symbol == symbol)

    def queued(self) -> int:
        with self._lock:
            return len(self._queued)

    def request(self, symbol: str, windows: list[SessionWindow], background: bool = False) -> None:
        """Queues the sessions not cached yet, newest first, and returns. A screen request
        goes to the front of the queue; `background` (prefetch) to the back."""
        wanted = sorted(
            (w for w in windows if self._cache.get(symbol, w.session_date) is None),
            key=lambda w: w.session_date,
            reverse=True,
        )
        if self._inline:
            for window in wanted:
                self._read(symbol, window)
            return
        with self._lock:
            fresh = []
            for window in wanted:
                key = (symbol, window.session_date)
                if key in self._queued:
                    if not background and (symbol, window) in self._queue:
                        # Waiting behind the prefetch: move it up (not one being read).
                        self._queue.remove((symbol, window))
                        fresh.append((symbol, window))
                    continue
                self._queued.add(key)
                fresh.append((symbol, window))
            self._queue = self._queue + fresh if background else fresh + self._queue
            self._threads = [thread for thread in self._threads if thread.is_alive()]
            while len(self._threads) < min(self._limits.workers, len(self._queue)):
                thread = threading.Thread(
                    target=self._run, name="screening-session-filler", daemon=True
                )
                self._threads.append(thread)
                thread.start()

    def wait(self, symbol: str, seconds: float, sleep: Callable[[float], None]) -> None:
        deadline = time.monotonic() + seconds
        while self.pending(symbol) and time.monotonic() < deadline:
            sleep(0.25)

    def _run(self) -> None:
        while True:
            with self._lock:
                if not self._queue:
                    return
                symbol, window = self._queue.pop(0)
            try:
                self._read(symbol, window)
            finally:
                with self._lock:
                    self._queued.discard((symbol, window.session_date))

    def _read(self, symbol: str, window: SessionWindow) -> None:
        pages = self._trade_pages()
        if pages is None:
            with self._lock:
                self._errors[(symbol, window.session_date)] = "no enabled Alpaca key"
            return
        try:
            document = read_session(
                pages,
                symbol,
                window,
                now=self._clock(),
                max_pages=self._limits.max_pages_per_session,
                max_seconds=self._limits.max_seconds_per_session,
            )
        except SessionReadError as exc:
            with self._lock:
                self._errors[(symbol, window.session_date)] = str(exc)[:300]
            return
        self._cache.put(document)
        with self._lock:
            self._errors.pop((symbol, window.session_date), None)


def open_database(path: Path) -> tuple[sqlite3.Connection, threading.Lock]:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    connection.execute("PRAGMA journal_mode = WAL")
    return connection, threading.Lock()
