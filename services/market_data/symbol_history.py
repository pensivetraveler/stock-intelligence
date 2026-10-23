"""US ticker changes and delistings (ADR 0033).

From Alpaca: name changes (old ticker, new ticker, process date), delisting-like corporate
actions and the inactive asset list, kept in `SIW_DATA_DIR/symbols/history-<source>.json`
and refreshed when more than a day old (a failed refresh keeps the previous copy).

A broker trade in ticker S on date D resolves to:

- **renamed**: the company's current ticker F (renames after D followed), when F is an
  active listing on a US exchange; its chart includes the years under S (Alpaca maps a
  ticker's history across name changes);
- **listed**: S itself, when it is active and was not renamed after D;
- **delisted**: otherwise. The instrument stays `us:S:<venue>:2026` and is registered in
  `symbols/delisted-instruments.json` with `asof` = the latest trade date seen, so its
  bars are read as that company (Alpaca `asof`) and end where exchange trading ended.
  When another company uses S today, the delisted one gets its own ID with a year
  suffix (`us:s.d23:xnas:2026`: the year it was renamed away, else of its first trade
  seen), so today's S keeps its own chart.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

MAX_AGE = timedelta(hours=24)
RETRY_AFTER_FAILURE = timedelta(minutes=5)
DEFAULT_VENUE = "XNAS"
# An instrument symbol that fits the US instrument ID (instruments.US_ID).
KEY = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")


@dataclass(frozen=True)
class Rename:
    old: str
    new: str
    on: date  # process date: the old ticker traded until the day before


@dataclass(frozen=True)
class Resolution:
    kind: Literal["listed", "renamed", "delisted"]
    symbol: str  # the ticker whose chart holds the trade
    venue: str
    venue_verified: bool
    moved_to: str | None = None  # delisted: the ticker it trades under now, if renamed


@dataclass(frozen=True)
class DelistedInstrument:
    symbol: str  # the instrument's symbol: the ticker, or TICKER.DYY when reused
    venue: str
    venue_verified: bool
    asof: date  # a date on which the ticker belonged to the traded company
    name: str | None
    moved_to: str | None
    delisted_on: date | None  # the last delisting-like corporate action, when known
    ticker: str = ""  # the ticker Alpaca knows it by (empty: the same as symbol)

    @property
    def instrument_id(self) -> str:
        return f"us:{self.symbol.lower()}:{self.venue.lower()}:2026"


def _write(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


class SymbolHistory:
    def __init__(
        self,
        root: Path,
        source_id: str,
        fetch: Callable[[], dict[str, Any]],
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        """`fetch` returns {"renames": [{old, new, on}], "delisted": [{symbol, name,
        venue, on, reason}]} (Alpaca: `AlpacaMarketDataProvider.symbol_history`)."""
        self.path = root / "symbols" / f"history-{source_id}.json"
        self.registry_path = root / "symbols" / "delisted-instruments.json"
        self._fetch = fetch
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._renames: dict[str, list[Rename]] = {}
        self._inactive: dict[str, dict[str, Any]] = {}
        self._fetched_at: datetime | None = None
        self._failed_at: datetime | None = None
        self._registry: dict[str, DelistedInstrument] | None = None

    # --- the fetched history ---------------------------------------------------------

    def _ensure(self) -> None:
        if self._fetched_at is None:
            self._load()
        now = self._now()
        fresh = self._fetched_at is not None and now - self._fetched_at < MAX_AGE
        waiting = self._failed_at is not None and now - self._failed_at < RETRY_AFTER_FAILURE
        if fresh or waiting:
            return
        try:
            payload = self._fetch()
        except Exception:  # noqa: BLE001  (an outage keeps the previous copy)
            self._failed_at = now
            if self._fetched_at is None:
                raise
            return
        document = {**payload, "fetched_at_utc": now.isoformat()}
        _write(self.path, document)
        self._set(document, now)

    def _load(self) -> None:
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
            self._set(stored, datetime.fromisoformat(stored["fetched_at_utc"]))
        except (OSError, ValueError, KeyError, TypeError):
            return

    def _set(self, document: dict[str, Any], fetched_at: datetime) -> None:
        renames: dict[str, list[Rename]] = {}
        for item in document["renames"]:
            rename = Rename(item["old"], item["new"], date.fromisoformat(item["on"]))
            renames.setdefault(rename.old, []).append(rename)
        for items in renames.values():
            items.sort(key=lambda item: item.on)
        self._renames = renames
        self._inactive = {item["symbol"]: item for item in document["delisted"]}
        self._fetched_at = fetched_at

    # --- the registry of delisted instruments seen in trades ---------------------------

    def _registered(self) -> dict[str, DelistedInstrument]:
        if self._registry is None:
            self._registry = {}
            try:
                stored = json.loads(self.registry_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                stored = {}
            for symbol, item in stored.items():
                self._registry[symbol] = DelistedInstrument(
                    symbol,
                    item["venue"],
                    item["venue_verified"],
                    date.fromisoformat(item["asof"]),
                    item.get("name"),
                    item.get("moved_to"),
                    None
                    if item.get("delisted_on") is None
                    else date.fromisoformat(item["delisted_on"]),
                    item.get("ticker") or symbol,
                )
        return self._registry

    def _register(self, entry: DelistedInstrument) -> None:
        registry = self._registered()
        before = registry.get(entry.symbol)
        if before is not None and before.asof >= entry.asof:
            return  # the latest trade date seen identifies the company best
        registry[entry.symbol] = entry
        _write(
            self.registry_path,
            {
                symbol: {
                    "venue": item.venue,
                    "venue_verified": item.venue_verified,
                    "asof": item.asof.isoformat(),
                    "name": item.name,
                    "moved_to": item.moved_to,
                    "delisted_on": None
                    if item.delisted_on is None
                    else item.delisted_on.isoformat(),
                    "ticker": item.ticker or symbol,
                }
                for symbol, item in sorted(registry.items())
            },
        )

    # --- questions -------------------------------------------------------------------

    def resolve(
        self, symbol: str, on: date, listed_venue: Callable[[str], str | None]
    ) -> Resolution:
        """Where a trade in `symbol` on `on` belongs. `listed_venue(ticker)` is the MIC
        of an active US-exchange listing using that ticker now, or None."""
        with self._lock:
            self._ensure()
            original = symbol.upper()
            current, when = original, on
            renamed_on: date | None = None
            for _step in range(10):  # chains are short; this also stops loops
                later = [r for r in self._renames.get(current, []) if r.on > when]
                if not later:
                    break
                renamed_on = renamed_on or later[0].on
                current, when = later[0].new, later[0].on
            venue = listed_venue(current)
            if venue is not None:
                kind: Literal["listed", "renamed"] = "listed" if current == original else "renamed"
                return Resolution(kind, current, venue, True)
            inactive = self._inactive.get(original)
            venue = None if inactive is None else inactive.get("venue")
            moved_to = None if current == original else current
            key = original
            if listed_venue(original) is not None:
                # Another company uses the ticker today: this one needs its own ID.
                reused = [
                    item
                    for item in self._registered().values()
                    if item.ticker == original and item.symbol != original
                ]
                year = (renamed_on or on).year
                key = reused[0].symbol if reused else f"{original}.D{year % 100:02d}"
            if not KEY.fullmatch(key):
                return Resolution("delisted", key, DEFAULT_VENUE, False, moved_to)
            registered = self._registered().get(key)
            if venue is None and registered is not None and registered.venue_verified:
                # A venue verified later (SIP tape) is kept, so the ID stays the same.
                venue = registered.venue
            entry = DelistedInstrument(
                key,
                venue or DEFAULT_VENUE,
                venue is not None,
                on,
                None if inactive is None else inactive.get("name"),
                moved_to,
                None
                if inactive is None or inactive.get("on") is None
                else date.fromisoformat(inactive["on"]),
                original,
            )
            self._register(entry)
            return Resolution("delisted", key, entry.venue, entry.venue_verified, moved_to)

    def delisted(self, symbol: str) -> DelistedInstrument | None:
        with self._lock:
            return self._registered().get(symbol.upper())

    def delisted_instruments(self) -> list[DelistedInstrument]:
        with self._lock:
            return sorted(self._registered().values(), key=lambda item: item.symbol)

    def traded_as(self, symbol: str, on: date) -> str:
        """The ticker a company used on `on`, given the ticker it uses now (renames after
        `on` followed backwards, newest first). Toss reports today's ticker, often an OTC
        one such as MMATQ for a trade made as MMAT."""
        with self._lock:
            self._ensure()
            into: dict[str, list[Rename]] = {}
            for items in self._renames.values():
                for rename in items:
                    into.setdefault(rename.new, []).append(rename)
            current, before = symbol.upper(), date.max
            for _step in range(10):
                earlier = [r for r in into.get(current, []) if on < r.on < before]
                if not earlier:
                    break
                rename = max(earlier, key=lambda item: item.on)
                current, before = rename.old, rename.on
            return current

    def renamed_to(self, symbol: str) -> str | None:
        """The ticker a renamed ticker trades under now (chains followed), if any."""
        with self._lock:
            if self._fetched_at is None:
                self._load()
            current, seen = symbol.upper(), set()
            while current in self._renames and current not in seen:
                seen.add(current)
                current = self._renames[current][-1].new
            return None if current == symbol.upper() else current


# Bars of a delisted instrument are read as the company that was traded (Alpaca `asof`),
# so a later company reusing the ticker is never mixed in. The API registers the history.
_REGISTERED: list[SymbolHistory] = []


def register(history: SymbolHistory | None) -> None:
    _REGISTERED[:] = [] if history is None else [history]


def resolution_of(symbol: str, instrument_id: str) -> str | None:
    """`renamed` when a trade went on another ticker's chart, `delisted` when its ticker
    is a registered delisted instrument, else None (a plain listing)."""
    parts = instrument_id.split(":")
    if len(parts) != 4 or parts[0] != "us":
        return None
    if _REGISTERED and _REGISTERED[0].delisted(parts[1]) is not None:
        return "delisted"
    return "renamed" if parts[1].upper() != symbol.upper() else None


def asof_for(symbol: str) -> date | None:
    """`asof` for an instrument symbol (ticker, or TICKER.DYY) when it is delisted."""
    if not _REGISTERED:
        return None
    entry = _REGISTERED[0].delisted(symbol)
    return None if entry is None else entry.asof


def ticker_for(symbol: str) -> str:
    """The ticker Alpaca knows an instrument symbol by (TICKER.DYY -> TICKER)."""
    if not _REGISTERED:
        return symbol
    entry = _REGISTERED[0].delisted(symbol)
    return symbol if entry is None or not entry.ticker else entry.ticker
