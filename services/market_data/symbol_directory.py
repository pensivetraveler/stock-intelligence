"""Searchable directory of US-listed stocks, fetched from the market-data provider.

The provider's asset list (about 12,000 symbols) is kept in `SIW_DATA_DIR/symbols/` and
refreshed when it is more than a day old. If a refresh fails, the previous copy is served
and marked stale, and the next attempt waits a few minutes so a provider outage does not
turn every keystroke into a request. Search matches tickers first, then company names.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from services.market_data.broker_common import instrument_listing

MAX_AGE = timedelta(hours=24)
RETRY_AFTER_FAILURE = timedelta(minutes=5)
MAX_RESULTS = 50
MAX_QUERY_LENGTH = 40


class SymbolDirectoryUnavailableError(RuntimeError):
    """No directory has been fetched yet and the provider could not supply one."""


@dataclass(frozen=True)
class SymbolEntry:
    symbol: str
    name: str
    venue: str

    @property
    def instrument_id(self) -> str:
        return f"us:{self.symbol.lower()}:{self.venue.lower()}:2026"


@dataclass(frozen=True)
class SearchResult:
    entries: tuple[SymbolEntry, ...]
    fetched_at_utc: str
    stale: bool


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _rank(entry: SymbolEntry, query: str) -> int | None:
    symbol, name = entry.symbol, entry.name.upper()
    if symbol == query:
        return 0
    if symbol.startswith(query):
        return 1
    if any(word.startswith(query) for word in name.replace(",", " ").split()):
        return 2
    if query in name:
        return 3
    return None


class SymbolDirectory:
    def __init__(
        self,
        root: Path,
        source_id: str,
        fetch: Callable[[], list[dict[str, str]]],
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = root / "symbols" / f"{source_id}.json"
        self.source_id = source_id
        self._fetch = fetch
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._entries: tuple[SymbolEntry, ...] | None = None
        self._by_id: dict[str, SymbolEntry] = {}
        self._fetched_at: datetime | None = None
        self._failed_at: datetime | None = None

    def search(self, query: str, limit: int = 20) -> SearchResult:
        entries, fetched_at, stale = self._current()
        wanted = query.strip().upper()[:MAX_QUERY_LENGTH]
        ranked: list[tuple[int, int, str, SymbolEntry]] = []
        if wanted:
            for entry in entries:
                rank = _rank(entry, wanted)
                if rank is not None:
                    ranked.append((rank, len(entry.symbol), entry.symbol, entry))
        ranked.sort(key=lambda item: item[:3])
        chosen = tuple(item[3] for item in ranked[: min(limit, MAX_RESULTS)])
        return SearchResult(chosen, _utc_text(fetched_at), stale)

    def lookup(self, instrument_id: str) -> SymbolEntry | None:
        """A known entry, from memory or disk only: never triggers a provider request."""
        with self._lock:
            if self._entries is None:
                self._load()
            return self._by_id.get(instrument_id)

    def known(self) -> dict[str, SymbolEntry]:
        """Every known entry by instrument ID, from memory or disk only."""
        with self._lock:
            if self._entries is None:
                self._load()
            return dict(self._by_id)

    def listing(self, entry: SymbolEntry) -> dict[str, Any]:
        fetched = (self._fetched_at or self._now()).date().isoformat()
        return instrument_listing(
            entry.symbol,
            entry.venue,
            self.source_id,
            f"{self.source_id}-assets-{fetched}",
            f"{self.source_id}-assets",
        )

    def _current(self) -> tuple[tuple[SymbolEntry, ...], datetime, bool]:
        with self._lock:
            if self._entries is None:
                self._load()
            now = self._now()
            fresh = self._fetched_at is not None and now - self._fetched_at < MAX_AGE
            waiting = self._failed_at is not None and now - self._failed_at < RETRY_AFTER_FAILURE
            if not fresh and not waiting:
                try:
                    self._store(self._fetch(), now)
                    self._failed_at = None
                except Exception as exc:
                    self._failed_at = now
                    if self._entries is None:
                        raise SymbolDirectoryUnavailableError(
                            f"the symbol list could not be fetched: {str(exc)[:200]}"
                        ) from exc
            if self._entries is None or self._fetched_at is None:
                raise SymbolDirectoryUnavailableError("the symbol list could not be fetched")
            stale = now - self._fetched_at >= MAX_AGE
            return self._entries, self._fetched_at, stale

    def _load(self) -> None:
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
            fetched_text = str(document["fetched_at_utc"]).replace("Z", "+00:00")
            fetched_at = datetime.fromisoformat(fetched_text)
            entries = tuple(
                SymbolEntry(str(item["symbol"]), str(item["name"]), str(item["venue"]))
                for item in document["symbols"]
            )
        except (OSError, ValueError, KeyError, TypeError):
            return  # missing or unreadable: fetched again on the next search
        self._set(entries, fetched_at)

    def _store(self, assets: list[dict[str, str]], fetched_at: datetime) -> None:
        unique = {
            (item["symbol"], item["venue"]): SymbolEntry(
                item["symbol"], item["name"], item["venue"]
            )
            for item in assets
        }
        entries = tuple(sorted(unique.values(), key=lambda entry: entry.symbol))
        if not entries:
            raise ValueError("the provider returned an empty symbol list")
        document = {
            "source_id": self.source_id,
            "fetched_at_utc": _utc_text(fetched_at),
            "symbols": [
                {"symbol": entry.symbol, "name": entry.name, "venue": entry.venue}
                for entry in entries
            ],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        temporary.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, self.path)
        self._set(entries, fetched_at)

    def _set(self, entries: tuple[SymbolEntry, ...], fetched_at: datetime) -> None:
        self._entries = entries
        self._by_id = {entry.instrument_id: entry for entry in entries}
        self._fetched_at = fetched_at
