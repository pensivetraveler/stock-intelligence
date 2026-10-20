"""Starts and talks to the process that runs one user Python strategy.

The process gets an empty environment (so no keys or tokens), a fresh temporary working
directory that is deleted afterwards, a CPU-time limit and a per-message wall-clock
timeout; it is killed on any error, timeout or cancellation.
"""

from __future__ import annotations

import contextlib
import json
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

WORKER = Path(__file__).with_name("sandbox_worker.py")
ROOT = Path(__file__).resolve().parents[2]
MAX_REPLY_BYTES = 1_000_000


class SandboxError(RuntimeError):
    """The strategy failed, timed out or broke the protocol."""


class StrategyProcess:
    def __init__(
        self,
        code: str,
        params: dict[str, Any],
        time_zone: str,
        cpu_seconds: int = 120,
        call_timeout: float = 10.0,
    ) -> None:
        self._workdir = tempfile.mkdtemp(prefix="siw-strategy-")
        self._timeout = call_timeout
        self.logs: list[str] = []
        self._closed = False
        self._process = subprocess.Popen(
            [sys.executable, "-I", str(WORKER), str(ROOT), str(cpu_seconds)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=self._workdir,
            env={},
            text=True,
            bufsize=1,
        )
        self._selector = selectors.DefaultSelector()
        assert self._process.stdout is not None
        self._selector.register(self._process.stdout, selectors.EVENT_READ)
        self.call({"type": "init", "code": code, "params": params, "time_zone": time_zone})

    def call(self, message: dict[str, Any]) -> dict[str, Any]:
        process = self._process
        assert process.stdin is not None and process.stdout is not None
        try:
            process.stdin.write(json.dumps(message) + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self.close()
            raise SandboxError("the strategy process stopped") from exc
        deadline = time.monotonic() + self._timeout
        if not self._selector.select(timeout=max(0.0, deadline - time.monotonic())):
            self.close()
            raise SandboxError(f"the strategy did not answer within {self._timeout:g} s")
        line = process.stdout.readline(MAX_REPLY_BYTES)
        if not line:
            code = process.poll()
            self.close()
            reason = "it exceeded its CPU time" if code in (-24, -9) else f"exit code {code}"
            raise SandboxError(f"the strategy process stopped ({reason})")
        try:
            reply = json.loads(line)
        except ValueError as exc:
            self.close()
            raise SandboxError("the strategy wrote something that is not a reply") from exc
        if not isinstance(reply, dict):
            self.close()
            raise SandboxError("the strategy wrote something that is not a reply")
        if reply.get("log"):
            self.logs.append(str(reply["log"]))
            del self.logs[:-200]
        if "error" in reply:
            self.close()
            raise SandboxError(str(reply["error"]))
        return reply

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._process.poll() is None:
            self._process.kill()
        self._process.wait()
        for stream in (self._process.stdin, self._process.stdout):
            if stream is not None:
                with contextlib.suppress(OSError):
                    stream.close()
        self._selector.close()
        shutil.rmtree(self._workdir, ignore_errors=True)

    def __enter__(self) -> StrategyProcess:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
