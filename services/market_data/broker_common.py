"""Shared pieces for read-only broker market-data adapters (KIS, Toss, Kiwoom).

Broker APIs put market data and order placement behind the same token. Every adapter
therefore talks through `AllowlistedClient`, which refuses any method and path that is
not explicitly listed, so no code path can reach an order endpoint.
"""

from __future__ import annotations

import re
import threading
import time as clock
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from packages.contracts.validator import validate_document
from services.market_data.calendar import CalendarError, TradingSessionName, UsEquityCalendar
from services.market_data.provider import (
    BarsQuery,
    BarsResult,
    InvalidBarsQueryError,
    ProviderDataError,
    ProviderUnavailableError,
    validate_bars_query,
)

MAX_QUERY_MINUTES = 1_440
SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")
VENUE_PATTERN = re.compile(r"^[A-Z]{4}$")
SESSIONS: tuple[TradingSessionName, ...] = ("pre", "regular", "post")


class BrokerConfigError(ValueError):
    """The environment does not describe a usable broker adapter configuration."""


class BlockedEndpointError(RuntimeError):
    """Code tried to call an endpoint outside the read-only allowlist (a programming error)."""


def utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_symbols(raw: str, setting: str) -> tuple[tuple[str, str], ...]:
    symbols: list[tuple[str, str]] = []
    for item in raw.split(","):
        if not item.strip():
            continue
        symbol, _, venue = item.strip().upper().partition(":")
        venue = venue or "XNAS"
        if not SYMBOL_PATTERN.fullmatch(symbol) or not VENUE_PATTERN.fullmatch(venue):
            raise BrokerConfigError(
                f"{setting} entries look like SYMBOL:MIC (e.g. SPY:ARCX): {item!r}"
            )
        symbols.append((symbol, venue))
    if not symbols:
        raise BrokerConfigError(f"{setting} must list at least one SYMBOL:MIC")
    return tuple(symbols)


def parse_sessions(raw: str, setting: str) -> frozenset[TradingSessionName]:
    chosen = {part.strip().lower() for part in raw.split(",") if part.strip()}
    unknown = chosen - set(SESSIONS)
    if not chosen or unknown:
        raise BrokerConfigError(f"{setting} lists pre, regular and/or post: {raw!r}")
    return frozenset(session for session in SESSIONS if session in chosen)


def require(values: dict[str, str] | Any, *names: str) -> list[str]:
    found = [str(values.get(name, "")).strip() for name in names]
    if not all(found):
        raise BrokerConfigError(f"set {' and '.join(names)} to use this provider")
    return found


def decimal_field(record: dict[str, Any], name: str) -> Decimal:
    try:
        value = Decimal(str(record[name]).replace(",", "").strip())
    except (KeyError, InvalidOperation) as exc:
        raise ProviderDataError(f"the provider returned a bar without a valid {name}") from exc
    if not value.is_finite():
        raise ProviderDataError(f"the provider returned a non-finite {name}")
    return value


class AllowlistedClient:
    """HTTP client that can only call listed (method, path) pairs."""

    def __init__(
        self,
        base_url: str,
        allowed: Iterable[tuple[str, str]],
        *,
        transport: httpx.BaseTransport | None = None,
        min_interval_seconds: float = 0.0,
        sleep: Callable[[float], None] = clock.sleep,
    ) -> None:
        self._allowed = {(method.upper(), path) for method, path in allowed}
        self._client = httpx.Client(
            base_url=base_url, timeout=httpx.Timeout(10.0, connect=5.0), transport=transport
        )
        self._min_interval = min_interval_seconds
        self._sleep = sleep
        self._last: float | None = None
        self._lock = threading.Lock()

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        if (method.upper(), path) not in self._allowed:
            raise BlockedEndpointError(f"{method} {path} is not on the read-only allowlist")
        with self._lock:
            if self._last is not None and self._min_interval:
                wait = self._min_interval - (clock.monotonic() - self._last)
                if wait > 0:
                    self._sleep(wait)
            self._last = clock.monotonic()
        try:
            return self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise ProviderUnavailableError("the provider did not answer in time") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                f"could not reach the provider: {type(exc).__name__}"
            ) from exc


