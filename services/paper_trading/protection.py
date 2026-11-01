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

    def document(self) -> dict[str, Any]:
        def text(value: Decimal | None) -> str | None:
            return None if value is None else format(value, "f")

        return {
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
        }

    def to_json(self) -> str:
        return json.dumps(self.document(), separators=(",", ":"), sort_keys=True)

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
    started = [bar for bar in bars if bar.time <= fill_time]
    entry_bar_time = fill_time
    if started:
        # A bar that has not arrived yet, or a minute without trades, holds the fill: the
        # bars already known all came before it and must not be judged.
        last = started[-1]
        length = last.end - last.time
        entry_bar_time = (
            last.time if fill_time < last.end else fill_time - (fill_time - last.end) % length
        )
    levels = entry_levels(rules, fill_price, entry_bar_time, AtrHistory(bars, time_zone))
    if levels is None:
        return None
    return ProtectionState(intent_id, instrument_id, fill_price, fill_time, entry_bar_time, levels)


def advance(
    rules: ProtectionRules,
    state: ProtectionState,
    bars: Sequence[EngineBar],
    time_zone: str,
    outside: Sequence[EngineBar] = (),
) -> tuple[ProtectionState, ProtectiveExit | None]:
    """Judge the final bars not judged yet, oldest first, up to the first exit.

    `outside` holds bars outside the regular session (ADR 0044, decision 3). They can reach
    a level but move nothing: the backtest never saw such bars, so its ATR and its trailing
    high come from regular bars alone.
    """
    atr = AtrHistory(bars, time_zone)
    last = state.last_bar_time
    regular = {bar.time for bar in bars}
    timeline = sorted((*bars, *(b for b in outside if b.time not in regular)), key=lambda b: b.time)
    for bar in timeline:
        if not bar.final or bar.time < state.entry_bar_time:
            continue
        if last is not None and bar.time <= last:
            continue
        inside = bar.time == state.entry_bar_time and state.entry_time != bar.time
        hit = exit_on_bar(state.levels, bar)
        if hit is not None:
            return replace(state, last_bar_time=bar.time), ProtectiveExit(hit, bar.time, inside)
        moves = bar.time in regular and not inside
        levels = follow(rules, state.levels, bar, atr) if moves else state.levels
        state = replace(state, levels=levels, last_bar_time=bar.time)
    return state, None
