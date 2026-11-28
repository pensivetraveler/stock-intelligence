"""A stop signal sent several times must still end the process (2026-11-28 mini PC hang).

systemd stops a unit by sending SIGTERM to uv and python, and uv forwards its copy, so python sees
the signal twice. The old `lambda *_: stop.set()` handler could re-enter `Event.set()` and wait on a
lock its own thread held; the reproduction hung about once in 100 stops with three signals.
A first fix that ran the handler in Python still slept until the `wait()` timeout whenever the OS
handed the signal to a thread other than the main one (2 of 300 here), so the burst test below
also catches that.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from services.stop_signal import stop_on_signals

REPO = Path(__file__).resolve().parents[1]

# The same loop shape as the paper/live runners: wait on the event with a long timeout.
RUNNER_LOOP = textwrap.dedent(
    """
    from services.stop_signal import stop_on_signals

    stop = stop_on_signals()
    print("ready", flush=True)
    while not stop.is_set():
        stop.wait(10)
    print("stopped", flush=True)
    """
)


@pytest.fixture
def restore_handlers() -> Iterator[None]:
    saved = {signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGINT)}
    wakeup = signal.set_wakeup_fd(-1)
    signal.set_wakeup_fd(wakeup)
    yield
    for signum, handler in saved.items():
        signal.signal(signum, handler)
    signal.set_wakeup_fd(wakeup)


def _start(arguments: list[str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, *arguments],
        cwd=REPO,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _signal_burst(process: subprocess.Popen[str], count: int) -> tuple[str, str]:
    try:
        for _ in range(count):
            os.kill(process.pid, signal.SIGTERM)
        return process.communicate(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_repeated_sigterm_always_stops_the_runner_loop() -> None:
    hung = 0
    for _ in range(40):
        process = _start(["-c", RUNNER_LOOP])
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "ready"
        try:
            stdout, stderr = _signal_burst(process, 5)
        except subprocess.TimeoutExpired:
            hung += 1
            continue
        assert process.returncode == 0, stderr
        assert stdout.strip() == "stopped"
        assert stderr == ""
    assert hung == 0


def test_signal_sets_the_event_while_its_lock_is_held(restore_handlers: None) -> None:
    stop = threading.Event()
    assert stop_on_signals(stop) is stop
    # Signals delivered while the main thread holds the event's own lock: the old handler
    # (`stop.set()`) waited on that lock forever right here.
    with stop._cond:  # type: ignore[attr-defined]
        for _ in range(3):
            os.kill(os.getpid(), signal.SIGTERM)
    assert stop.wait(2)
    os.kill(os.getpid(), signal.SIGINT)  # later signals stay harmless


def test_collector_run_stops_cleanly_on_repeated_sigterm() -> None:
    # `-u` as in the systemd unit, so "running" reaches the pipe before the signals.
    process = _start(["-u", "-m", "services.collector", "run"])
    assert process.stdout is not None
    assert json.loads(process.stdout.readline())["status"] == "running"
    stdout, stderr = _signal_burst(process, 3)
    assert process.returncode == 0
    assert stderr == ""
    assert [json.loads(line)["status"] for line in stdout.splitlines()] == ["stopped"]
