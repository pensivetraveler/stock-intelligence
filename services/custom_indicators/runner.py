"""Checks and computes user Python indicators in the strategy sandbox (ADR 0023)."""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from typing import Any

from services.backtest.sandbox import SandboxError, StrategyProcess

NAME = re.compile(r"^[a-z_][a-z0-9_]{0,30}$")
MAX_INPUTS = 8
MAX_OUTPUTS = 6
MAX_BARS = 5_000
MAX_CODE_BYTES = 100_000
LIMIT = 1e9  # settings
BAR_LIMIT = 1e15  # prices and volumes (KRW prices, share volumes)
RUN_TIMEOUT = 30.0
SAMPLE_BARS = [
    {
        "time": 1_700_000_000 + i * 86_400,
        "open": 10 + i,
        "high": 11 + i,
        "low": 9 + i,
        "close": 10.5 + i,
        "volume": 1000,
    }
    for i in range(3)
]

Process = Callable[..., StrategyProcess]


class IndicatorError(ValueError):
    """The indicator's code, settings or bars are invalid, or the code failed."""


def _number(value: object, what: str, limit: float = LIMIT) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IndicatorError(f"{what} must be a number")
    number = float(value)
    if not math.isfinite(number) or abs(number) > limit:
        raise IndicatorError(f"{what} must be a finite number up to {limit:g}")
    return number


def validate_meta(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise IndicatorError("the indicator did not describe itself")
    name = raw.get("name")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
        raise IndicatorError("NAME must be text of 1 to 60 characters")
    inputs = raw.get("inputs") or {}
    if not isinstance(inputs, dict) or len(inputs) > MAX_INPUTS:
        raise IndicatorError(f"INPUTS must be a dict of at most {MAX_INPUTS} numbers")
    clean_inputs = []
    for key, value in inputs.items():
        if not isinstance(key, str) or not NAME.fullmatch(key):
            raise IndicatorError(f"input name {key!r} must be lowercase letters, digits or _")
        clean_inputs.append({"key": key, "default": _number(value, f"INPUTS[{key!r}]")})
    outputs = raw.get("outputs")
    if (
        not isinstance(outputs, list)
        or not 1 <= len(outputs) <= MAX_OUTPUTS
        or len(set(map(str, outputs))) != len(outputs)
        or not all(isinstance(item, str) and NAME.fullmatch(item) for item in outputs)
    ):
        raise IndicatorError(f"OUTPUTS must list 1 to {MAX_OUTPUTS} distinct lowercase names")
    pane = raw.get("pane") or "separate"
    if pane not in ("price", "separate"):
        raise IndicatorError('PANE must be "price" or "separate"')
    return {"name": name.strip(), "inputs": clean_inputs, "outputs": list(outputs), "pane": pane}


def _start(code: str, time_zone: str, process: Process) -> StrategyProcess:
    if not isinstance(code, str) or not code.strip():
        raise IndicatorError("the indicator needs code")
    if len(code.encode()) > MAX_CODE_BYTES:
        raise IndicatorError(f"code must be at most {MAX_CODE_BYTES} bytes")
    try:
        compile(code, "<indicator>", "exec")
    except SyntaxError as exc:
        raise IndicatorError(f"code does not compile: line {exc.lineno}: {exc.msg}") from exc
    try:
        return process(
            "",
            {},
            time_zone,
            cpu_seconds=60,
            call_timeout=RUN_TIMEOUT,
            init={"type": "indicator_init", "code": code, "time_zone": time_zone},
        )
    except SandboxError as exc:
        raise IndicatorError(str(exc)) from exc


def _run(
    worker: StrategyProcess,
    meta: dict[str, Any],
    params: dict[str, float],
    bars: list[dict[str, Any]],
) -> dict[str, list[float | None]]:
    try:
        reply = worker.call(
            {"type": "indicator_run", "outputs": meta["outputs"], "params": params, "bars": bars}
        )
    except SandboxError as exc:
        raise IndicatorError(str(exc)) from exc
    outputs = reply.get("outputs")
    if not isinstance(outputs, dict) or set(outputs) != set(meta["outputs"]):
        raise IndicatorError("the indicator returned the wrong outputs")
    for name, values in outputs.items():
        if not isinstance(values, list) or len(values) != len(bars):
            raise IndicatorError(f"{name} does not have one value per bar")
    return outputs


def inspect(code: str, process: Process = StrategyProcess) -> dict[str, Any]:
    """The indicator's description, after one trial run on a few synthetic bars."""
    worker = _start(code, "America/New_York", process)
    try:
        meta = validate_meta(worker.init_reply.get("meta"))
        defaults = {item["key"]: item["default"] for item in meta["inputs"]}
        _run(worker, meta, defaults, SAMPLE_BARS)
        return meta
    finally:
        worker.close()


def clean_params(meta: dict[str, Any], params: object) -> dict[str, float]:
    given = params if isinstance(params, dict) else {}
    known = {item["key"]: item["default"] for item in meta["inputs"]}
    unknown = set(given) - set(known)
    if unknown:
        raise IndicatorError(f"unknown settings: {', '.join(sorted(map(str, unknown)))}")
    return {key: _number(given.get(key, default), key) for key, default in known.items()}


def clean_bars(bars: object) -> list[dict[str, float]]:
    if not isinstance(bars, list) or not 1 <= len(bars) <= MAX_BARS:
        raise IndicatorError(f"send 1 to {MAX_BARS} bars")
    clean: list[dict[str, float]] = []
    previous = None
    for bar in bars:
        if not isinstance(bar, dict):
            raise IndicatorError("each bar must be an object")
        item = {
            key: _number(bar.get(key), f"bar {key}", BAR_LIMIT)
            for key in ("open", "high", "low", "close", "volume")
        }
        time = bar.get("time")
        if isinstance(time, bool) or not isinstance(time, int) or not 0 < time < 2**32:
            raise IndicatorError("bar time must be whole epoch seconds")
        item["time"] = time
        if previous is not None and item["time"] <= previous:
            raise IndicatorError("bars must be in time order without repeats")
        previous = item["time"]
        clean.append(item)
    return clean


def compute(
    code: str,
    meta: dict[str, Any],
    params: object,
    bars: object,
    time_zone: str,
    process: Process = StrategyProcess,
) -> dict[str, list[float | None]]:
    settings = clean_params(meta, params)
    rows = clean_bars(bars)
    worker = _start(code, time_zone, process)
    try:
        return _run(worker, meta, settings, rows)
    finally:
        worker.close()
