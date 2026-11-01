"""Rules-strategy signals for the paper runner, from the backtest's own evaluator.

The same `RuleSignals` that a backtest uses (ADR 0021) is shown only final bars, and is
asked about the latest one, so a paper signal is what the backtest would have queued at
that bar's close. Python strategies and Python indicator operands are not offered here
yet: they need the sandbox lifecycle of a resident runner.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from services.backtest.data import EngineBar, InstrumentInput
from services.backtest.engine import RuleSignals
from services.backtest.strategy import StrategyError, validate_definition


def rule_signals(
    definition: dict[str, Any],
    instrument_id: str,
    time_zone: str,
    bars: Sequence[EngineBar],
    holding: bool,
) -> list[dict[str, Any]]:
    """Order intents the rules give at the last final bar ([] when it is not final)."""
    clean = validate_definition(definition)
    if clean["type"] != "rules":
        raise StrategyError("paper runner: only rules strategies are offered for now")
    if any(
        clean.get(name) is not None
        for name in ("stop_loss_pct", "take_profit_pct", "stop_loss_atr", "trailing_stop")
    ):
        # The backtest checks these inside each bar; the runner has no such order yet.
        raise StrategyError("paper runner: stop-loss and take-profit are not offered yet")
    final = [bar for bar in bars if bar.final]
    if not final or not bars[-1].final:
        return []
    try:
        source = RuleSignals(clean, [InstrumentInput(instrument_id, time_zone, list(final))])
    except StrategyError as exc:
        raise StrategyError(f"paper runner: {exc}") from exc
    book = {"position": {"quantity": 1} if holding else None}
    return source.signals(instrument_id, len(final) - 1, book)