@dataclass
class _Token:
    value: str
    expires_at: float


class TokenCache:
    """Holds one access token in memory only; refreshed on expiry or on request."""

    def __init__(
        self, issue: Callable[[], tuple[str, float]], *, margin_seconds: float = 300
    ) -> None:
        self._issue = issue
        self._margin = margin_seconds
        self._token: _Token | None = None
        self._lock = threading.Lock()

    def get(self, *, refresh: bool = False) -> str:
        with self._lock:
            now = clock.time()
            if refresh or self._token is None or self._token.expires_at - self._margin <= now:
                value, lifetime = self._issue()
                self._token = _Token(value, now + lifetime)
            return self._token.value


@dataclass(frozen=True)
class RawMinute:
    """One provider minute bar, already in UTC, prices exact."""

    start_utc: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True)
class SourceIdentity:
    source_id: str
    feed_scope: str
    dataset_revision: str
    adjustment: str  # "raw" when unadjusted prices were requested, else "unknown"
    covered_sessions: frozenset[TradingSessionName]
    publication_delay_minutes: int
    finality_minutes: int


def validate_minute_query(query: BarsQuery, now: datetime) -> None:
    if query.timeframe != "1m":
        raise InvalidBarsQueryError("broker adapters serve canonical 1m bars")
    validate_bars_query(query)
    for name, value in (("start_utc", query.start_utc), ("end_utc", query.end_utc)):
        if value.second:
            raise InvalidBarsQueryError(f"{name} must align to a minute boundary")
    if query.end_utc - query.start_utc > timedelta(minutes=MAX_QUERY_MINUTES):
        raise InvalidBarsQueryError(f"range cannot exceed {MAX_QUERY_MINUTES} minutes")
    if query.as_of_utc is not None and query.as_of_utc > now:
        raise InvalidBarsQueryError("as_of_utc cannot be in the future")


def build_result(
    query: BarsQuery,
    raw: Iterable[RawMinute],
    identity: SourceIdentity,
    calendar: UsEquityCalendar,
    now: datetime,
    history_start_utc: datetime | None = None,
) -> BarsResult:
    """Canonical bars plus coverage for every expected minute, with an honest reason for gaps.

    `history_start_utc` is the earliest minute the source could serve for this request
    (for example the oldest of a capped "latest N bars" response); older minutes are
    reported as `beyond_source_history`, not as minutes without trades.
    """
    try:
        expected = calendar.trading_minutes(query.start_utc, query.end_utc)
    except CalendarError as exc:
        raise InvalidBarsQueryError(str(exc)) from exc
    published_until = now - timedelta(minutes=identity.publication_delay_minutes)
    final_before = now - timedelta(minutes=identity.finality_minutes)
    delay = timedelta(minutes=identity.publication_delay_minutes)

    by_start: dict[datetime, RawMinute] = {}
    for minute in raw:
        if not query.start_utc <= minute.start_utc < query.end_utc:
            continue
        if minute.start_utc in by_start:
            raise ProviderDataError("the provider returned two bars for the same minute")
        by_start[minute.start_utc] = minute

    visible: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    for start, session in expected:
        end = start + timedelta(minutes=1)
        item = by_start.get(start)
        available_at = end + delay
        if session not in identity.covered_sessions:
            reason = "session_not_covered"
        elif history_start_utc is not None and start < history_start_utc:
            reason = "beyond_source_history"
        elif item is not None and (query.as_of_utc is None or available_at <= query.as_of_utc):
            visible.append(
                _bar(
                    query.instrument_id,
                    item,
                    session,
                    end <= final_before,
                    available_at,
                    identity,
                    calendar,
                )
            )
            continue
        elif item is not None:
            reason = "not_available_as_of_cutoff"
        elif end > published_until:
            reason = "outside_entitlement"
        elif end > final_before:
            reason = "not_yet_final"
        elif query.as_of_utc is not None and available_at > query.as_of_utc:
            reason = "not_available_as_of_cutoff"
        else:
            reason = "provider_no_bar"
        missing.append({"start_utc": utc_text(start), "end_utc": utc_text(end), "reason": reason})

    coverage = {
        "kind": "coverage_manifest",
        "schema_version": "0.1",
        "instrument_id": query.instrument_id,
        "timeframe": "1m",
        "requested_start_utc": utc_text(query.start_utc),
        "requested_end_utc": utc_text(query.end_utc),
        "coverage_status": "complete"
        if visible and not missing
        else ("partial" if visible else "empty"),
        "verification_status": "unverified",
        "observed_interval_count": len(visible),
        "expected_interval_count": len(expected),
        "missing_intervals": missing,
        "source_id": identity.source_id,
        "source_feed_scope": identity.feed_scope,
        "adjustment": identity.adjustment,
        "dataset_revision": identity.dataset_revision,
        "point_in_time_cutoff_utc": utc_text(query.as_of_utc)
        if query.as_of_utc is not None
        else None,
    }
    result = BarsResult(tuple(visible), coverage)
    try:
        validate_document(result.to_document())
    except ValueError as exc:
        raise ProviderDataError(f"provider data violates the contract: {exc}") from exc
    return result


