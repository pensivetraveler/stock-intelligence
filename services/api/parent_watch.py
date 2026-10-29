"""Stops the API when the desktop app that started it is gone.

The desktop apps stop the API they started when they quit normally. When an app is
killed (SIGKILL, crash) it gets no chance to, and uv and uvicorn keep answering on the
port. An app that starts the API passes its own PID in ``SIW_PARENT_PID``; this module
checks that PID and asks uvicorn to shut down (SIGTERM, the same graceful path as a
normal quit) once it no longer exists. An API started without the variable (a terminal,
the resident runner) is never stopped by this.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)

PARENT_PID_ENV = "SIW_PARENT_PID"
CHECK_SECONDS = 2.0


def parent_pid_from_env(environ: dict[str, str] | None = None) -> int | None:
    raw = (os.environ if environ is None else environ).get(PARENT_PID_ENV, "").strip()
    if not raw:
        return None
    try:
        pid = int(raw)
    except ValueError:
        logger.warning("%s is not a process ID: %r", PARENT_PID_ENV, raw)
        return None
    return pid if pid > 1 else None


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


def _stop_self() -> None:
    os.kill(os.getpid(), signal.SIGTERM)


def start_parent_watch(
    pid: int,
    *,
    exists: Callable[[int], bool] = process_exists,
    stop: Callable[[], None] = _stop_self,
    interval: float = CHECK_SECONDS,
) -> threading.Event:
    """Watches ``pid`` in a daemon thread. Setting the returned event ends the watch."""
    done = threading.Event()

    def watch() -> None:
        while not done.wait(interval):
            if not exists(pid):
                logger.warning("the app that started the API (PID %d) is gone; stopping", pid)
                done.set()
                stop()
                return

    threading.Thread(target=watch, name="siw-parent-watch", daemon=True).start()
    return done
