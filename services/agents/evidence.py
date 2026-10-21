"""Deterministic evidence for the chart and trade review Agent (ADR 0022).

Every fact comes from the same bars, indicators and ledgers the screens use. A fact
says what it measures (`code`), its value and unit, an English sentence, and whether
it is hindsight: known only after the moment under review. Facts that need more bars
than exist are left out and listed in `missing`, never estimated.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from services.backtest.data import EngineBar, GetBars, InstrumentInput, load_instrument
from services.backtest.indicators import Bar, compute
from services.market_data.instruments import calendar_for
from services.market_data.provider import InvalidBarsQueryError

INDICATOR_VERSIONS = {"sma": "1", "rsi": "1", "atr": "1", "vwap": "1"}
MIN_TRADES = 30
DAILY_LOOKBACK_DAYS = 420  # about 290 sessions: enough for SMA 50 and RSI 14 warm-up


@dataclass(frozen=True)
class Fact:
    code: str
    value: str
    unit: str  # "price", "percent", "ratio", "count", "days", "currency", "text"
    text: str
    subject: str | None = None
    hindsight: bool = False

    def document(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "subject": self.subject,
            "value": self.value,
            "unit": self.unit,
            "text": self.text,
            "hindsight": self.hindsight,
        }


@dataclass
class Evidence:
    target: dict[str, Any]
    cutoff_utc: str
    facts: list[Fact] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    uncertainty: list[str] = field(default_factory=list)
    dataset_revisions: set[str] = field(default_factory=set)
    source_refs: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _fmt(value: float | Decimal, places: int = 2) -> str:
    return f"{float(value):.{places}f}"


def _pct(value: float) -> str:
    return _fmt(value * 100, 2)


def _floats(bars: Sequence[EngineBar]) -> list[Bar]:
    return [
        Bar(b.time, float(b.open), float(b.high), float(b.low), float(b.close), float(b.volume))
        for b in bars
    ]


def _last(series: Sequence[float | None]) -> float | None:
    return series[-1] if series else None


def _pin(evidence: Evidence, loaded: InstrumentInput) -> None:
    evidence.dataset_revisions.update(loaded.pin["dataset_revisions"])
    evidence.warnings.extend(loaded.warnings)


def _session_date(instrument_id: str, moment: datetime) -> date:
    return moment.astimezone(calendar_for(instrument_id).zone).date()


# --- chart point --------------------------------------------------------------------


def chart_point(
    get_bars: GetBars, instrument_id: str, timeframe: str, cutoff: datetime
) -> Evidence:
    """The chart as it stood at `cutoff`: no bar ending later is read."""
    if cutoff.tzinfo is None:
        raise InvalidBarsQueryError("cutoff must carry a time zone")
    day = _session_date(instrument_id, cutoff)
    daily = timeframe in ("1d", "1w")
    start = day - timedelta(days=DAILY_LOOKBACK_DAYS) if daily else day
    loaded = load_instrument(get_bars, instrument_id, timeframe, start, day, as_of_utc=cutoff)
    limit = int(cutoff.timestamp())
    bars = [bar for bar in loaded.bars if bar.end <= limit]
    evidence = Evidence(
        {"type": "chart", "instrument_id": instrument_id, "timeframe": timeframe},
        _utc(cutoff),
    )
    _pin(evidence, loaded)
    evidence.facts.append(
        Fact("bars_used", str(len(bars)), "count", f"{len(bars)} {timeframe} bars up to the cutoff")
    )
    if not daily:
        evidence.uncertainty.append(
            "Intraday facts use the cutoff's session only, so long indicators may be missing."
        )
    if len(bars) < 2:
        evidence.missing.extend(["change_pct", "sma20", "rsi14"])
        return evidence
    series = _floats(bars)
    last, previous = series[-1], series[-2]
    facts = evidence.facts
    facts.append(Fact("last_close", _fmt(last.close, 4), "price", f"Last close {last.close:g}"))
    change = last.close / previous.close - 1
    facts.append(
        Fact("change_pct", _pct(change), "percent", f"Change from the previous bar {change:+.2%}")
    )
    window = series[-20:]
    top, bottom = max(b.high for b in window), min(b.low for b in window)
    if top > bottom:
        position = (last.close - bottom) / (top - bottom)
        facts.append(
            Fact(
                "range20_position",
                _fmt(position),
                "ratio",
                f"Close sits at {position:.0%} of the last {len(window)} bars' range "
                "(0 = low, 1 = high)",
            )
        )
    for length in (20, 50):
        value = _last(compute("sma", series, {"length": length})["value"])
        if value is None:
            evidence.missing.append(f"sma{length}")
            continue
        distance = last.close / value - 1
        facts.append(
            Fact(
                f"close_vs_sma{length}_pct",
                _pct(distance),
                "percent",
                f"Close is {distance:+.2%} from its {length}-bar simple average",
            )
        )
    sma20 = compute("sma", series, {"length": 20})["value"]
    if len(sma20) > 5 and sma20[-1] is not None and sma20[-6] is not None:
        slope = sma20[-1] / sma20[-6] - 1
        facts.append(
            Fact("sma20_slope5_pct", _pct(slope), "percent", f"SMA 20 moved {slope:+.2%} in 5 bars")
        )
    rsi = _last(compute("rsi", series, {"length": 14})["value"])
    if rsi is None:
        evidence.missing.append("rsi14")
    else:
        facts.append(Fact("rsi14", _fmt(rsi, 1), "ratio", f"RSI 14 is {rsi:.1f}"))
    atr = _last(compute("atr", series, {"length": 14})["value"])
    if atr is None:
        evidence.missing.append("atr14_pct")
    else:
        facts.append(
            Fact(
                "atr14_pct",
                _pct(atr / last.close),
                "percent",
                f"ATR 14 is {atr / last.close:.2%} of the price",
            )
        )
    volumes = [b.volume for b in series[-21:-1]]
    if len(volumes) == 20 and sum(volumes) > 0:
        ratio = last.volume / (sum(volumes) / 20)
        facts.append(
            Fact(
                "volume_vs_avg20",
                _fmt(ratio),
                "ratio",
                f"Volume is {ratio:.2f}x its 20-bar average",
            )
        )
    if not daily:
        vwap = _last(compute("vwap", series, {}, str(calendar_for(instrument_id).zone))["value"])
        if vwap:
            distance = last.close / vwap - 1
            facts.append(
                Fact(
                    "vwap_distance_pct",
                    _pct(distance),
                    "percent",
                    f"Close is {distance:+.2%} from VWAP",
                )
            )
    return evidence


# --- real trades ----------------------------------------------------------------------


@dataclass(frozen=True)
class Execution:
    ref: str  # "<ledger>:<execution_id>"; kept locally, never sent to Claude
    side: str
    quantity: Decimal
    price: Decimal
    fees: Decimal
    at: datetime | None  # exact time when known
    day: date
    precise: bool


def execution_of(ledger: str, document: dict[str, Any], instrument_id: str) -> Execution:
    when = document.get("executed_at_utc")
    if when is None:  # a manual record without a claimed time: the clicked candle's date
        when = document.get("selected_interval_start_utc")
    if when is None:
        raise InvalidBarsQueryError("an execution has no date")
    moment = _parse(str(when))
    precision = document.get("time_precision", "exact")
    precise = document.get("executed_at_utc") is not None and precision in ("exact", "minute")
    fees = sum(
        (
            Decimal(str(document[name]))
            for name in ("fees", "commission", "tax")
            if document.get(name) not in (None, "")
        ),
        Decimal(0),
    )
    return Execution(
        f"{ledger}:{document['execution_id']}",
        str(document["side"]),
        Decimal(str(document["quantity"])),
        Decimal(str(document["price"])),
        fees,
        moment if precise else None,
        _session_date(instrument_id, moment),
        precise,
    )


def trade_review(
    get_bars: GetBars,
    instrument_id: str,
    executions: Sequence[Execution],
    now: datetime,
) -> Evidence:
    today = _session_date(instrument_id, now)
    if not executions:
        raise InvalidBarsQueryError("choose at least one execution")
    fills = sorted(
        executions, key=lambda e: (e.day, e.at or datetime.min.replace(tzinfo=timezone.utc))
    )
    first_day, last_day = fills[0].day, fills[-1].day
    evidence = Evidence(
        {"type": "trades", "instrument_id": instrument_id, "execution_count": len(fills)},
        _utc(now),
    )
    evidence.source_refs = [f.ref for f in fills]
    if any(not f.precise for f in fills):
        evidence.uncertainty.append(
            "Some executions have no exact time; they are placed by their date only."
        )
    facts = evidence.facts

    # FIFO matching, long positions.
    lots: list[list[Decimal]] = []  # [quantity, price]
    realized = Decimal(0)
    unmatched = Decimal(0)
    for fill in fills:
        if fill.side == "buy":
            lots.append([fill.quantity, fill.price])
            continue
        remaining = fill.quantity
        while remaining > 0 and lots:
            take = min(remaining, lots[0][0])
            realized += take * (fill.price - lots[0][1])
            lots[0][0] -= take
            remaining -= take
            if lots[0][0] == 0:
                lots.pop(0)
        unmatched += remaining
    buys = [f for f in fills if f.side == "buy"]
    sells = [f for f in fills if f.side == "sell"]
    fees = sum((f.fees for f in fills), Decimal(0))
    bought = sum((f.quantity for f in buys), Decimal(0))
    sold = sum((f.quantity for f in sells), Decimal(0))
    avg_buy = sum((f.quantity * f.price for f in buys), Decimal(0)) / bought if bought else None
    avg_sell = sum((f.quantity * f.price for f in sells), Decimal(0)) / sold if sold else None
    open_quantity = sum((lot[0] for lot in lots), Decimal(0))
    facts.append(
        Fact(
            "execution_count", str(len(fills)), "count", f"{len(buys)} buys and {len(sells)} sells"
        )
    )
    if avg_buy is not None:
        facts.append(
            Fact(
                "average_buy",
                _fmt(avg_buy, 4),
                "price",
                f"Bought {bought:f} at {avg_buy:.4f} on average",
            )
        )
    if avg_sell is not None:
        facts.append(
            Fact(
                "average_sell",
                _fmt(avg_sell, 4),
                "price",
                f"Sold {sold:f} at {avg_sell:.4f} on average",
            )
        )
    facts.append(
        Fact(
            "realized_pnl",
            _fmt(realized - fees),
            "currency",
            f"Realized P&L after fees {realized - fees:.2f} (FIFO, fees {fees:.2f})",
        )
    )
    facts.append(
        Fact(
            "holding_days",
            str((last_day - first_day).days),
            "days",
            f"From the first to the last execution: {(last_day - first_day).days} calendar days",
        )
    )
    if open_quantity:
        facts.append(
            Fact(
                "open_quantity",
                f"{open_quantity:f}",
                "count",
                f"{open_quantity:f} shares are still open",
            )
        )
        evidence.uncertainty.append("Part of the position is still open; its outcome is unknown.")
    if unmatched:
        facts.append(
            Fact(
                "unmatched_sell",
                f"{unmatched:f}",
                "count",
                f"{unmatched:f} shares sold without a matching buy in the chosen executions",
            )
        )
        evidence.uncertainty.append("Sells exceed the chosen buys; earlier buys may be missing.")

    end = min(last_day + timedelta(days=14), today)
    loaded = load_instrument(
        get_bars, instrument_id, "1d", first_day - timedelta(days=DAILY_LOOKBACK_DAYS), end
    )
    _pin(evidence, loaded)
    zone = calendar_for(instrument_id).zone
    by_day = {
        datetime.fromtimestamp(bar.time, timezone.utc).astimezone(zone).date(): (i, bar)
        for i, bar in enumerate(loaded.bars)
    }
    for n, fill in enumerate(fills, start=1):
        found = by_day.get(fill.day)
        subject = f"fill {n} ({fill.side} {fill.quantity:f} at {fill.price:f} on {fill.day})"
        if found is None:
            evidence.missing.append(f"fill_range_position:{n}")
            continue
        bar = found[1]
        if bar.high > bar.low:
            position = (fill.price - bar.low) / (bar.high - bar.low)
            facts.append(
                Fact(
                    "fill_range_position",
                    _fmt(position),
                    "ratio",
                    f"{subject} sat at {float(position):.0%} of that session's range",
                    subject=f"fill {n}",
                )
            )

    entry = by_day.get(buys[0].day) if buys else None
    if entry is not None:
        before = _floats(loaded.bars[: entry[0]])  # sessions before the entry only
        rsi = _last(compute("rsi", before, {"length": 14})["value"]) if before else None
        if rsi is not None:
            facts.append(
                Fact(
                    "entry_rsi14",
                    _fmt(rsi, 1),
                    "ratio",
                    f"RSI 14 at the close before the first buy was {rsi:.1f}",
                )
            )
        for length in (20, 50):
            value = _last(compute("sma", before, {"length": length})["value"]) if before else None
            if value is None:
                evidence.missing.append(f"entry_sma{length}")
                continue
            distance = before[-1].close / value - 1
            facts.append(
                Fact(
                    f"entry_close_vs_sma{length}_pct",
                    _pct(distance),
                    "percent",
                    f"Before the first buy the close was {distance:+.2%} "
                    f"from its {length}-day average",
                )
            )
        exit_index = by_day.get(sells[-1].day) if sells and not open_quantity else None
        held = loaded.bars[entry[0] : (exit_index[0] + 1 if exit_index else len(loaded.bars))]
        if held and avg_buy:
            high = max(b.high for b in held)
            low = min(b.low for b in held)
            facts.append(
                Fact(
                    "mfe_pct",
                    _pct(float(high / avg_buy - 1)),
                    "percent",
                    f"While held, the high reached {float(high / avg_buy - 1):+.2%} "
                    "from the average buy",
                    hindsight=True,
                )
            )
            facts.append(
                Fact(
                    "mae_pct",
                    _pct(float(low / avg_buy - 1)),
                    "percent",
                    f"While held, the low reached {float(low / avg_buy - 1):+.2%} "
                    "from the average buy",
                    hindsight=True,
                )
            )
        if exit_index and avg_sell:
            for after in (1, 5):
                index = exit_index[0] + after
                if index < len(loaded.bars):
                    move = float(loaded.bars[index].close / avg_sell - 1)
                    facts.append(
                        Fact(
                            f"after_exit_{after}d_pct",
                            _pct(move),
                            "percent",
                            f"{after} session(s) after the last sell the close was "
                            f"{move:+.2%} from the average sell",
                            hindsight=True,
                        )
                    )
                else:
                    evidence.missing.append(f"after_exit_{after}d_pct")
    return evidence


# --- backtest run ---------------------------------------------------------------------


def backtest_review(run: dict[str, Any], results: dict[str, Any]) -> Evidence:
    metrics = results["metrics"]
    evidence = Evidence(
        {
            "type": "backtest",
            "run_id": run["run_id"],
            "strategy_version": run["strategy_version"],
            "instrument_ids": run["config"]["instrument_ids"],
            "timeframe": run["config"]["timeframe"],
        },
        run.get("finished_at_utc") or run["created_at_utc"],
    )
    evidence.source_refs = [f"backtest:{run['run_id']}"]
    for pin in results["inputs"]:
        evidence.dataset_revisions.update(pin["dataset_revisions"])
    facts = evidence.facts

    def ratio(name: str) -> float | None:
        value = metrics.get(name)
        return None if value is None else float(value)

    net, bench = ratio("net_return"), ratio("benchmark_return")
    if net is not None and bench is not None:
        facts.append(
            Fact(
                "net_vs_benchmark_pct",
                _pct(net - bench),
                "percent",
                f"Net return {net:+.2%} against buy-and-hold {bench:+.2%}",
            )
        )
    mdd = ratio("max_drawdown")
    if mdd is not None:
        facts.append(Fact("max_drawdown_pct", _pct(mdd), "percent", f"Maximum drawdown {mdd:.2%}"))
    trades = int(metrics["trade_count"])
    facts.append(Fact("closed_trades", str(trades), "count", f"{trades} closed trades"))
    if trades < MIN_TRADES:
        evidence.uncertainty.append(
            f"Only {trades} closed trades: too few to tell skill from chance "
            f"(fewer than {MIN_TRADES})."
        )
    win = ratio("win_rate")
    if win is not None:
        facts.append(Fact("win_rate_pct", _pct(win), "percent", f"Win rate {win:.1%}"))
    closed = [t for t in results["trades"] if t["exit_reason"] != "open_at_end"]
    profits = sorted((float(t["net_pnl"]) for t in closed if float(t["net_pnl"]) > 0), reverse=True)
    if profits:
        share = profits[0] / sum(profits)
        facts.append(
            Fact(
                "best_trade_share_pct",
                _pct(share),
                "percent",
                f"The best trade made {share:.1%} of all gross profit",
            )
        )
    gross = sum(profits)
    costs = float(metrics["fees"]) + float(metrics["slippage_cost"])
    if gross > 0:
        facts.append(
            Fact(
                "cost_drag_pct",
                _pct(costs / gross),
                "percent",
                f"Fees and slippage were {costs / gross:.1%} of gross profit",
            )
        )
    unrealized = sum(float(row["unrealized_pnl"]) for row in results["per_symbol"])
    if unrealized:
        facts.append(
            Fact(
                "unrealized_end",
                _fmt(unrealized),
                "currency",
                f"Unrealized P&L still open at the end: {unrealized:.2f} {results['currency']}",
            )
        )
    for row in results["per_symbol"]:
        facts.append(
            Fact(
                "symbol_realized",
                row["realized_pnl"],
                "currency",
                f"{row['instrument_id']}: {row['trade_count']} trades, "
                f"realized {row['realized_pnl']} {results['currency']}",
                subject=row["instrument_id"],
            )
        )
    reasons: dict[str, int] = {}
    for order in results["orders"]:
        if order["status"] == "rejected" and order["reason"]:
            reasons[order["reason"]] = reasons.get(order["reason"], 0) + 1
    for reason, count in sorted(reasons.items()):
        facts.append(
            Fact(
                "rejected_orders",
                str(count),
                "count",
                f"{count} orders rejected: {reason}",
                subject=reason,
            )
        )
    evidence.warnings.extend(results["warnings"])
    if any(
        d.get("code") in ("raw_prices", "suspected_splits")
        for d in results.get("warning_details", [])
    ):
        evidence.uncertainty.append(
            "Prices were not adjusted; splits or dividends may distort the results."
        )
    return evidence