def _bar(
    instrument_id: str,
    item: RawMinute,
    session: TradingSessionName,
    final: bool,
    available_at: datetime,
    identity: SourceIdentity,
    calendar: UsEquityCalendar,
) -> dict[str, Any]:
    return {
        "kind": "price_bar",
        "schema_version": "0.1",
        "instrument_id": instrument_id,
        "timeframe": "1m",
        "interval_start_utc": utc_text(item.start_utc),
        "interval_end_utc": utc_text(item.start_utc + timedelta(minutes=1)),
        "open": format(item.open, "f"),
        "high": format(item.high, "f"),
        "low": format(item.low, "f"),
        "close": format(item.close, "f"),
        "volume": format(item.volume, "f"),
        "volume_unit": "shares",
        "session": session,
        "is_final": final,
        "quality": "complete" if final else "provisional",
        "lineage": {
            "kind": "provider",
            "source_timeframe": "1m",
            "expected_source_bar_count": 1,
            "observed_source_bar_count": 1,
            "source_dataset_revision": None,
            "calendar_id": "XNAS",
            "calendar_version": calendar.calendar_version,
        },
        "adjustment": identity.adjustment,
        "source_id": identity.source_id,
        "source_feed_scope": identity.feed_scope,
        "dataset_revision": identity.dataset_revision,
        "available_at_utc": utc_text(available_at),
    }


def instrument_listing(
    symbol: str, venue: str, source_id: str, revision: str, identity_source: str
) -> dict[str, Any]:
    return {
        "instrument": {
            "kind": "instrument_ref",
            "schema_version": "0.1",
            "instrument_id": f"us:{symbol.lower()}:{venue.lower()}:2026",
            "symbol": symbol,
            "venue": venue,
            "asset_class": "us_equity",
            "currency": "USD",
            "identity_as_of": "2026-01-01T00:00:00Z",
            "identity_source": identity_source,
        },
        "source_id": source_id,
        "dataset_revision": revision,
        "session_dates": [],
    }


def capabilities(
    source_id: str, feed: str, now: datetime, *, verification_status: str = "documented"
) -> dict[str, Any]:
    """`documented` until a live check with the user's key has passed (`integration_tested`)."""
    return {
        "kind": "source_capabilities",
        "schema_version": "0.1",
        "source_id": source_id,
        "source_type": "broker_market",
        "verification_status": verification_status,
        "entitlement_status": "unknown",
        "license_status": "unknown",
        "feeds": [feed],
        "rights_local_retention": "unknown",
        "observed_at_utc": utc_text(now),
    }
