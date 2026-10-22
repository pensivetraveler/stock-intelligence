"""Walk-forward runs (ADR 0032): parameters chosen in-sample, judged out-of-sample.

The run's bar times (all instruments together) are cut into rolling windows: an
in-sample stretch of `in_sample_bars` followed by an out-of-sample stretch of
`out_of_sample_bars`; the next window starts one out-of-sample stretch later. In every
window each combination of the grid is backtested on the in-sample stretch only, and the
best by the objective (ties: the first in grid order) trades the out-of-sample stretch.
The result is one engine pass over the out-of-sample stretches with the chosen
parameters switching at each window, so its equity, fills and metrics are all
out-of-sample. Indicators always see every earlier bar (causal), never later ones.
"""

from __future__ import annotations

import copy
import itertools
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from services.backtest.data import InstrumentInput
from services.backtest.engine import Backtest, EngineConfig, SignalSource, _utc
from services.backtest.strategy import StrategyError, validate_definition

OBJECTIVES = ("net_return", "return_over_drawdown", "profit_factor", "win_rate")
MAX_PARAMETERS = 3
MAX_VALUES = 20
MAX_BACKTESTS = 400  # windows x combinations
MAX_WINDOWS = 50


class WalkForwardError(ValueError):
    """The walk-forward settings are invalid for this run."""


def clean_spec(value: object) -> dict[str, Any]:
    """Validated walk-forward settings (shape only; paths are checked against the strategy)."""
    if not isinstance(value, dict):
        raise WalkForwardError("walk_forward must be an object")
    extra = set(value) - {"in_sample_bars", "out_of_sample_bars", "objective", "grid"}
    if extra:
        raise WalkForwardError(f"walk_forward has unexpected fields: {', '.join(sorted(extra))}")
    sizes = {}
    for name, low in (("in_sample_bars", 20), ("out_of_sample_bars", 5)):
        number = value.get(name)
        if isinstance(number, bool) or not isinstance(number, int) or not low <= number <= 5000:
            raise WalkForwardError(f"walk_forward {name} must be a whole number from {low} to 5000")
        sizes[name] = number
    objective = value.get("objective", "net_return")
    if objective not in OBJECTIVES:
        raise WalkForwardError(f"walk_forward objective must be one of {', '.join(OBJECTIVES)}")
    grid = value.get("grid")
    if not isinstance(grid, list) or not 1 <= len(grid) <= MAX_PARAMETERS:
        raise WalkForwardError(f"walk_forward grid must list 1 to {MAX_PARAMETERS} parameters")
    clean_grid = []
    for item in grid:
        if not isinstance(item, dict) or set(item) != {"path", "values"}:
            raise WalkForwardError("each grid entry needs exactly path and values")
        path, values = item["path"], item["values"]
        if not isinstance(path, str) or not path.startswith("/") or len(path) > 200:
            raise WalkForwardError(
                "a grid path is a JSON pointer such as /entry/left/params/length"
            )
        if (
            not isinstance(values, list)
            or not 1 <= len(values) <= MAX_VALUES
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in values)
            or any(not math.isfinite(v) for v in values)
            or len(set(values)) != len(values)
        ):
            raise WalkForwardError(f"grid values must be 1 to {MAX_VALUES} distinct numbers")
        clean_grid.append({"path": path, "values": list(values)})
    if len({item["path"] for item in clean_grid}) != len(clean_grid):
        raise WalkForwardError("each grid path may appear once")
    return {**sizes, "objective": objective, "grid": clean_grid}


def _set(definition: dict[str, Any], path: str, value: float) -> None:
    parts = [part.replace("~1", "/").replace("~0", "~") for part in path.split("/")[1:]]
    node: Any = definition
    for part in parts[:-1]:
        node = node[int(part)] if isinstance(node, list) else node[part]
    last = parts[-1]
    if isinstance(node, list):
        current = node[int(last)]
    elif isinstance(node, dict) and last in node:
        current = node[last]
    else:
        raise KeyError(path)
    if isinstance(current, bool) or not isinstance(current, (int, float, str)):
        raise KeyError(path)
    if isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value


def variants(
    definition: dict[str, Any], spec: dict[str, Any], custom: Any = None
) -> list[tuple[dict[str, float], dict[str, Any]]]:
    """Every grid combination as (assignment, validated definition), in grid order."""
    if definition["type"] != "rules":
        raise WalkForwardError("walk-forward runs take rules strategies for now")
    paths = [item["path"] for item in spec["grid"]]
    found = []
    for values in itertools.product(*(item["values"] for item in spec["grid"])):
        candidate = copy.deepcopy(definition)
        assignment = dict(zip(paths, values, strict=True))
        for path, value in assignment.items():
            try:
                _set(candidate, path, value)
            except (KeyError, IndexError, ValueError, TypeError) as exc:
                raise WalkForwardError(f"{path} is not a number in this strategy") from exc
        try:
            found.append((assignment, validate_definition(candidate, custom)))
        except StrategyError as exc:
            raise WalkForwardError(f"grid value {assignment} is invalid: {exc}") from exc
    return found


