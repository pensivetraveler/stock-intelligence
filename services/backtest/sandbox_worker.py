"""Runs one user Python strategy in its own process (started by `sandbox.py`).

Usage: python -I sandbox_worker.py <repo-root> <cpu-seconds>

The parent sends one JSON message per line on stdin and reads one reply per line. Bars
arrive one at a time, so this process never holds a bar the engine has not reached.
Before the user's code is compiled this process lowers its resource limits and installs
an audit hook refusing network, subprocesses, native code and writes outside its working
directory. That guards against accidents; it is not a boundary against deliberately
hostile code (ADR 0021).
"""

from __future__ import annotations

import io
import json
import os
import resource
import sys
import traceback
from typing import Any

WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
PATH_EVENTS = {
    "os.remove": 1,
    "os.rename": 2,
    "os.rmdir": 1,
    "os.mkdir": 1,
    "os.chmod": 1,
    "os.chown": 1,
    "os.link": 2,
    "os.symlink": 2,
    "os.truncate": 1,
    "os.utime": 1,
    "shutil.rmtree": 1,
    "shutil.copyfile": 2,
    "shutil.move": 2,
}
BLOCKED_PREFIXES = (
    "socket.",
    "subprocess.",
    "os.exec",
    "os.spawn",
    "os.posix_spawn",
    "os.fork",
    "os.system",
    "os.kill",
    "os.killpg",
    "ctypes.",
    "_winapi.",
    "webbrowser.",
    "urllib.Request",
    "http.client.",
    "ftplib.",
    "smtplib.",
    "telnetlib.",
    "pty.",
)
BLOCKED_MODULES = {"ctypes", "_ctypes", "socket", "_socket", "subprocess", "_posixsubprocess"}
LOG_LIMIT = 4_000


class Refused(PermissionError):
    pass


def _inside(path: object, root: str) -> bool:
    if isinstance(path, int):
        return True
    try:
        resolved = os.path.realpath(os.fsdecode(path))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return resolved == root or resolved.startswith(root + os.sep)


def install_guard(root: str) -> None:
    def hook(event: str, args: tuple[Any, ...]) -> None:
        if event.startswith(BLOCKED_PREFIXES):
            raise Refused(f"strategies may not use {event}")
        if event == "import" and args and str(args[0]).split(".")[0] in BLOCKED_MODULES:
            raise Refused(f"strategies may not import {args[0]}")
        if event == "open" and len(args) >= 3:
            path, mode, flags = args[0], args[1], args[2]
            writes = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                isinstance(flags, int) and flags & WRITE_FLAGS
            )
            if writes and not _inside(path, root):
                raise Refused("strategies may only write inside their working directory")
        count = PATH_EVENTS.get(event)
        if count is not None and not all(_inside(arg, root) for arg in args[:count]):
            raise Refused(f"strategies may not use {event} outside their working directory")

    sys.addaudithook(hook)


def limit_resources(cpu_seconds: int) -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    resource.setrlimit(resource.RLIMIT_FSIZE, (10_000_000, 10_000_000))
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (min(64, soft), hard))


class Bar:
    __slots__ = ("time", "open", "high", "low", "close", "volume")

    def __init__(self, data: dict[str, Any]) -> None:
        self.time = int(data["time"])
        for name in ("open", "high", "low", "close", "volume"):
            setattr(self, name, float(data[name]))


class Context:
    """What `on_bar(ctx)` sees: the current bar, this instrument's past bars and the book."""

    def __init__(self, message: dict[str, Any], history: list[Any], params: dict[str, Any]):
        self.instrument_id: str = message["instrument_id"]
        self.time: int = int(message["bar"]["time"])
        self.bar = history[-1]
        self.history = history
        self.params = params
        self.position: dict[str, str] | None = message.get("position")
        self.cash: str = message["cash"]
        self.equity: str = message["equity"]
        self.open_positions: int = int(message.get("open_positions", 0))

    @property
    def closes(self) -> list[float]:
        return [bar.close for bar in self.history]

    def indicator(self, indicator_id: str, output: str | None = None, **params: float) -> Any:
        """The indicator's latest value over this instrument's bars so far (None in warm-up)."""
        from services.backtest.indicators import INDICATORS, compute

        spec = INDICATORS.get(indicator_id)
        if spec is None:
            raise ValueError(f"unknown indicator {indicator_id!r}")
        values = compute(indicator_id, self.history, params, _TIME_ZONE)
        return values[output or spec.outputs[0]][-1]

    def buy(self, **size: Any) -> dict[str, Any]:
        return {"side": "buy", "instrument_id": self.instrument_id, **size}

    def sell(self, **size: Any) -> dict[str, Any]:
        return {"side": "sell", "instrument_id": self.instrument_id, **size}


_TIME_ZONE = "America/New_York"


class HistoryView:
    """The bars up to the current one; anything later is out of range."""

    def __init__(self, bars: list[Any], end: int) -> None:
        self._bars = bars
        self._end = end  # index of the current bar

    def __len__(self) -> int:
        return self._end + 1

    def __iter__(self) -> Any:
        return iter(self._bars[: self._end + 1])

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, slice):
            return self._bars[: self._end + 1][key]
        index = key + self._end + 1 if key < 0 else key
        if not 0 <= index <= self._end:
            raise IndexError("only bars up to the current one can be read")
        return self._bars[index]


