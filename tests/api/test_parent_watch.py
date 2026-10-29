from __future__ import annotations

import subprocess
import sys
import threading
import time

from services.api.parent_watch import parent_pid_from_env, process_exists, start_parent_watch


def test_parent_pid_from_env() -> None:
    assert parent_pid_from_env({}) is None
    assert parent_pid_from_env({"SIW_PARENT_PID": ""}) is None
    assert parent_pid_from_env({"SIW_PARENT_PID": "abc"}) is None
    assert parent_pid_from_env({"SIW_PARENT_PID": "1"}) is None  # launchd/init, never "gone"
    assert parent_pid_from_env({"SIW_PARENT_PID": " 4242 "}) == 4242


def test_stops_once_the_parent_is_gone() -> None:
    alive = [True, True, False]
    stopped = threading.Event()
    done = start_parent_watch(
        4242, exists=lambda _pid: alive.pop(0), stop=stopped.set, interval=0.01
    )
    assert stopped.wait(2)
    assert done.is_set()


def test_ending_the_watch_never_stops() -> None:
    stopped = threading.Event()
    done = start_parent_watch(4242, exists=lambda _pid: True, stop=stopped.set, interval=0.01)
    done.set()
    time.sleep(0.05)
    assert not stopped.is_set()


def test_real_process_gets_sigterm_when_the_parent_is_killed() -> None:
    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    child_code = (
        "import signal, sys, time\n"
        "from services.api.parent_watch import start_parent_watch\n"
        "signal.signal(signal.SIGTERM, lambda *_: sys.exit(7))\n"
        f"start_parent_watch({parent.pid}, interval=0.05)\n"
        "print('ready', flush=True)\n"
        "time.sleep(30)\n"
    )
    child = subprocess.Popen([sys.executable, "-c", child_code], stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout is not None and child.stdout.readline().strip() == "ready"
        assert child.poll() is None
        parent.kill()
        parent.wait()
        assert not process_exists(parent.pid)
        assert child.wait(timeout=5) == 7
    finally:
        parent.kill()
        child.kill()