def windows(times: Sequence[int], spec: dict[str, Any]) -> list[tuple[int, int, int, int]]:
    """(in-sample start, out-of-sample start, out-of-sample end, last time) as bar times;
    the last window may be shorter. Ends are exclusive except the run's final bar time."""
    size_in, size_out = spec["in_sample_bars"], spec["out_of_sample_bars"]
    found = []
    start = 0
    while start + size_in < len(times):
        split = start + size_in
        stop = min(split + size_out, len(times))
        end = times[stop] if stop < len(times) else times[-1] + 1
        found.append((times[start], times[split], end, times[stop - 1]))
        start += size_out
        if len(found) > MAX_WINDOWS:
            raise WalkForwardError(
                f"more than {MAX_WINDOWS} windows: use larger out_of_sample_bars"
            )
    if not found:
        raise WalkForwardError("not enough bars for one in-sample and one out-of-sample stretch")
    return found


def score(metrics: dict[str, Any], objective: str) -> float:
    """Higher is better; a combination without trades or a defined value ranks last."""
    if metrics["trade_count"] == 0 and objective != "net_return":
        return -math.inf
    if objective == "return_over_drawdown":
        drawdown = float(metrics["max_drawdown"])
        return float(metrics["net_return"]) / max(drawdown, 0.0001)
    value = metrics.get(objective)
    if value is None:
        # profit_factor is null without losses: with winning trades that is the best case.
        return math.inf if objective == "profit_factor" and metrics["trade_count"] else -math.inf
    return float(value)


class ScheduledSignals:
    """Each window's chosen strategy gives the signals for bars inside its window."""

    def __init__(
        self,
        inputs: Sequence[InstrumentInput],
        schedule: list[tuple[int, int, SignalSource]],
    ) -> None:
        self._times = {
            item.instrument_id: [bar.time for bar in item.bars if bar.final] for item in inputs
        }
        self._schedule = schedule

    def signals(self, instrument_id: str, index: int, book: dict[str, Any]) -> list[dict[str, Any]]:
        time = self._times[instrument_id][index]
        for start, end, source in self._schedule:
            if start <= time < end:
                return source.signals(instrument_id, index, book)
        return []

    def close(self) -> None:
        for _start, _end, source in self._schedule:
            source.close()


@dataclass
class WalkForward:
    run_id: str
    definition: dict[str, Any]
    spec: dict[str, Any]
    inputs: Sequence[InstrumentInput]
    config: EngineConfig
    make_source: Callable[[dict[str, Any]], SignalSource]
    custom: Any = None
    cancelled: Callable[[], bool] = lambda: False
    progress: Callable[[float], None] = lambda _fraction: None

    def run(self) -> dict[str, Any]:
        combos = variants(self.definition, self.spec, self.custom)
        times = sorted({bar.time for item in self.inputs for bar in item.bars})
        cuts = windows(times, self.spec)
        if len(cuts) * len(combos) > MAX_BACKTESTS:
            raise WalkForwardError(
                f"{len(cuts)} windows x {len(combos)} combinations exceed {MAX_BACKTESTS} backtests"
            )
        # One signal source per combination: rules sources keep no state between runs,
        # so their indicator series are computed once and reused in every window.
        sources = [self.make_source(definition) for _assignment, definition in combos]
        chosen = []
        done, total = 0, len(cuts) * len(combos) + 1
        for number, (in_start, out_start, out_end, _last) in enumerate(cuts):
            best: tuple[float, int] | None = None
            for k, (_assignment, definition) in enumerate(combos):
                result = Backtest(
                    f"{self.run_id}-w{number + 1}c{k + 1}",
                    definition,
                    self.inputs,
                    self.config,
                    sources[k],
                    cancelled=self.cancelled,
                    trade_window=(in_start, out_start),
                ).run()
                value = score(result["metrics"], self.spec["objective"])
                if best is None or value > best[0]:
                    best = (value, k)
                done += 1
                self.progress(done / total)
            assert best is not None
            chosen.append((number, best, in_start, out_start, out_end))
        schedule = [
            (out_start, out_end, sources[k]) for _n, (_v, k), _i, out_start, out_end in chosen
        ]
        final = Backtest(
            self.run_id,
            self.definition,
            self.inputs,
            self.config,
            ScheduledSignals(self.inputs, schedule),
            cancelled=self.cancelled,
            trade_window=(cuts[0][1], cuts[-1][2]),
        ).run()
        final["walk_forward"] = {
            "objective": self.spec["objective"],
            "in_sample_bars": self.spec["in_sample_bars"],
            "out_of_sample_bars": self.spec["out_of_sample_bars"],
            "combinations": len(combos),
            "windows": [
                {
                    "in_sample_start_utc": _utc(in_start),
                    "out_of_sample_start_utc": _utc(out_start),
                    "out_of_sample_end_utc": _utc(cuts[number][3]),
                    "chosen": [
                        {"path": path, "value": _number(value)}
                        for path, value in combos[k][0].items()
                    ],
                    "in_sample_score": None if math.isinf(value) else _number(value),
                }
                for number, (value, k), in_start, out_start, _out_end in chosen
            ],
        }
        # Python indicator versions used by any combination, pinned like a plain run.
        pins = {
            pin["type"]: pin for source in sources for pin in getattr(source, "custom_pins", [])
        }
        if pins:
            final["custom_indicators"] = [pins[key] for key in sorted(pins)]
        self.progress(1.0)
        return final


def _number(value: float) -> str:
    return format(Decimal(str(value)).normalize(), "f")
