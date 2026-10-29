"""Background principle checks for the trade list (ADR 0042, step 3b).

Checks that need bars are slow over thousands of trades, so they run one at a time on a
worker thread, paced so market data requests stay gentle. Results are stored with a
`basis` (principles version, account capital, the trade's fills); a stored result whose
basis no longer matches is treated as unchecked and queued again. The queue itself can be
mirrored to storage (`added`/`finished`) so trades still waiting when the app closes are
queued again after a restart.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from typing import Any

PAUSE_SECONDS = 0.05


class CheckRunner:
    def __init__(
        self,
        check: Callable[[str], None],
        pause: float = PAUSE_SECONDS,
        *,
        added: Callable[[list[str]], None] | None = None,
        finished: Callable[[str], None] | None = None,
    ) -> None:
        self._check = check
        self._pause = pause
        self._added = added
        self._finished = finished
        self._lock = threading.Lock()
        self._queue: deque[str] = deque()
        self._queued: set[str] = set()
        self._thread: threading.Thread | None = None
        self._done = 0
        self._failed = 0
        self._last_error: str | None = None
        self._running: str | None = None

    def enqueue(self, trade_ids: Iterable[str]) -> int:
        added: list[str] = []
        with self._lock:
            for trade_id in trade_ids:
                if trade_id not in self._queued:
                    self._queue.append(trade_id)
                    self._queued.add(trade_id)
                    added.append(trade_id)
            if added and self._added is not None:
                self._added(added)  # before the worker can finish (and forget) any of them
            if self._queue and (self._thread is None or not self._thread.is_alive()):
                self._thread = threading.Thread(
                    target=self._work, name="principle-checks", daemon=True
                )
                self._thread.start()
        return len(added)

    def _work(self) -> None:
        while True:
            with self._lock:
                if not self._queue:
                    self._running = None
                    return
                trade_id = self._queue.popleft()
                self._queued.discard(trade_id)
                self._running = trade_id
            try:
                self._check(trade_id)
                with self._lock:
                    self._done += 1
            except Exception as exc:  # noqa: BLE001 - one bad trade must not stop the rest
                with self._lock:
                    self._failed += 1
                    self._last_error = f"{type(exc).__name__}: {exc}"[:300]
            if self._finished is not None:
                # A failed check is not retried after a restart; storage must not stop the worker.
                with contextlib.suppress(Exception):
                    self._finished(trade_id)
            time.sleep(self._pause)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "queued": len(self._queue),
                "running": self._running is not None,
                "done": self._done,
                "failed": self._failed,
                "last_error": self._last_error,
            }

    def wait(self, timeout: float = 30.0) -> None:
        """Until the queue is empty (tests)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            thread = self._thread
            if thread is None or not thread.is_alive():
                return
            thread.join(0.05)
