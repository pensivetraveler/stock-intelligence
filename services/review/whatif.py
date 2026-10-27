"""What-ifs for one trade (ADR 0042, step 3b). Both are hindsight by nature.

- **Principles:** the same buys, but sold where the price first touched the principles'
  stop or target while held. When one bar touches both, its 1-minute bars decide; when a
  1-minute bar touches both too, the stop is assumed first and the result says so.
- **Strategy:** a saved rule strategy trades the same window instead: bought at the open
  after its first entry signal, sold at the open after its exit signal. The window is the
  trade's session(s) on 5-minute bars, or for longer trades 10 sessions before the entry
  through the exit on daily bars.

Quantities are the trade's own bought quantity, so profit is comparable in currency.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from services.backtest.data import GetBars
from services.backtest.indicators import Bar
from services.backtest.strategy import RuleEvaluator, StrategyError, validate_definition
from services.market_data.instruments import calendar_for
from services.market_data.provider import MarketDataError
from services.review.principles import _held_path, _Trade
from services.review.report import (
    INTRADAY,
    _bars,
    _first_entry,
    _intraday,
    _last_exit,
    _parse,
    _sessions_before,
    _utc,
    _window,
    executions_of,
)
from services.review.trades import Trade

WINDOW_SESSIONS_BEFORE = 10
WARMUP_SESSIONS = {"5m": 2, "1d": 300}


def _text(value: Decimal) -> str:
    return "0" if value == 0 else format(value.quantize(Decimal("0.0001")).normalize(), "f")


def _info(trade: Trade) -> _Trade:
    executions = executions_of(trade)
    buys = [e for e in executions if e.side == "buy"]
    sells = [e for e in executions if e.side == "sell"]
    bought = sum((e.quantity for e in buys), Decimal(0))
    sold = sum((e.quantity for e in sells), Decimal(0))
    return _Trade(
        trade,
        executions,
        sum((e.quantity * e.price for e in buys), Decimal(0)) / bought if bought else None,
        sum((e.quantity * e.price for e in sells), Decimal(0)) / sold if sold else None,
    )


def _actual(info: _Trade) -> tuple[Decimal, Decimal] | None:
    """(bought quantity, gross P&L of what was sold) or None without a buy."""
    buys = [e for e in info.executions if e.side == "buy"]
    if not buys or info.avg_buy is None:
        return None
    bought = sum((e.quantity for e in buys), Decimal(0))
    sold = sum((e.quantity for e in info.executions if e.side == "sell"), Decimal(0))
    gross = (info.avg_sell - info.avg_buy) * sold if info.avg_sell is not None else Decimal(0)
    return bought, gross


def principles_what_if(
    get_bars: GetBars,
    trade: Trade,
    items: Sequence[Mapping[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    applicable = [i for i in items if i["scope"] in ("shared", trade.strategy_id)]
    stops = [Decimal(i["value"]) for i in applicable if i["kind"] == "stop_loss_pct"]
    targets = [Decimal(i["value"]) for i in applicable if i["kind"] == "take_profit_pct"]
    base: dict[str, Any] = {"type": "principles", "hindsight": True, "notes": []}
    if not stops and not targets:
        return {**base, "status": "not_applicable", "detail": "no stop or target principle"}
    info = _info(trade)
    actual = _actual(info)
    if actual is None or info.avg_buy is None:
        return {**base, "status": "unknown", "detail": "no buy in this trade"}
    bought, actual_gross = actual
    # The tightest of each applies when several are written.
    stop = info.avg_buy * (1 - min(stops) / 100) if stops else None
    target = info.avg_buy * (1 + min(targets) / 100) if targets else None
    bars, timeframe, caveats = _held_path(get_bars, info, now)
    notes = [
        "all shares are taken at the average buy from the first buy",
        *caveats,
    ]
    for bar in bars:
        low, high = Decimal(str(bar["low"])), Decimal(str(bar["high"]))
        hit_stop = stop is not None and low <= stop
        hit_target = target is not None and high >= target
        if not hit_stop and not hit_target:
            continue
        reason = "stop" if hit_stop else "target"
        at: Mapping[str, Any] = bar
        if hit_stop and hit_target:
            reason, at, assumed = _finer(get_bars, trade.instrument_id, bar, stop, target)
            if assumed:
                notes.append("one 1-minute bar touched both; the stop is assumed first")
        level = stop if reason == "stop" else target
        assert level is not None
        open_ = Decimal(str(at["open"]))
        # A gap through the level fills at the open, not at the level.
        price = min(open_, level) if reason == "stop" else max(open_, level)
        gross = (price - info.avg_buy) * bought
        return {
            **base,
            "status": "computed",
            "reason": reason,
            "exit_price": _text(price),
            "exit_at_utc": at["interval_start_utc"],
            "timeframe": timeframe,
            "quantity": _text(bought),
            "gross_pnl": _text(gross),
            "actual_gross_pnl": _text(actual_gross),
            "difference": _text(gross - actual_gross),
            "notes": notes,
        }
    return {
        **base,
        "status": "computed",
        "reason": "none",
        "detail": "neither the stop nor the target was touched while held; the exit is unchanged",
        "timeframe": timeframe,
        "quantity": _text(bought),
        "gross_pnl": _text(actual_gross),
        "actual_gross_pnl": _text(actual_gross),
        "difference": "0",
        "notes": notes,
    }


def _finer(
    get_bars: GetBars,
    instrument_id: str,
    bar: Mapping[str, Any],
    stop: Decimal | None,
    target: Decimal | None,
) -> tuple[str, Mapping[str, Any], bool]:
    """Which level a bar that touched both reached first, by its 1-minute bars."""
    start, end = _parse(str(bar["interval_start_utc"])), _parse(str(bar["interval_end_utc"]))
    try:
        minutes = _bars(get_bars, instrument_id, "1m", start, end)
    except MarketDataError:
        minutes = []
    for minute in minutes:
        low, high = Decimal(str(minute["low"])), Decimal(str(minute["high"]))
        hit_stop = stop is not None and low <= stop
        hit_target = target is not None and high >= target
        if hit_stop and hit_target:
            return "stop", minute, True
        if hit_stop:
            return "stop", minute, False
        if hit_target:
            return "target", minute, False
    return "stop", bar, True


def _objects(bars: Sequence[Mapping[str, Any]]) -> list[Bar]:
    return [
        Bar(
            int(_parse(str(b["interval_start_utc"])).timestamp()),
            float(b["open"]),
            float(b["high"]),
            float(b["low"]),
            float(b["close"]),
            float(b["volume"]),
        )
        for b in bars
    ]


def strategy_what_if(
    get_bars: GetBars,
    trade: Trade,
    strategy: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "type": "strategy",
        "hindsight": True,
        "strategy_id": strategy["strategy_id"],
        "strategy_name": strategy["name"],
        "notes": [],
    }
    if strategy.get("strategy_type") != "rules":
        return {**base, "status": "unknown", "detail": "only rule strategies are replayed"}
    try:
        definition = validate_definition(strategy["definition"])
    except StrategyError as exc:
        return {**base, "status": "unknown", "detail": f"cannot be replayed here: {exc}"}
    info = _info(trade)
    actual = _actual(info)
    entry = _first_entry(info.executions)
    if actual is None or entry is None:
        return {**base, "status": "unknown", "detail": "no buy in this trade"}
    bought, actual_gross = actual
    instrument = trade.instrument_id
    exit_ = _last_exit(info.executions)
    last_day = exit_.day if exit_ is not None and trade.open_quantity == 0 else now.date()
    intraday = _intraday(info.executions)
    timeframe = INTRADAY if intraday else "1d"
    # The window where signals count, and the earlier bars indicators warm up on.
    if intraday:
        days = sorted({e.day for e in info.executions})
        window_sessions = [w for w in (_window(instrument, d) for d in days) if w]
        warmup = _sessions_before(instrument, days[0], WARMUP_SESSIONS[timeframe])
        bars: list[dict[str, Any]] = []
        for session in [*warmup, *window_sessions]:
            bars += _bars(
                get_bars, instrument, timeframe, session.pre_open_utc, session.post_close_utc
            )
        window_start = window_sessions[0].pre_open_utc if window_sessions else None
    else:
        before = _sessions_before(instrument, entry.day, WINDOW_SESSIONS_BEFORE)
        warmup = _sessions_before(
            instrument, entry.day, WINDOW_SESSIONS_BEFORE + WARMUP_SESSIONS[timeframe]
        )
        last = _window(instrument, last_day) or _sessions_before(instrument, last_day, 1)[-1]
        bars = _bars(get_bars, instrument, timeframe, warmup[0].pre_open_utc, last.post_close_utc)
        window_start = before[0].pre_open_utc if before else None
    bars = [b for b in bars if _parse(str(b["interval_end_utc"])) <= now]
    if not bars or window_start is None:
        return {**base, "status": "unknown", "detail": "no bars for the window"}
    evaluator = RuleEvaluator(definition, _objects(bars), calendar_for(instrument).zone.key)
    first = next(
        (i for i, b in enumerate(bars) if _parse(str(b["interval_start_utc"])) >= window_start),
        len(bars),
    )
    window_info = {
        "timeframe": timeframe,
        "window_start_utc": _utc(window_start),
        "window_end_utc": bars[-1]["interval_end_utc"],
    }
    try:
        signal = next(
            (i for i in range(first, len(bars) - 1) if evaluator.holds(definition["entry"], i)),
            None,
        )
        if signal is None:
            return {
                **base,
                **window_info,
                "status": "no_signal",
                "detail": "the strategy's entry rule never held in the window",
                "actual_gross_pnl": _text(actual_gross),
            }
        buy = bars[signal + 1]
        exit_rule = definition.get("exit")
        sell_index = None
        if exit_rule is not None:
            sell_index = next(
                (i + 1 for i in range(signal + 1, len(bars) - 1) if evaluator.holds(exit_rule, i)),
                None,
            )
    except StrategyError as exc:
        return {**base, "status": "unknown", "detail": str(exc)}
    buy_price = Decimal(str(buy["open"]))
    notes = ["bought and sold at the next bar's open after each signal"]
    if sell_index is None:
        sell = bars[-1]
        sell_price = Decimal(str(sell["close"]))
        notes.append("no exit signal in the window: valued at the window's last close")
        sell_at = sell["interval_end_utc"]
    else:
        sell = bars[sell_index]
        sell_price = Decimal(str(sell["open"]))
        sell_at = sell["interval_start_utc"]
    gross = (sell_price - buy_price) * bought
    return {
        **base,
        **window_info,
        "status": "computed",
        "entry_price": _text(buy_price),
        "entry_at_utc": buy["interval_start_utc"],
        "exit_price": _text(sell_price),
        "exit_at_utc": sell_at,
        "quantity": _text(bought),
        "gross_pnl": _text(gross),
        "actual_gross_pnl": _text(actual_gross),
        "difference": _text(gross - actual_gross),
        "notes": notes,
    }


def suggest_strategies(
    get_bars: GetBars, trade: Trade, strategies: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Saved rule strategies whose entry rule held on the last bar closed before the buy."""
    info = _info(trade)
    entry = _first_entry(info.executions)
    if entry is None:
        return []
    instrument = trade.instrument_id
    zone = calendar_for(instrument).zone
    cutoff = entry.at or datetime.combine(entry.day, datetime.min.time(), zone)
    series: dict[str, list[dict[str, Any]]] = {}
    for timeframe in (INTRADAY, "1d"):
        sessions = _sessions_before(
            instrument, entry.day + timedelta(days=1), WARMUP_SESSIONS[timeframe] + 1
        )
        if not sessions:
            continue
        try:
            if timeframe == INTRADAY:
                bars = []
                for session in sessions:
                    bars += _bars(
                        get_bars,
                        instrument,
                        timeframe,
                        session.pre_open_utc,
                        session.post_close_utc,
                    )
            else:
                bars = _bars(
                    get_bars,
                    instrument,
                    timeframe,
                    sessions[0].pre_open_utc,
                    sessions[-1].post_close_utc,
                )
        except MarketDataError:
            continue
        series[timeframe] = [b for b in bars if _parse(str(b["interval_end_utc"])) <= cutoff]
    found = []
    for strategy in strategies:
        if strategy.get("strategy_type") != "rules":
            continue
        try:
            definition = validate_definition(strategy["definition"])
        except StrategyError:
            continue
        held = []
        for timeframe, bars in series.items():
            if not bars:
                continue
            evaluator = RuleEvaluator(definition, _objects(bars), zone.key)
            try:
                if evaluator.holds(definition["entry"], len(bars) - 1):
                    held.append(timeframe)
            except StrategyError:
                continue
        if held:
            found.append(
                {
                    "strategy_id": strategy["strategy_id"],
                    "name": strategy["name"],
                    "timeframes": held,
                }
            )
    return found
