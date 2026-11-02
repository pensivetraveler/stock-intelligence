"""SEC responses and the SEC request limit shared with the dilution analyzer (ADR 0046).

The layout and the lock are the analyzer's own (`dilution/sec/client.py`), so both programs
read each other's files and together stay under one limit. A file holds the decoded bytes
of one complete 200 response. Its time says when some program downloaded it, not when SIW
knew the record: that stays `first_observed_utc` in SIW's own cache.
"""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from services.market_data.filing_splits import shared_data_dir
from services.market_data.storage import _lock_descriptor, _unlock_descriptor

SEC_HOSTS = frozenset({"www.sec.gov", "data.sec.gov"})
IMMUTABLE_PATH = "/Archives/"
INTERVAL_SECONDS = 0.2  # both programs compute their wait from this: 5 per second in total
LOCK_WAIT_SECONDS = 60.0
PENDING_SECONDS = 1e6  # written while sending, so a holder that dies still leaves an interval


class SharedSecFiles:
    def __init__(
        self,
        root: Path,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._files = root / "sec"
        self._lock = root / "sec.lock"
        self._monotonic = monotonic
        self._sleep = sleep
        self._wall_clock = wall_clock

    def path(self, url: str) -> Path:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname not in SEC_HOSTS:
            raise ValueError("not an SEC address")
        digest = hashlib.sha256(url.encode()).hexdigest()[:16]
        return (
            self._files / parts.hostname / f"{digest}-{PurePosixPath(parts.path).name or 'index'}"
        )

    def fresh(self, url: str, max_age: timedelta) -> bytes | None:
        """The file for `url` when some program downloaded it within `max_age`."""
        path = self.path(url)
        try:
            if self._wall_clock() - path.stat().st_mtime > max_age.total_seconds():
                return None
            return path.read_bytes()
        except OSError:
            return None

    def store(self, url: str, body: bytes) -> None:
        """Keep a complete response. An immutable file that exists is never replaced."""
        path = self.path(url)
        if IMMUTABLE_PATH in urlsplit(url).path and path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    @contextmanager
    def request_turn(self) -> Iterator[None]:
        """Hold the lock, spaced from the last request, until the response has ended."""
        self._lock.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self._lock, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            limit = self._monotonic() + LOCK_WAIT_SECONDS
            while not _lock_descriptor(descriptor, blocking=False):
                if self._monotonic() > limit:
                    raise TimeoutError("another program holds the SEC request lock")
                self._sleep(0.05)
            try:
                self._wait_for_interval(descriptor)
                self._stamp(descriptor, PENDING_SECONDS)
                try:
                    yield
                finally:
                    self._stamp(descriptor, 0.0)
            finally:
                _unlock_descriptor(descriptor)
        finally:
            os.close(descriptor)

    def _wait_for_interval(self, descriptor: int) -> None:
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            last = float(os.read(descriptor, 64).decode("ascii").strip())
        except (UnicodeDecodeError, ValueError):
            return
        if last != last:  # NaN
            return
        gap = self._monotonic() - last
        # A time ahead of the clock: a holder died while sending, or the machine restarted.
        wait = INTERVAL_SECONDS if gap < 0 else INTERVAL_SECONDS - gap
        if wait > 0:
            self._sleep(min(wait, INTERVAL_SECONDS))

    def _stamp(self, descriptor: int, ahead: float) -> None:
        os.ftruncate(descriptor, 0)
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.write(descriptor, repr(self._monotonic() + ahead).encode("ascii"))


def shared_sec_files() -> SharedSecFiles | None:
    root = shared_data_dir()
    return None if root is None else SharedSecFiles(root)