class IndicatorContext:
    """What `update(ctx)` sees for one bar of a custom indicator (ADR 0023)."""

    def __init__(
        self,
        bars: list[Any],
        index: int,
        params: dict[str, Any],
        state: dict[str, Any],
        series: dict[Any, Any],
    ) -> None:
        self.index = index
        self.bar = bars[index]
        self.history = HistoryView(bars, index)
        self.params = params
        self.state = state
        self._bars = bars
        self._series = series

    @property
    def closes(self) -> list[float]:
        return [bar.close for bar in self.history]

    def indicator(self, indicator_id: str, output: str | None = None, **params: float) -> Any:
        """A built-in indicator's value at this bar (None during its warm-up).

        Each series is computed once over the bars sent; the built-ins are causal, so the
        value at this bar depends only on this bar and earlier ones.
        """
        from services.backtest.indicators import INDICATORS, compute

        spec = INDICATORS.get(indicator_id)
        if spec is None:
            raise ValueError(f"unknown indicator {indicator_id!r}")
        key = (indicator_id, tuple(sorted(params.items())))
        if key not in self._series:
            self._series[key] = compute(indicator_id, self._bars, params, _TIME_ZONE)
        return self._series[key][output or spec.outputs[0]][self.index]


def run_indicator(namespace: dict[str, Any], message: dict[str, Any]) -> dict[str, Any]:
    bars = [Bar(item) for item in message["bars"]]
    outputs: list[str] = list(message["outputs"])
    params = message.get("params") or {}
    update = namespace["update"]
    values: dict[str, list[float | None]] = {name: [] for name in outputs}
    state: dict[str, Any] = {}
    series: dict[Any, Any] = {}
    for index in range(len(bars)):
        result = update(IndicatorContext(bars, index, params, state, series))
        if result is None:
            result = {}
        elif isinstance(result, (int, float)) and len(outputs) == 1:
            result = {outputs[0]: result}
        if not isinstance(result, dict):
            raise TypeError("update(ctx) must return a dict of output values or None")
        unknown = set(result) - set(outputs)
        if unknown:
            raise ValueError(f"update(ctx) returned undeclared outputs: {sorted(unknown)}")
        for name in outputs:
            value = result.get(name)
            if value is not None:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise TypeError(f"{name} must be a number or None at bar {index}")
                value = float(value)
                if value != value or value in (float("inf"), float("-inf")):
                    raise ValueError(f"{name} is not a finite number at bar {index}")
            values[name].append(value)
    return {"outputs": values}


def main() -> None:
    global _TIME_ZONE
    root, cpu_seconds = sys.argv[1], int(sys.argv[2])
    sys.path.insert(0, root)
    from services.backtest import indicators  # noqa: F401  (imported before the guard)

    reply = os.fdopen(os.dup(1), "w", buffering=1)
    requests = sys.stdin
    workdir = os.path.realpath(os.getcwd())
    limit_resources(cpu_seconds)
    namespace: dict[str, Any] = {"__name__": "strategy"}
    history: dict[str, list[Any]] = {}
    params: dict[str, Any] = {}
    install_guard(workdir)

    for line in requests:
        message = json.loads(line)
        captured = io.StringIO()
        sys.stdout = captured
        result: dict[str, Any]
        try:
            if message["type"] == "indicator_init":
                _TIME_ZONE = message.get("time_zone") or _TIME_ZONE
                exec(compile(message["code"], "<strategy>", "exec"), namespace)
                if not callable(namespace.get("update")):
                    raise ValueError("the indicator must define update(ctx)")
                meta = {
                    key: namespace.get(name)
                    for key, name in (
                        ("name", "NAME"),
                        ("inputs", "INPUTS"),
                        ("outputs", "OUTPUTS"),
                        ("pane", "PANE"),
                    )
                }
                result = {"ok": True, "meta": json.loads(json.dumps(meta, default=str))}
            elif message["type"] == "indicator_run":
                result = run_indicator(namespace, message)
            elif message["type"] == "init":
                params = message.get("params") or {}
                _TIME_ZONE = message.get("time_zone") or _TIME_ZONE
                exec(compile(message["code"], "<strategy>", "exec"), namespace)
                if not callable(namespace.get("on_bar")):
                    raise ValueError("the strategy must define on_bar(ctx)")
                result = {"ok": True}
            elif message["type"] == "history":
                # Earlier bars for warm-up (a run that starts later, ADR 0032): added to
                # ctx.history without calling on_bar, so no order can come from them.
                bars = history.setdefault(message["instrument_id"], [])
                bars.extend(Bar(item) for item in message["bars"])
                result = {"ok": True}
            else:
                bars = history.setdefault(message["instrument_id"], [])
                bars.append(Bar(message["bar"]))
                intents = namespace["on_bar"](Context(message, bars, params))
                if intents is None:
                    intents = []
                elif isinstance(intents, dict):
                    intents = [intents]
                result = {"intents": json.loads(json.dumps(list(intents), default=str))}
        except BaseException as exc:  # noqa: BLE001  (reported to the engine, never hidden)
            frames = [
                frame
                for frame in traceback.extract_tb(exc.__traceback__)
                if frame.filename == "<strategy>"
            ]
            where = f" (line {frames[-1].lineno})" if frames else ""
            result = {"error": f"{type(exc).__name__}: {exc}"[:500] + where}
        finally:
            sys.stdout = sys.__stdout__
        result["log"] = captured.getvalue()[:LOG_LIMIT]
        reply.write(json.dumps(result) + "\n")


if __name__ == "__main__":
    main()
