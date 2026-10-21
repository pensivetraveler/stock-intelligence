"""Shared HTTP plumbing for the public sources: errors, pacing and the refetch rule."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta

MAX_AGE = timedelta(hours=6)
#: A cutoff later than the last download refetches, but not more often than this.
MIN_AGE = timedelta(minutes=5)
TIMEOUT_SECONDS = 30.0


class SourceError(RuntimeError):
    """A public source could not be reached or answered unexpectedly."""


class SourceConfigError(SourceError):
    """A source needs a setting or credential that is missing; nothing was sent."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class RateLimiter:
    """At most one request per `interval` seconds, across threads."""

    def __init__(
        self,
        interval: float,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._interval = interval
        self._monotonic = monotonic
        self._sleep = sleep
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = self._monotonic()
            if now < self._next:
                self._sleep(self._next - now)
                now = self._next
            self._next = now + self._interval


def needs_fetch(last: datetime | None, cutoff: datetime, now: datetime) -> bool:
    """Download again when never fetched, stale, or older than the cutoff asked about."""
    if last is None:
        return True
    age = now - last
    return age > MAX_AGE or (last < cutoff and age > MIN_AGE)
