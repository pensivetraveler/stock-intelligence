"""Turn SIGTERM/SIGINT into a `threading.Event` without taking a lock in a signal handler.

A handler that calls `Event.set()` takes the event's internal lock. Python runs handlers in the
main thread between bytecodes, so a second signal can arrive while the first handler (or the main
thread's own `Event.wait()`) holds that lock; the nested `set()` then waits on a lock its own thread
holds and the process hangs until SIGKILL. systemd sends SIGTERM to every process of the unit and
uv forwards its copy to python, so the runner sees two signals on every stop (2026-11-28 mini PC).

Here CPython's C-level handler writes the signal number to a pipe (`signal.set_wakeup_fd`), from
whichever thread the OS picked, and a daemon thread blocked on that pipe sets the event. The
Python-level handler does nothing. Any number of signals, at any moment, stop the wait at once —
also when the OS delivers the signal to a thread other than the main one, which would otherwise
leave the main thread asleep until its `wait()` timeout.

At exit the signals are set to SIG_IGN. CPython's finalization puts every signal that has a Python
handler back to SIG_DFL, so a late copy of the stop signal arriving then killed the process with
-15 after it had already stopped cleanly (CI 2026-11-28; a SIGTERM flood on Mac did it 100 of
100 times). SIG_IGN is left alone by finalization.
"""

from __future__ import annotations

import atexit
import os
import signal
import sys
import threading
from collections.abc import Iterable

DEFAULT_SIGNALS = (signal.SIGTERM, signal.SIGINT)


def _ignore(_signum: int, _frame: object) -> None:
    # Installed only so CPython's C handler (which writes the wakeup fd) stays in place.
    return None


def stop_on_signals(
    stop: threading.Event | None = None, signals: Iterable[int] = DEFAULT_SIGNALS
) -> threading.Event:
    """Set `stop` (a new event when omitted) on any of `signals`, and return the event.

    Call from the main thread, like `signal.signal`. It takes over the process's signal wakeup
    fd, so it does not mix with an asyncio loop's `add_signal_handler` in the same process.
    """
    event = stop if stop is not None else threading.Event()
    read_end, write_end = os.pipe()
    os.set_blocking(write_end, False)

    def wait_for_signals() -> None:
        # The write end stays open for the life of the process, so this read never sees EOF.
        while os.read(read_end, 512):
            event.set()

    threading.Thread(target=wait_for_signals, name="stop-signal", daemon=True).start()
    signal.set_wakeup_fd(write_end, warn_on_full_buffer=False)
    chosen = tuple(signals)
    for signum in chosen:
        signal.signal(signum, _ignore)
    atexit.register(_ignore_while_exiting, chosen)
    return event


def _ignore_while_exiting(signals: tuple[int, ...]) -> None:
    # atexit runs in the main thread, before finalization resets Python handlers to SIG_DFL.
    # A copy that arrived just before the switch is then reported by CPython as an unraisable
    # "Signal N ignored due to race condition"; that is the intended outcome, so keep it quiet.
    previous = sys.unraisablehook

    def quiet(unraisable: sys.UnraisableHookArgs) -> None:
        error = unraisable.exc_value
        if isinstance(error, OSError) and "ignored due to race condition" in str(error):
            return
        previous(unraisable)

    sys.unraisablehook = quiet
    try:
        for signum in signals:
            signal.signal(signum, signal.SIG_IGN)
        _flush_pending_signals()
    finally:
        # Not left in place: a Python hook called late in finalization can crash the interpreter.
        sys.unraisablehook = previous


def _flush_pending_signals() -> None:
    # A Python call is a point where CPython runs (here: reports) signals already caught.
    return None
