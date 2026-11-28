"""Turn SIGTERM/SIGINT into a `threading.Event` without taking a lock in the handler.

A handler that calls `Event.set()` takes the event's internal lock. Python runs handlers in the
main thread between bytecodes, so a second signal can arrive while the first handler (or the main
thread's own `Event.wait()`) holds that lock; the nested `set()` then waits on a lock its own thread
holds and the process hangs until SIGKILL. systemd sends SIGTERM to every process of the unit and
uv forwards its copy to python, so the runner sees two signals on every stop (2026-11-28 mini PC).

Here the handler only writes a byte to a pipe — no Python lock — and a small daemon thread blocked
on the pipe sets the event. Any number of signals, at any moment, leave the handler at once.
"""

from __future__ import annotations

import contextlib
import os
import signal
import threading
from collections.abc import Iterable

DEFAULT_SIGNALS = (signal.SIGTERM, signal.SIGINT)


def stop_on_signals(
    stop: threading.Event | None = None, signals: Iterable[int] = DEFAULT_SIGNALS
) -> threading.Event:
    """Install handlers that set `stop` (a new event when omitted) and return the event.

    Call from the main thread, like `signal.signal`.
    """
    event = stop if stop is not None else threading.Event()
    read_end, write_end = os.pipe()
    os.set_blocking(write_end, False)

    def handle(_signum: int, _frame: object) -> None:
        # A full pipe already holds a wake-up byte; nothing more is needed.
        with contextlib.suppress(OSError):
            os.write(write_end, b"\0")

    def wait_for_signal() -> None:
        try:
            os.read(read_end, 1)
        finally:
            os.close(read_end)
        event.set()

    threading.Thread(target=wait_for_signal, name="stop-signal", daemon=True).start()
    for signum in signals:
        signal.signal(signum, handle)
    return event
