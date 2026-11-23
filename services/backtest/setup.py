"""The base-bar Fibonacci setup's state (ADR 0061).

One state per instrument: idle -> base -> armed -> holding -> idle. Every move happens at a
final bar's close and reads only that bar and earlier ones. The lines run from the base
bar's low (1) to the second rise's high (0): the price at ratio r is high - r x (high - low).
The high is the highest high from the breakout bar to the latest final bar, and is frozen at
the first fill (that bar's own high may have come after the fill, so it is not used).

The engine (`services.backtest.engine`) places, fills and cancels the orders; this module
only keeps the state and the prices.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

PRICE = Decimal("0.000001")


@dataclass
class Leg:
    level: Decimal
    size_pct: Decimal
    targets: list[tuple[Decimal, Decimal]]  # (line, sell_pct), upward
    order: Any = None  # the resting engine Order, None when not placed or done
    filled: bool = False


@dataclass
class SetupState:
    phase: str = "idle"  # idle | base | armed | holding
    base: int = -1  # signal index of the base bar
    base_low: Decimal = Decimal(0)
    base_high: Decimal = Decimal(0)
    breakout: int = -1
    waited: int = 0  # bars since the base bar (base) or the breakout (armed)
    high: Decimal | None = None
    legs: list[Leg] = field(default_factory=list)
    deepest: int = -1  # the deepest filled leg, whose targets apply
    step: int = 0  # the next target in that leg's ladder
    stop: Decimal | None = None
    took_target: bool = False
    at_breakeven: bool = False
    raise_stop_next: bool = False  # breakeven applies from the bar after the target


def line(setup_state: SetupState, ratio: Decimal) -> Decimal:
    """The price of a line, from the current (or frozen) high and the base bar's low."""
    assert setup_state.high is not None
    high, low = setup_state.high, setup_state.base_low
    return (high - ratio * (high - low)).quantize(PRICE)


def new_legs(setup: dict[str, Any]) -> list[Leg]:
    return [
        Leg(
            Decimal(leg["level"]),
            Decimal(leg["size_pct"]),
            [(Decimal(t["level"]), Decimal(t["sell_pct"])) for t in leg["targets"]],
        )
        for leg in setup["legs"]
    ]
