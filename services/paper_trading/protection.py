"""A paper position's protective state (ADR 0044 G2), judged like its backtest.

The levels come from the fill, then each final bar is judged by the backtest's own rules
(`services.backtest.protection`). The state after each bar is what the store keeps, so a
restart goes on from the last bar judged. Nothing here sends an order: the runner does
that from the decision it is given (G3).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

from services.backtest.data import EngineBar
from services.backtest.protection import (
    AtrHistory,
    ExitHit,
    Levels,
    ProtectionRules,
    entry_levels,
    exit_on_bar,
    follow,
)


@dataclass(frozen=True)
class ProtectionState:
    intent_id: str  # the entry order this position came from
    instrument_id: str
    entry_price: Decimal
    entry_time: int  # the fill, epoch seconds
    entry_bar_time: int  # the start of the bar the fill is in
    levels: Levels
    last_bar_time: int | None = None  # the last bar judged

    def to_json(self) -> str:
        def text(value: Decimal | None) -> str | None:
            return None if value is None else format(value, "f")

        return json.dumps(
            {
                "intent_id": self.intent_id,
                "instrument_id": self.instrument_id,
                "entry_price": format(self.entry_price, "f"),
                "entry_time": self.entry_time,
                "entry_bar_time": self.entry_bar_time,
                "stop": text(self.levels.stop),
                "target": text(self.levels.target),
                "trail_stop": text(self.levels.trail_stop),
                "trail_high": text(self.levels.trail_high),
                "last_bar_time": self.last_bar_time,
            },
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, document: str) -> ProtectionState:
        data: dict[str, Any] = json.loads(document)

        def number(name: str) -> Decimal | None:
            return None if data[name] is None else Decimal(data[name])

        return cls(
            data["intent_id"],
            data["instrument_id"],
            Decimal(data["entry_price"]),
            int(data["entry_time"]),
            int(data["entry_bar_time"]),
            Levels(number("stop"), number("target"), number("trail_stop"), number("trail_high")),
            data["last_bar_time"],
        )


@dataclass(frozen=True)
class ProtectiveExit:
    """A level was reached: the position should be sold, for this reason."""

    hit: ExitHit
    bar_time: int
    same_bar_as_entry: bool  # the bar's low or high may have come before the entry


def open_state(
    rules: ProtectionRules,
    intent_id: str,
    instrument_id: str,
    fill_price: Decimal,
    fill_time: int,
    bars: Sequence[EngineBar],
    time_zone: str,
) -> ProtectionState | None:
    """The state for a confirmed fill; None while an ATR the rules need is not known."""
    started = [bar.time for bar in bars if bar.time <= fill_time]
    entry_bar_time = started[-1] if started else fill_time
    levels = entry_levels(rules, fill_price, entry_bar_time, AtrHistory(bars, time_zone))
    if levels is None:
        return None
    return ProtectionState(intent_id, instrument_id, fill_price, fill_time, entry_bar_time, levels)


def advance(
    rules: ProtectionRules, state: ProtectionState, bars: Sequence[EngineBar], time_zone: str
) -> tuple[ProtectionState, ProtectiveExit | None]:
    """Judge the final bars not judged yet, oldest first, up to the first exit."""
    atr = AtrHistory(bars, time_zone)
    last = state.last_bar_time
    for bar in bars:
        if not bar.final or bar.time < state.entry_bar_time:
            continue
        if last is not None and bar.time <= last:
            continue
        inside = bar.time == state.entry_bar_time and state.entry_time != bar.time
        hit = exit_on_bar(state.levels, bar)
        if hit is not None:
            return replace(state, last_bar_time=bar.time), ProtectiveExit(hit, bar.time, inside)
        levels = state.levels if inside else follow(rules, state.levels, bar, atr)
        state = replace(state, levels=levels, last_bar_time=bar.time)
    return state, None
