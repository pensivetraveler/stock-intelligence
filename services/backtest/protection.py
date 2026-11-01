"""Protective exits: stop-loss, take-profit and trailing stop (ADR 0021, ADR 0044).

One set of rules for the backtest engine and the paper runner, so that a paper position is
judged the way its backtest was. Everything here sees one bar at a time and only what was
known before it.
"""

from __future__ import annotations

import bisect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

from services.backtest.data import EngineBar
from services.backtest.indicators import Bar, Series, compute

HUNDRED = Decimal(100)


def _indicator_bars(bars: Sequence[EngineBar]) -> list[Bar]:
    return [
        Bar(b.time, float(b.open), float(b.high), float(b.low), float(b.close), float(b.volume))
        for b in bars
    ]


@dataclass(frozen=True)
class ProtectionRules:
    """A strategy's protective settings, from its validated definition."""

    stop_pct: Decimal | None = None
    target_pct: Decimal | None = None
    stop_atr: Mapping[str, Any] | None = None
    trailing: Mapping[str, Any] | None = None

    @property
    def active(self) -> bool:
        return any(
            value is not None
            for value in (self.stop_pct, self.target_pct, self.stop_atr, self.trailing)
        )

    @classmethod
    def of(cls, definition: Mapping[str, Any]) -> ProtectionRules:
        stop, target = definition.get("stop_loss_pct"), definition.get("take_profit_pct")
        return cls(
            None if stop is None else Decimal(stop) / HUNDRED,
            None if target is None else Decimal(target) / HUNDRED,
            definition.get("stop_loss_atr"),
            definition.get("trailing_stop"),
        )


class AtrHistory:
    """ATR values of one instrument's final bars, read by time so a later bar never shows."""

    def __init__(self, bars: Sequence[EngineBar], time_zone: str) -> None:
        self._final = [bar for bar in bars if bar.final]
        self._times = [bar.time for bar in self._final]
        self._time_zone = time_zone
        self._series: dict[int, Series] = {}

    def before(self, length: int, time: int) -> Decimal | None:
        """ATR of the last final bar that started before `time`."""
        if length not in self._series:
            self._series[length] = compute(
                "atr", _indicator_bars(self._final), {"length": length}, self._time_zone
            )["value"]
        index = bisect.bisect_left(self._times, time) - 1
        value = self._series[length][index] if index >= 0 else None
        return None if value is None else Decimal(str(value))


@dataclass(frozen=True)
class Levels:
    stop: Decimal | None = None
    target: Decimal | None = None
    # The trailing stop's level and the highest price it has followed so far.
    trail_stop: Decimal | None = None
    trail_high: Decimal | None = None


@dataclass(frozen=True)
class ExitHit:
    """A level reached in a bar. `reference` is the price before costs."""

    reference: Decimal
    reason: str  # "stop_loss", "trailing_stop" or "take_profit"
    ambiguous: bool = False  # stop and target in one bar: the order is unknown


def _trail_level(
    rules: ProtectionRules, high: Decimal, time: int, atr: AtrHistory
) -> Decimal | None:
    """Where the trailing stop sits under `high`, knowing only bars before `time`."""
    assert rules.trailing is not None
    if rules.trailing["mode"] == "pct":
        return high * (1 - Decimal(rules.trailing["value"]) / HUNDRED)
    value = atr.before(rules.trailing["length"], time)
    return None if value is None else high - Decimal(rules.trailing["mult"]) * value


def entry_levels(
    rules: ProtectionRules, price: Decimal, time: int, atr: AtrHistory
) -> Levels | None:
    """The levels for a fill at `price` in the bar at `time`; None while an ATR is not known."""
    stop = None if rules.stop_pct is None else price * (1 - rules.stop_pct)
    if rules.stop_atr is not None:
        value = atr.before(rules.stop_atr["length"], time)
        if value is None:
            return None
        stop = price - Decimal(rules.stop_atr["mult"]) * value
    target = None if rules.target_pct is None else price * (1 + rules.target_pct)
    if rules.trailing is None:
        return Levels(stop, target)
    trail = _trail_level(rules, price, time, atr)
    return None if trail is None else Levels(stop, target, trail, price)


def exit_on_bar(levels: Levels, bar: EngineBar) -> ExitHit | None:
    """The exit this bar reaches, if any. The stop wins when both could have come first."""
    active = [level for level in (levels.stop, levels.trail_stop) if level is not None]
    stop = max(active) if active else None
    stop_hit = stop is not None and bar.low <= stop
    target_hit = levels.target is not None and bar.high >= levels.target
    if stop_hit:
        assert stop is not None
        trailing = levels.trail_stop == stop and levels.stop != stop
        # A gap through the stop fills at the open, not at the stop.
        return ExitHit(
            min(bar.open, stop), "trailing_stop" if trailing else "stop_loss", target_hit
        )
    if target_hit:
        assert levels.target is not None
        return ExitHit(max(bar.open, levels.target), "take_profit")
    return None


def follow(rules: ProtectionRules, levels: Levels, bar: EngineBar, atr: AtrHistory) -> Levels:
    """Raise the trailing stop after a bar: its high can only move the stop for later bars,
    because a bar's low may have come first. The stop never moves down."""
    if levels.trail_high is None or levels.trail_stop is None or not bar.final:
        return levels
    high = max(levels.trail_high, bar.high)
    level = _trail_level(rules, high, bar.time + 1, atr)
    stop = levels.trail_stop if level is None else max(levels.trail_stop, level)
    return replace(levels, trail_stop=stop, trail_high=high)
