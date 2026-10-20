"""Searchable directory of US-listed stocks, fetched from the market-data provider.

The provider's asset list (about 12,000 symbols) is kept in `SIW_DATA_DIR/symbols/` and
refreshed when it is more than a day old. If a refresh fails, the previous copy is served
and marked stale, and the next attempt waits a few minutes so a provider outage does not
turn every keystroke into a request. Search matches tickers first, then company names,
including Korean names when a source of them is given (Toss; see toss_info.korean_names).
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
KOREAN_VENUES = frozenset({"XKRX", "XKOS"})


class SymbolDirectoryUnavailableError(RuntimeError):
    """No directory has been fetched yet and the provider could not supply one."""


@dataclass(frozen=True)
class SymbolEntry:
    symbol: str
    name: str
    venue: str
    local_name: str | None = None

    @property
    def korean(self) -> bool:
        return self.venue in KOREAN_VENUES

    @property
    def instrument_id(self) -> str:
        market = "kr" if self.korean else "us"
        return f"{market}:{self.symbol.lower()}:{self.venue.lower()}:2026"


@dataclass(frozen=True)
class SearchResult:
    entries: tuple[SymbolEntry, ...]
    fetched_at_utc: str
    stale: bool


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _rank(entry: SymbolEntry, query: str) -> int | None:
    """0 exact ticker or Korean name, 1 ticker prefix, 2 name word prefix, 3 name match."""
    symbol, name = entry.symbol, entry.name.upper()
    local = (entry.local_name or "").upper()
    if symbol == query or local == query:
        return 0
    if symbol.startswith(query):
        return 1
    words = name.replace(",", " ").split() + local.split()
    if any(word.startswith(query) for word in words):
        return 2
    if query in name or query in local:
        return 3
    return None


class SymbolDirectory:
    def __init__(
        self,
        root: Path,
        source_id: str,
        fetch: Callable[[], list[dict[str, str]]],
        *,
        local_names: Callable[[], dict[str, str]] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = root / "symbols" / f"{source_id}.json"
        self.source_id = source_id
        self._fetch = fetch
        self._local_names = local_names
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._entries: tuple[SymbolEntry, ...] | None = None
        self._by_id: dict[str, SymbolEntry] = {}
        self._fetched_at: datetime | None = None
        self._failed_at: datetime | None = None
        # Whether the stored list has Korean names, and whether this process tried to add them.
        self._has_names = False
        self._names_tried = False

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
        listing = instrument_listing(
            entry.symbol,
            entry.venue,
            self.source_id,
            f"{self.source_id}-assets-{fetched}",
            f"{self.source_id}-assets",
        )
        if entry.korean:
            listing["instrument"].update(
                instrument_id=entry.instrument_id, asset_class="kr_equity", currency="KRW"
            )
        return listing

    def _current(self) -> tuple[tuple[SymbolEntry, ...], datetime, bool]:
        with self._lock:
            if self._entries is None:
                self._load()
            now = self._now()
            fresh = self._fetched_at is not None and now - self._fetched_at < MAX_AGE
            # A list saved without Korean names is refreshed once when a source exists.
            fresh = fresh and (self._local_names is None or self._has_names or self._names_tried)
            waiting = self._failed_at is not None and now - self._failed_at < RETRY_AFTER_FAILURE
            if not fresh and not waiting:
                self._names_tried = True
                try:
                    self._store(self._fetch(), self._names(), now)
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
                SymbolEntry(
                    str(item["symbol"]),
                    str(item["name"]),
                    str(item["venue"]),
                    str(item["local_name"]) if item.get("local_name") else None,
                )
                for item in document["symbols"]
            )
        except (OSError, ValueError, KeyError, TypeError):
            return  # missing or unreadable: fetched again on the next search
        self._set(entries, fetched_at)
        self._has_names = bool(document.get("with_local_names"))

    def _names(self) -> dict[str, str]:
        """Korean names by ticker; the previous ones when the source fails or is absent."""
        previous = {e.symbol: e.local_name for e in self._entries or () if e.local_name}
        if self._local_names is None:
            return previous
        try:
            return self._local_names() or previous
        except Exception:
            return previous

    def _store(
        self, assets: list[dict[str, str]], names: dict[str, str], fetched_at: datetime
    ) -> None:
        unique = {
            (item["symbol"], item["venue"]): SymbolEntry(
                item["symbol"], item["name"], item["venue"], names.get(item["symbol"])
            )
            for item in assets
        }
        entries = tuple(sorted(unique.values(), key=lambda entry: entry.symbol))
        if not entries:
            raise ValueError("the provider returned an empty symbol list")
        document = {
            "source_id": self.source_id,
            "fetched_at_utc": _utc_text(fetched_at),
            "with_local_names": bool(names),
            "symbols": [
                {
                    "symbol": entry.symbol,
                    "name": entry.name,
                    "venue": entry.venue,
                    "local_name": entry.local_name,
                }
                for entry in entries
            ],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        temporary.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, self.path)
        self._set(entries, fetched_at)
        self._has_names = bool(names)

    def _set(self, entries: tuple[SymbolEntry, ...], fetched_at: datetime) -> None:
        self._entries = entries
        self._by_id = {entry.instrument_id: entry for entry in entries}
        self._fetched_at = fetched_at


def search_all(
    directories: list[SymbolDirectory], query: str, limit: int = 20
) -> tuple[list[tuple[SymbolDirectory, SymbolEntry]], str, bool]:
    """Search several directories (US and Korean listings) and rank the results together.

    Returns the results with their directory, the oldest fetch time and whether any
    directory is stale. A directory that cannot answer is skipped when another can.
    """
    wanted = query.strip().upper()[:MAX_QUERY_LENGTH]
    found: list[tuple[int, int, str, SymbolDirectory, SymbolEntry]] = []
    times: list[str] = []
    stale = False
    errors: list[SymbolDirectoryUnavailableError] = []
    for directory in directories:
        try:
            result = directory.search(query, MAX_RESULTS)
        except SymbolDirectoryUnavailableError as exc:
            errors.append(exc)
            continue
        times.append(result.fetched_at_utc)
        stale = stale or result.stale
        for entry in result.entries:
            rank = _rank(entry, wanted)
            if rank is not None:
                found.append((rank, len(entry.symbol), entry.symbol, directory, entry))
    if not times:
        raise errors[0] if errors else SymbolDirectoryUnavailableError("no symbol list")
    found.sort(key=lambda item: item[:3])
    chosen = [(item[3], item[4]) for item in found[: min(limit, MAX_RESULTS)]]
    return chosen, min(times), stale
