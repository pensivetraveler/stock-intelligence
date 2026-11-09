"""Check A (ADR 0052): a saved rules strategy's entry condition on confirmed daily bars.

The same evaluator as the backtests reads the bars up to the last regular session that
closed before the cutoff, as the market-data store had them at the cutoff. Only final
bars count. Each simple condition is reported with the values it compared, so the card
shows why a candidate passed or failed, not only that it did.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from services.backtest.data import GetBars, load_instrument
from services.backtest.protection import _indicator_bars
from services.backtest.strategy import RuleEvaluator, StrategyError, parse_custom
from services.market_data.instruments import calendar_for
from services.market_data.provider import InvalidBarsQueryError, MarketDataError
from services.screening.candidates import utc_text

CHECK_VERSION = "screening-entry-1"
# Calendar days of daily bars read before the last session: room for a 200-day average.
LOOKBACK_DAYS = 420
MAX_SESSIONS_BACK = 10


class EntryCheckError(ValueError):
    """The strategy cannot be checked on daily bars (reported as `unsupported`)."""


def _leaves(condition: dict[str, Any], path: str = "entry") -> list[tuple[str, dict[str, Any]]]:
    if "all" in condition or "any" in condition:
        key = "all" if "all" in condition else "any"
        found: list[tuple[str, dict[str, Any]]] = []
        for index, item in enumerate(condition[key]):
            found.extend(_leaves(item, f"{path}.{key}[{index}]"))
        return found
    return [(path, condition)]


def _operands(condition: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        operand
        for _path, leaf in _leaves(condition)
        for operand in (leaf.get("left"), leaf.get("right"))
        if isinstance(operand, dict)
    ]


def _supported(definition: dict[str, Any]) -> None:
    entry = definition.get("entry")
    if not isinstance(entry, dict):
        raise EntryCheckError("the strategy has no entry condition")
    for path, leaf in _leaves(entry):
        if "time" in leaf:
            raise EntryCheckError(f"{path} is a time-of-day condition, which daily bars lack")
    for operand in _operands(entry):
        indicator = operand.get("indicator")
        if isinstance(indicator, str) and parse_custom(indicator) is not None:
            raise EntryCheckError("Python indicators are not checked here yet")


def _number(value: float | None) -> str | None:
    if value is None:
        return None
    return format(Decimal(repr(value)).quantize(Decimal("0.000001")).normalize(), "f")


def last_closed_session(instrument_id: str, cutoff: datetime) -> Any:
    """The latest session whose regular session closed at or before the cutoff."""
    calendar = calendar_for(instrument_id)
    day = cutoff.astimezone(calendar.zone).date()
    for _ in range(MAX_SESSIONS_BACK):
        window = calendar.window_for_date(day)
        if window is not None and window.regular_close_utc <= cutoff:
            return window
        day -= timedelta(days=1)
    raise InvalidBarsQueryError("no closed session in the last ten days")


def entry_check(
    strategy: dict[str, Any],
    instrument_id: str,
    cutoff: datetime,
    get_bars: GetBars,
    get_adjusted: Callable[..., Any] | None,
) -> dict[str, Any]:
    """The `result`, `status`, `sources` and `warnings` of an entry check."""
    definition = strategy["definition"]
    base: dict[str, Any] = {
        "strategy": {
            "strategy_id": strategy["strategy_id"],
            "name": strategy["name"],
            "version": strategy["version"],
            "definition_sha256": strategy["definition_sha256"],
        },
        "versions": {"check": CHECK_VERSION},
        "warnings": [],
        "sources": {},
    }
    try:
        if strategy.get("strategy_type") != "rules":
            raise EntryCheckError("only rules strategies are checked here")
        _supported(definition)
    except EntryCheckError as exc:
        return {**base, "status": "unsupported", "result": {"reason": str(exc)}}

    session = last_closed_session(instrument_id, cutoff)
    start = session.session_date - timedelta(days=LOOKBACK_DAYS)

    def load(adjustment: str) -> Any:
        return load_instrument(
            get_bars,
            instrument_id,
            "1d",
            start,
            session.session_date,
            as_of_utc=cutoff,
            adjustment=adjustment,
            get_adjusted=get_adjusted,
        )

    adjustment = "split_adjusted"
    try:
        if get_adjusted is None:
            raise InvalidBarsQueryError("the data provider offers no adjusted daily bars")
        loaded = load(adjustment)
    except MarketDataError as unadjusted:
        # A provider without adjusted bars (a broker feed) still has prices as traded.
        adjustment = "raw"
        try:
            loaded = load(adjustment)
        except MarketDataError as exc:
            return {**base, "status": "error", "result": {"reason": str(exc)[:300]}}
        base["warnings"].append(
            f"split-adjusted daily bars are not available ({str(unadjusted)[:200]}): "
            "raw prices used"
        )
    base["warnings"].extend(loaded.warnings)
    base["sources"] = {"bars": loaded.pin}
    final = [bar for bar in loaded.bars if bar.final]
    if not final:
        return {**base, "status": "no_data", "result": {"reason": "no final daily bar"}}

    evaluator = RuleEvaluator(definition, _indicator_bars(final), loaded.time_zone)
    index = len(final) - 1
    try:
        passed = evaluator.holds(definition["entry"], index)
        conditions = [
            {
                "path": path,
                "op": leaf["op"],
                "left": leaf["left"],
                "right": leaf["right"],
                "left_value": _number(evaluator.value(leaf["left"], index)),
                "right_value": _number(evaluator.value(leaf["right"], index)),
                "holds": evaluator.holds(leaf, index),
            }
            for path, leaf in _leaves(definition["entry"])
        ]
    except StrategyError as exc:
        return {**base, "status": "unsupported", "result": {"reason": str(exc)[:300]}}
    last = final[-1]
    bar_date = datetime.fromtimestamp(last.time, calendar_for(instrument_id).zone).date()
    if bar_date != session.session_date:
        base["warnings"].append(
            f"the last daily bar is {bar_date}, not the last closed session {session.session_date}"
        )
    return {
        **base,
        "status": "passed" if passed else "failed",
        "result": {
            "passed": passed,
            "bar": {
                "session_date": bar_date.isoformat(),
                "close": format(last.close, "f"),
                "volume": format(last.volume, "f"),
                "interval_end_utc": utc_text(datetime.fromtimestamp(last.end, timezone.utc)),
            },
            "bars_used": len(final),
            "adjustment": adjustment,
            "conditions": conditions,
        },
    }
