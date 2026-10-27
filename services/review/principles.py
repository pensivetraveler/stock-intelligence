"""Trading principles and how one trade measured up to them (ADR 0042, step 3a).

A principles version is the whole set: shared items plus items for one strategy. Items
with a number are checked here from the trade's fills and bars; text items are left to
Claude in the report, which must say it is judging.

A trade is checked against the version in effect when it opened. A trade from before
the first version is checked against the current one and marked "after the fact".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from services.agents.evidence import Execution, instrument_currency
from services.backtest.data import GetBars
from services.market_data.provider import MarketDataError
from services.review.report import (
    INTRADAY,
    Capital,
    _bars,
    _first_entry,
    _intraday,
    _last_exit,
    _parse,
    _sessions_before,
    _window,
    capital_for,
    executions_of,
    peak_cost,
)
from services.review.trades import Trade

Kind = Literal[
    "stop_loss_pct",
    "take_profit_pct",
    "max_position_pct",
    "no_entry_first_minutes",
    "max_gap_chase_pct",
    "max_trades_per_day",
    "text",
]
NUMERIC_KINDS: tuple[str, ...] = (
    "stop_loss_pct",
    "take_profit_pct",
    "max_position_pct",
    "no_entry_first_minutes",
    "max_gap_chase_pct",
    "max_trades_per_day",
)
KINDS: tuple[str, ...] = (*NUMERIC_KINDS, "text")
Status = Literal["kept", "broken", "not_applicable", "unknown", "judgment"]


class PrincipleError(ValueError):
    """An item that cannot be stored as given."""


def clean_items(items: Sequence[Mapping[str, Any]], strategy_ids: set[str]) -> list[dict[str, Any]]:
    """Validated items: a known kind, a positive number for numeric kinds, a known scope."""
    cleaned = []
    for index, item in enumerate(items, start=1):
        kind = str(item.get("kind", ""))
        if kind not in KINDS:
            raise PrincipleError(f"item {index}: unknown kind {kind!r}")
        scope = str(item.get("scope") or "shared")
        if scope != "shared" and scope not in strategy_ids:
            raise PrincipleError(f"item {index}: no strategy {scope}")
        text = str(item.get("text") or "").strip()[:500]
        value: str | None = None
        if kind != "text":
            try:
                number = Decimal(str(item.get("value")))
            except (InvalidOperation, ValueError) as exc:
                raise PrincipleError(f"item {index}: {kind} needs a number") from exc
            if not number.is_finite() or number <= 0:
                raise PrincipleError(f"item {index}: {kind} must be above 0")
            if kind in ("no_entry_first_minutes", "max_trades_per_day") and number != int(number):
                raise PrincipleError(f"item {index}: {kind} must be a whole number")
            value = format(number.normalize(), "f")
        elif not text:
            raise PrincipleError(f"item {index}: a text item needs its sentence")
        cleaned.append(
            {"item_id": f"p{index}", "scope": scope, "kind": kind, "value": value, "text": text}
        )
    return cleaned


def version_for(
    versions: Sequence[Mapping[str, Any]], opened: datetime
) -> tuple[Mapping[str, Any] | None, bool]:
    """The version in effect when the trade opened, or the current one after the fact."""
    if not versions:
        return None, False
    effective = [v for v in versions if _parse(str(v["created_at_utc"])) <= opened]
    if effective:
        return max(effective, key=lambda v: int(v["version"])), False
    return max(versions, key=lambda v: int(v["version"])), True


@dataclass
class _Trade:
    trade: Trade
    executions: list[Execution]
    avg_buy: Decimal | None
    avg_sell: Decimal | None


def _held_path(
    get_bars: GetBars, info: _Trade, now: datetime
) -> tuple[list[dict[str, Any]], str, list[str]]:
    """Bars while held, their timeframe, and caveats about what they cover."""
    executions = info.executions
    entry, exit_ = _first_entry(executions), _last_exit(executions)
    instrument = info.trade.instrument_id
    if entry is None:
        return [], INTRADAY, []
    closed = info.trade.open_quantity == 0 and exit_ is not None
    if _intraday(executions) and entry.at is not None:
        end = exit_.at if closed and exit_ is not None else None
        bars: list[dict[str, Any]] = []
        for day in sorted({e.day for e in executions}):
            window = _window(instrument, day)
            if window is not None:
                bars += _bars(
                    get_bars, instrument, INTRADAY, window.pre_open_utc, window.post_close_utc
                )
        held = [
            b
            for b in bars
            if _parse(str(b["interval_end_utc"])) > entry.at
            and (end is None or _parse(str(b["interval_start_utc"])) < end)
        ]
        return held, INTRADAY, []
    last_day = exit_.day if closed and exit_ is not None else now.date()
    first = _window(instrument, entry.day)
    last = _window(instrument, last_day) or _sessions_before(instrument, last_day, 1)[-1]
    if first is None:
        return [], "1d", ["the entry day is not a trading session"]
    bars = _bars(get_bars, instrument, "1d", first.pre_open_utc, last.post_close_utc)
    # Daily bars of the entry and exit days also cover hours before the buy and after the
    # sell, so a touch on those days cannot be placed: only the days in between count.
    inner = [
        b
        for b in bars
        if entry.day < _bar_day(b, instrument) < (last_day if closed else last_day + timedelta(1))
    ]
    return inner, "1d", ["touches on the entry and exit days are not counted (daily bars)"]


def _bar_day(bar: Mapping[str, Any], instrument_id: str) -> date:
    from services.market_data.instruments import calendar_for

    zone = calendar_for(instrument_id).zone
    return _parse(str(bar["interval_start_utc"])).astimezone(zone).date()


def _result(item: Mapping[str, Any], status: Status, detail: str) -> dict[str, Any]:
    return {**item, "status": status, "detail": detail}


def check_trade(
    get_bars: GetBars,
    trade: Trade,
    items: Sequence[Mapping[str, Any]],
    account_ref: str | None,
    capital: Sequence[Capital],
    trades_that_day: int,
    now: datetime,
) -> list[dict[str, Any]]:
    """Each applicable item (shared, and the trade's strategy) with its result."""
    applicable = [i for i in items if i["scope"] in ("shared", trade.strategy_id)]
    executions = executions_of(trade)
    buys = [e for e in executions if e.side == "buy"]
    sells = [e for e in executions if e.side == "sell"]
    bought = sum((e.quantity for e in buys), Decimal(0))
    sold = sum((e.quantity for e in sells), Decimal(0))
    info = _Trade(
        trade,
        executions,
        sum((e.quantity * e.price for e in buys), Decimal(0)) / bought if bought else None,
        sum((e.quantity * e.price for e in sells), Decimal(0)) / sold if sold else None,
    )
    path_cache: list[tuple[list[dict[str, Any]], str, list[str]]] = []

    def path() -> tuple[list[dict[str, Any]], str, list[str]]:
        if not path_cache:
            path_cache.append(_held_path(get_bars, info, now))
        return path_cache[0]

    results = []
    for item in applicable:
        kind = item["kind"]
        value = Decimal(item["value"]) if item["value"] is not None else Decimal(0)
        try:
            if kind == "text":
                results.append(_result(item, "judgment", "judged by Claude in the report"))
            elif kind in ("stop_loss_pct", "take_profit_pct"):
                results.append(_price_limit(item, value, info, path()))
            elif kind == "max_position_pct":
                results.append(_position(item, value, info, account_ref, capital))
            elif kind == "no_entry_first_minutes":
                results.append(_first_minutes(item, value, info))
            elif kind == "max_gap_chase_pct":
                results.append(_gap_chase(item, value, info, get_bars))
            else:
                results.append(
                    _result(
                        item,
                        "kept" if trades_that_day <= value else "broken",
                        f"{trades_that_day} trade(s) opened that day in this ledger",
                    )
                )
        except MarketDataError as exc:
            results.append(_result(item, "unknown", f"bars not available: {exc}"))
    return results


def _price_limit(
    item: Mapping[str, Any],
    value: Decimal,
    info: _Trade,
    held: tuple[list[dict[str, Any]], str, list[str]],
) -> dict[str, Any]:
    stop = item["kind"] == "stop_loss_pct"
    if info.avg_buy is None:
        return _result(item, "unknown", "no buy in this trade")
    level = info.avg_buy * (1 - value / 100 if stop else 1 + value / 100)
    bars, timeframe, caveats = held
    note = f" ({'; '.join(caveats)})" if caveats else ""
    touched = next(
        (
            b
            for b in bars
            if (Decimal(str(b["low"])) <= level if stop else Decimal(str(b["high"])) >= level)
        ),
        None,
    )
    exit_ = _last_exit(info.executions)
    closed = info.trade.open_quantity == 0 and exit_ is not None
    word = "stop" if stop else "target"
    never = f"the price never reached the {word} {level:.4f} while held ({timeframe} bars){note}"
    if info.avg_sell is not None and closed:
        result = info.avg_sell / info.avg_buy - 1
        if stop and result < -value / 100:
            return _result(
                item,
                "broken",
                f"sold at {float(result):+.2%}, past the {word} of -{value}% ({level:.4f}){note}",
            )
    if touched is None:
        if stop:
            return _result(
                item,
                "kept",
                never,
            )
        reached = info.avg_sell is not None and info.avg_sell >= level
        return _result(
            item,
            "kept" if reached else "not_applicable",
            f"sold at or above the {word} {level:.4f}" if reached else never,
        )
    at = touched["interval_start_utc"]
    if not closed:
        return _result(
            item,
            "broken",
            f"the price reached the {word} {level:.4f} in the bar of {at}, "
            "and the position is still open",
        )
    end = _parse(str(touched["interval_end_utc"]))
    exit_at = exit_.at if exit_ is not None else None
    if exit_at is not None and exit_at <= end:
        return _result(item, "kept", f"sold within the bar that reached the {word} ({at})")
    held_on = (
        f"the price reached the {word} {level:.4f} in the bar of {at} ({timeframe}), "
        "but the position was held on"
    )
    if not stop and info.avg_sell is not None and info.avg_sell >= level:
        return _result(item, "kept", f"{held_on}; it was still sold at or above the target")
    return _result(item, "broken", f"{held_on}{note}")


def _position(
    item: Mapping[str, Any],
    value: Decimal,
    info: _Trade,
    account_ref: str | None,
    capital: Sequence[Capital],
) -> dict[str, Any]:
    entry = _first_entry(info.executions)
    if entry is None:
        return _result(item, "unknown", "no buy in this trade")
    currency = instrument_currency(info.trade.instrument_id)
    chosen = capital_for(capital, account_ref, currency, entry.day)
    if chosen is None:
        return _result(item, "unknown", "no account capital entered for this account and date")
    share = peak_cost(info.executions) / chosen.amount * 100
    return _result(
        item,
        "kept" if share <= value else "broken",
        f"the position peaked at {share:.2f}% of {chosen.amount} {currency}",
    )


def _first_minutes(item: Mapping[str, Any], value: Decimal, info: _Trade) -> dict[str, Any]:
    entry = _first_entry(info.executions)
    if entry is None or entry.at is None:
        return _result(item, "unknown", "the first buy has no exact time")
    window = _window(info.trade.instrument_id, entry.day)
    if window is None:
        return _result(item, "unknown", "the entry day is not a trading session")
    after = (entry.at - window.regular_open_utc).total_seconds() / 60
    if 0 <= after < float(value):
        return _result(item, "broken", f"bought {after:.0f} minute(s) after the regular open")
    if after < 0:
        return _result(item, "kept", "bought before the regular open")
    return _result(item, "kept", f"bought {after:.0f} minute(s) after the regular open")


def _gap_chase(
    item: Mapping[str, Any], value: Decimal, info: _Trade, get_bars: GetBars
) -> dict[str, Any]:
    entry = _first_entry(info.executions)
    if entry is None:
        return _result(item, "unknown", "no buy in this trade")
    previous = _sessions_before(info.trade.instrument_id, entry.day, 1)
    if not previous:
        return _result(item, "unknown", "no earlier session")
    daily = _bars(
        get_bars,
        info.trade.instrument_id,
        "1d",
        previous[-1].pre_open_utc,
        previous[-1].post_close_utc,
    )
    if not daily:
        return _result(item, "unknown", "no previous close")
    close = Decimal(str(daily[-1]["close"]))
    move = (entry.price / close - 1) * 100
    return _result(
        item,
        "broken" if move >= value else "kept",
        f"the first buy was {move:+.2f}% from the previous close {close}",
    )
