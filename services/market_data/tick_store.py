"""Local store for realtime trade ticks, kept apart from minute bars.

Ticks come from a broker stream that has no sequence numbers and may drop frames (Toss:
"유실 시 항상 최신 상태가 우선"), so every stored tick is marked `lossy_stream`: a record of
what this app received, never a complete tape. Ticks are never turned into bars and never
feed charts, indicators, replay or backtests.

Layout: `ticks/source_id=<id>/symbol=<SYM>/session_date=<ET date>/part-<first tick>-<uuid>.parquet`.
Each flush writes a new immutable file through a temporary name, so a crash loses at most
the unflushed buffer and never leaves a half-written part. Prices and volumes stay exact
decimal strings.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

NEW_YORK = ZoneInfo("America/New_York")
SEOUL = ZoneInfo("Asia/Seoul")
COMPLETENESS = "lossy_stream"
FLUSH_SECONDS = 10.0
FLUSH_TICKS = 2_000
SCHEMA = pa.schema(
    [
        ("source_id", pa.string()),
        ("symbol", pa.string()),
        ("instrument_id", pa.string()),
        ("price", pa.string()),
        ("volume", pa.string()),
        ("currency", pa.string()),
        ("exchange_time_utc", pa.string()),
        ("received_at_utc", pa.string()),
        ("completeness", pa.string()),
    ]
)


@dataclass(frozen=True)
class Tick:
    source_id: str
    symbol: str
    instrument_id: str | None
    price: str
    volume: str
    currency: str
    exchange_time_utc: str
    received_at_utc: str
    completeness: str = COMPLETENESS

    @property
    def session_date(self) -> str:
        """Exchange calendar date of the trade: Seoul for Korean listings, else New York."""
        stamp = datetime.fromisoformat(self.exchange_time_utc.replace("Z", "+00:00"))
        zone = SEOUL if self.source_id == "toss-kr" else NEW_YORK
        return stamp.astimezone(zone).date().isoformat()


class TickStore:
    def __init__(
        self,
        root: Path,
        *,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self.root = root.resolve() / "ticks"
        self._buffer: list[Tick] = []
        self._lock = threading.Lock()
        self._monotonic = monotonic or time.monotonic
        self._last_flush = self._monotonic()

    def add(self, tick: Tick) -> bool:
        """Buffer one tick (cheap; no I/O). Returns whether a flush is due."""
        with self._lock:
            self._buffer.append(tick)
            return (
                len(self._buffer) >= FLUSH_TICKS
                or self._monotonic() - self._last_flush >= FLUSH_SECONDS
            )

    def flush(self) -> int:
        """Write every buffered tick; returns how many were written."""
        with self._lock:
            ticks, self._buffer = self._buffer, []
            self._last_flush = self._monotonic()
        groups: dict[tuple[str, str, str], list[Tick]] = {}
        for tick in ticks:
            groups.setdefault((tick.source_id, tick.symbol, tick.session_date), []).append(tick)
        for (source_id, symbol, session_date), items in groups.items():
            directory = self._directory(source_id, symbol, session_date)
            directory.mkdir(parents=True, exist_ok=True)
            first = items[0].exchange_time_utc.replace(":", "").replace("-", "")
            final = directory / f"part-{first}-{uuid4().hex[:12]}.parquet"
            temporary = directory / f".{final.name}.tmp"
            table = pa.Table.from_pylist([asdict(item) for item in items], schema=SCHEMA)
            pq.write_table(table, temporary, compression="zstd")
            os.replace(temporary, final)
        return len(ticks)

    def read(self, source_id: str, symbol: str, session_date: str) -> list[dict[str, Any]]:
        """Stored ticks of one symbol and ET date, in exchange-time order."""
        directory = self._directory(source_id, symbol, session_date)
        rows: list[dict[str, Any]] = []
        for path in sorted(directory.glob("part-*.parquet")):
            rows.extend(pq.read_table(path).to_pylist())
        rows.sort(key=lambda row: (row["exchange_time_utc"], row["received_at_utc"]))
        return rows

    def _directory(self, source_id: str, symbol: str, session_date: str) -> Path:
        path = (
            self.root
            / f"source_id={quote(source_id, safe='-_.')}"
            / f"symbol={quote(symbol, safe='-_.')}"
            / f"session_date={session_date}"
        ).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("tick path escapes the data directory")
        return path


def now_utc_text(now: Callable[[], datetime]) -> str:
    return now().astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
