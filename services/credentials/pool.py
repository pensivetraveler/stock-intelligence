"""One provider served through several registered keys, with failover on rate limits.

Keys are tried in the user's order. A key is skipped only while it cools down after the
provider reported a rate limit; any other error is reported as is, so a broken key is
never hidden behind a working one. Requests are not spread across keys to raise
throughput: brokers may treat that as circumventing their limits (ADR 0014).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from services.credentials.store import CredentialRecord, SecretStorageError
from services.market_data.daily import DailyBarsProvider
from services.market_data.provider import (
    BarsQuery,
    BarsResult,
    InvalidBarsQueryError,
    MarketDataError,
    MarketDataProvider,
    ProviderAuthError,
    ProviderRateLimitError,
)

DEFAULT_COOLDOWN_SECONDS = 60.0


@dataclass
class KeyStatus:
    state: str = "idle"  # idle | ok | cooling | error
    cooling_until: float | None = None
    last_error: str | None = None
    last_used_at_utc: str | None = None


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class PooledProvider:
    def __init__(
        self,
        provider: str,
        *,
        records: Callable[[], list[CredentialRecord]],
        secrets: Callable[[CredentialRecord], Mapping[str, str]],
        # Builds one adapter from a key's secrets and its account environment ("real"/"demo").
        build: Callable[[Mapping[str, str] | None, str | None], MarketDataProvider],
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.provider = provider
        self._records = records
        self._secrets = secrets
        self._build = build
        self._cooldown = cooldown_seconds
        self._monotonic = monotonic
        self._now = now or (lambda: datetime.now(timezone.utc))
        # Built without a key: lists instruments and capabilities, never sends a request.
        self._listing = build(None, None)
        self._adapters: dict[str, tuple[tuple[int, str | None], MarketDataProvider]] = {}
        self._status: dict[str, KeyStatus] = {}
        self._lock = threading.Lock()
        self.last_raw_sample: dict[str, Any] | None = None
        self.last_credential_id: str | None = None

    @property
    def source_id(self) -> str:
        return self._listing.source_id

    def capabilities(self) -> dict[str, Any]:
        return self._listing.capabilities()

    def instruments(self) -> list[dict[str, Any]]:
        return self._listing.instruments()

    def status(self) -> dict[str, dict[str, Any]]:
        """Runtime state per credential ID, for the key management screen."""
        with self._lock:
            now_mono = self._monotonic()
            report: dict[str, dict[str, Any]] = {}
            for credential_id, item in self._status.items():
                cooling = item.cooling_until is not None and item.cooling_until > now_mono
                report[credential_id] = {
                    "state": "cooling" if cooling else item.state,
                    "cooling_until_utc": _utc_text(
                        self._now() + timedelta(seconds=item.cooling_until - now_mono)
                    )
                    if cooling and item.cooling_until is not None
                    else None,
                    "last_error": item.last_error,
                    "last_used_at_utc": item.last_used_at_utc,
                }
            return report

    def get_bars(self, query: BarsQuery) -> BarsResult:
        return self._call(lambda adapter: adapter.get_bars(query))

    def get_daily_bars(self, query: BarsQuery) -> BarsResult:
        if not isinstance(self._listing, DailyBarsProvider):
            raise InvalidBarsQueryError(f"{self.provider} does not offer daily bars yet")
        return self._call(lambda adapter: cast(DailyBarsProvider, adapter).get_daily_bars(query))

    def _call(self, request: Callable[[MarketDataProvider], BarsResult]) -> BarsResult:
        candidates = self._candidates()
        if not candidates:
            with self._lock:
                errors = [
                    status.last_error
                    for record in self._records()
                    if record.enabled
                    and (status := self._status.get(record.credential_id)) is not None
                    and status.state == "error"
                    and status.last_error
                ]
            if errors:
                raise ProviderAuthError(f"{self.provider}: no usable key; {errors[0]}")
            raise ProviderAuthError(
                f"no enabled {self.provider} key is registered; add one on the Keys screen"
            )
        tried = 0
        for record, adapter in candidates:
            with self._lock:
                status = self._status.setdefault(record.credential_id, KeyStatus())
                if status.cooling_until is not None and status.cooling_until > self._monotonic():
                    continue
            tried += 1
            try:
                result = request(adapter)
            except ProviderRateLimitError as exc:
                with self._lock:
                    status.state = "cooling"
                    status.cooling_until = self._monotonic() + self._cooldown
                    status.last_error = str(exc)[:200]
                continue
            except MarketDataError as exc:
                with self._lock:
                    status.state = "error"
                    status.last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
                raise
            with self._lock:
                status.state = "ok"
                status.cooling_until = None
                status.last_error = None
                status.last_used_at_utc = _utc_text(self._now())
                self.last_credential_id = record.credential_id
                self.last_raw_sample = getattr(adapter, "last_raw_sample", None)
            return result
        detail = "every key hit the rate limit" if tried else "every key is cooling down"
        raise ProviderRateLimitError(
            f"{self.provider}: {detail}; retry after about {int(self._cooldown)} seconds"
        )

    def _candidates(self) -> list[tuple[CredentialRecord, MarketDataProvider]]:
        """Enabled keys in order, rebuilding adapters only for new or replaced secrets.

        Adapters keep their access tokens in memory, so reusing them avoids reissuing a
        token per request (KIS notifies the account holder on every issuance).
        """
        records = [record for record in self._records() if record.enabled]
        chosen: list[tuple[CredentialRecord, MarketDataProvider]] = []
        with self._lock:
            live = {record.credential_id for record in records}
            for stale in set(self._adapters) - live:
                del self._adapters[stale]
            for record in records:
                cached = self._adapters.get(record.credential_id)
                version = (record.secret_revision, record.environment)
                if cached is None or cached[0] != version:
                    try:
                        adapter = self._build(self._secrets(record), record.environment)
                    except (SecretStorageError, ValueError) as exc:
                        # A missing secret, or settings the adapter refuses (such as a
                        # demo account where the broker's demo domain is unknown).
                        self._adapters.pop(record.credential_id, None)
                        self._status.setdefault(record.credential_id, KeyStatus())
                        self._status[record.credential_id].state = "error"
                        self._status[record.credential_id].last_error = str(exc)[:200]
                        continue
                    cached = (version, adapter)
                    self._adapters[record.credential_id] = cached
                    self._status[record.credential_id] = KeyStatus()
                chosen.append((record, cached[1]))
        return chosen
