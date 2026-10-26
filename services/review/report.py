"""Evidence for a single-trade Claude report (ADR 0042, step 2a).

The facts reuse the review Agent's trade evidence (ADR 0022) for the trade's own share
of each fill, then add what the report needs: the market around the entry (SPY, QQQ),
the gap and pre-market high, the trade's own intraday path, and its size against the
account capital the user entered.

Everything describing the decision is cut at the entry; the intraday path while held is
marked hindsight, as the Agent does. The chart images are cut here too: the client only
draws the bars it is given, so no image can show a bar that ended after its cutoff.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from services.agents.evidence import Evidence, Execution, Fact, instrument_currency, trade_review
from services.backtest.data import GetBars
from services.market_data.calendar import CalendarError, SessionWindow
from services.market_data.instruments import calendar_for
from services.market_data.provider import BarsQuery, MarketDataError
from services.review.trades import Fill, Trade

SPY = "us:spy:arcx:2026"
QQQ = "us:qqq:xnas:2026"
INTRADAY = "5m"
DAILY_IMAGE_SESSIONS = 120
WEEKLY_IMAGE_WEEKS = 104
EXIT_IMAGE_LEAD_SESSIONS = 60


@dataclass(frozen=True)
class Capital:
    """Account capital the user entered, in effect from a date."""

    account_ref: str
    effective_from: date
    amount: Decimal
    currency: str


def _utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _pct(value: Decimal) -> str:
    return f"{float(value) * 100:.2f}"


def executions_of(trade: Trade) -> list[Execution]:
    """The trade's share of each fill, as review Agent executions (fees in proportion)."""
    shares: dict[str, tuple[Fill, Decimal]] = {}
    for piece in trade.allocations:
        fill, quantity = shares.get(piece.fill.ref, (piece.fill, Decimal(0)))
        shares[piece.fill.ref] = (fill, quantity + piece.quantity)
    currency = instrument_currency(trade.instrument_id)
    executions = []
    for fill, quantity in shares.values():
        fees = Decimal(0) if fill.fees is None else fill.fees * quantity / fill.quantity
        executions.append(
            Execution(
                fill.ref,
                fill.side,
                quantity,
                fill.price,
                fees,
                fill.at,
                fill.day,
                fill.at is not None,
                fill.book,
                fill.fees is not None,
                currency,
            )
        )
    return sorted(
        executions, key=lambda e: (e.day, e.at or datetime.min.replace(tzinfo=timezone.utc))
    )


def _window(instrument_id: str, day: date) -> SessionWindow | None:
    try:
        return calendar_for(instrument_id).window_for_date(day)
    except CalendarError:
        return None


def _sessions_before(instrument_id: str, day: date, count: int) -> list[SessionWindow]:
    """Up to `count` trading sessions strictly before `day`, oldest first."""
    found: list[SessionWindow] = []
    cursor = day - timedelta(days=1)
    tries = 0
    while len(found) < count and tries < count * 2 + 20:
        window = _window(instrument_id, cursor)
        if window is not None:
            found.append(window)
        cursor -= timedelta(days=1)
        tries += 1
    return list(reversed(found))


def _bars(
    get_bars: GetBars, instrument_id: str, timeframe: str, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    result = get_bars(BarsQuery(instrument_id, timeframe, start, end))
    return sorted(result.bars, key=lambda bar: str(bar["interval_start_utc"]))


def _ended_by(bars: Sequence[Mapping[str, Any]], cutoff: datetime) -> list[dict[str, Any]]:
    """Only bars that had ended by the cutoff: no bar shows anything after it."""
    return [dict(bar) for bar in bars if _parse(str(bar["interval_end_utc"])) <= cutoff]


def _day_cut(instrument_id: str, day: date) -> datetime:
    """The start of a session day at its exchange: bars of that day are left out."""
    zone = calendar_for(instrument_id).zone
    return datetime.combine(day, datetime.min.time(), zone).astimezone(timezone.utc)


@dataclass
class _Context:
    get_bars: GetBars
    trade: Trade
    executions: list[Execution]
    evidence: Evidence


def _first_entry(executions: Sequence[Execution]) -> Execution | None:
    return next((e for e in executions if e.side == "buy"), None)


def _last_exit(executions: Sequence[Execution]) -> Execution | None:
    sells = [e for e in executions if e.side == "sell"]
    return sells[-1] if sells else None


def _intraday(executions: Sequence[Execution]) -> bool:
    """A trade inside one or two session days whose fills all have a time."""
    if not executions or any(e.at is None for e in executions):
        return False
    return (executions[-1].day - executions[0].day).days <= 1


def _market_facts(ctx: _Context) -> None:
    """Gap, pre-market high and SPY/QQQ up to the first entry (US listings)."""
    evidence, trade = ctx.evidence, ctx.trade
    entry = _first_entry(ctx.executions)
    if entry is None:
        evidence.missing.append("entry_context")
        return
    if trade.instrument_id.startswith("kr:"):
        evidence.uncertainty.append(
            "Market context (index moves, gap, pre-market) is only computed for US listings."
        )
        return
    if entry.at is None:
        evidence.missing.extend(["gap_pct", "premarket_high", "index_change_to_entry"])
        evidence.uncertainty.append(
            "The first entry has no exact time, so the intraday context around it is left out."
        )
        return
    window = _window(trade.instrument_id, entry.day)
    previous = _sessions_before(trade.instrument_id, entry.day, 1)
    if window is None or not previous:
        evidence.missing.append("entry_session")
        return
    prior = previous[-1]
    facts = evidence.facts

    def previous_close(instrument_id: str) -> Decimal | None:
        daily = _bars(ctx.get_bars, instrument_id, "1d", prior.pre_open_utc, prior.post_close_utc)
        return Decimal(str(daily[-1]["close"])) if daily else None

    def until_entry(instrument_id: str) -> list[dict[str, Any]]:
        bars = _bars(
            ctx.get_bars, instrument_id, INTRADAY, window.pre_open_utc, window.post_close_utc
        )
        return _ended_by(bars, entry.at or window.post_close_utc)

    try:
        stock_close = previous_close(trade.instrument_id)
        stock_bars = until_entry(trade.instrument_id)
    except MarketDataError as exc:
        evidence.warnings.append(f"Intraday bars around the entry were not available: {exc}")
        return
    if stock_close is None or stock_close <= 0:
        evidence.missing.append("previous_close")
        return
    move = entry.price / stock_close - 1
    facts.append(
        Fact(
            "entry_vs_previous_close_pct",
            _pct(move),
            "percent",
            f"The first buy at {entry.price:f} was {float(move):+.2%} from the previous "
            f"regular close {stock_close:f}",
        )
    )
    regular = [
        b for b in stock_bars if _parse(str(b["interval_start_utc"])) >= window.regular_open_utc
    ]
    if regular:
        gap = Decimal(str(regular[0]["open"])) / stock_close - 1
        facts.append(
            Fact(
                "gap_pct",
                _pct(gap),
                "percent",
                f"The regular session opened {float(gap):+.2%} from the previous close",
            )
        )
    else:
        evidence.missing.append("gap_pct")
        evidence.uncertainty.append("The first buy came before the regular open.")
    premarket = [
        b for b in stock_bars if _parse(str(b["interval_start_utc"])) < window.regular_open_utc
    ]
    if premarket:
        high = max(Decimal(str(b["high"])) for b in premarket)
        facts.append(
            Fact(
                "premarket_high",
                f"{high:f}",
                "price",
                f"The pre-market high before the first buy was {high:f}",
            )
        )
        distance = entry.price / high - 1
        facts.append(
            Fact(
                "entry_vs_premarket_high_pct",
                _pct(distance),
                "percent",
                f"The first buy was {float(distance):+.2%} from that pre-market high",
            )
        )
    else:
        evidence.missing.append("premarket_high")
    if not stock_bars:
        evidence.missing.append("index_change_to_entry")
        return
    stock_now = Decimal(str(stock_bars[-1]["close"])) / stock_close - 1
    at = _parse(str(stock_bars[-1]["interval_end_utc"]))
    for name, index in (("SPY", SPY), ("QQQ", QQQ)):
        try:
            index_close = previous_close(index)
            index_bars = until_entry(index)
        except MarketDataError:
            evidence.missing.append(f"{name.lower()}_change_to_entry_pct")
            continue
        if index_close is None or not index_bars:
            evidence.missing.append(f"{name.lower()}_change_to_entry_pct")
            continue
        index_now = Decimal(str(index_bars[-1]["close"])) / index_close - 1
        facts.append(
            Fact(
                f"{name.lower()}_change_to_entry_pct",
                _pct(index_now),
                "percent",
                f"{name} was {float(index_now):+.2%} from its previous close by {_utc(at)} "
                "(the last 5-minute bar before the first buy)",
                subject=name,
            )
        )
        facts.append(
            Fact(
                f"relative_to_{name.lower()}_pct",
                _pct(stock_now - index_now),
                "percent",
                f"By then the stock was {float(stock_now):+.2%} from its previous close, "
                f"{float(stock_now - index_now):+.2%} against {name}",
                subject=name,
            )
        )


def _path_facts(ctx: _Context) -> None:
    """The intraday path while held (hindsight), for short timed trades."""
    evidence, trade, executions = ctx.evidence, ctx.trade, ctx.executions
    if not _intraday(executions):
        return
    entry, exit_ = _first_entry(executions), _last_exit(executions)
    if entry is None or entry.at is None:
        return
    buys = [e for e in executions if e.side == "buy"]
    sells = [e for e in executions if e.side == "sell"]
    bought = sum((e.quantity for e in buys), Decimal(0))
    avg_buy = sum((e.quantity * e.price for e in buys), Decimal(0)) / bought
    end = exit_.at if exit_ is not None and exit_.at is not None else None
    windows = [
        w for w in (_window(trade.instrument_id, d) for d in {e.day for e in executions}) if w
    ]
    bars: list[dict[str, Any]] = []
    try:
        for window in sorted(windows, key=lambda w: w.session_date):
            bars += _bars(
                ctx.get_bars,
                trade.instrument_id,
                INTRADAY,
                window.pre_open_utc,
                window.post_close_utc,
            )
    except MarketDataError as exc:
        evidence.warnings.append(f"Intraday bars while held were not available: {exc}")
        return
    held = [
        b
        for b in bars
        if _parse(str(b["interval_end_utc"])) > entry.at
        and (end is None or _parse(str(b["interval_start_utc"])) < end)
    ]
    if not held:
        evidence.missing.append("intraday_path")
        return
    # The 5-minute path replaces the review Agent's daily high and low, which for a trade
    # inside a day also count the hours before the buy and after the sell.
    evidence.facts[:] = [f for f in evidence.facts if f.code not in ("mfe_pct", "mae_pct")]
    high = max(Decimal(str(b["high"])) for b in held)
    low = min(Decimal(str(b["low"])) for b in held)
    evidence.facts.append(
        Fact(
            "intraday_mfe_pct",
            _pct(high / avg_buy - 1),
            "percent",
            f"While held (5-minute bars), the high reached {float(high / avg_buy - 1):+.2%} "
            "from the average buy",
            hindsight=True,
        )
    )
    evidence.facts.append(
        Fact(
            "intraday_mae_pct",
            _pct(low / avg_buy - 1),
            "percent",
            f"While held (5-minute bars), the low reached {float(low / avg_buy - 1):+.2%} "
            "from the average buy",
            hindsight=True,
        )
    )
    sold = sum((e.quantity for e in sells), Decimal(0))
    if sells and sold == bought and high > avg_buy:
        avg_sell = sum((e.quantity * e.price for e in sells), Decimal(0)) / sold
        captured = (avg_sell - avg_buy) / (high - avg_buy)
        evidence.facts.append(
            Fact(
                "captured_of_best_ratio",
                f"{float(captured):.2f}",
                "ratio",
                f"The average sell sat at {float(captured):.2f} of the way from the average "
                "buy to the best price while held (1 = sold at that high, 0 = at the average "
                "buy, below 0 = at a loss)",
                hindsight=True,
            )
        )
    if exit_ is not None and exit_.at is not None:
        minutes = int((exit_.at - entry.at).total_seconds() // 60)
        evidence.facts.append(
            Fact(
                "holding_minutes",
                str(minutes),
                "count",
                f"From the first buy to the last sell: {minutes} minutes",
            )
        )


def _size_facts(ctx: _Context, capital: Sequence[Capital], account_ref: str | None) -> None:
    evidence, executions = ctx.evidence, ctx.executions
    buys = [e for e in executions if e.side == "buy"]
    sells = [e for e in executions if e.side == "sell"]
    evidence.facts.append(
        Fact("entry_count", str(len(buys)), "count", f"{len(buys)} buy(s) built the position")
    )
    evidence.facts.append(
        Fact("exit_count", str(len(sells)), "count", f"{len(sells)} sell(s) closed it")
    )
    if not buys:
        return
    bought = sum((e.quantity for e in buys), Decimal(0))
    avg_buy = sum((e.quantity * e.price for e in buys), Decimal(0)) / bought
    if len(buys) > 1:
        effect = avg_buy / buys[0].price - 1
        evidence.facts.append(
            Fact(
                "scale_in_effect_pct",
                _pct(effect),
                "percent",
                f"Later buys moved the average price {float(effect):+.2%} from the first buy's "
                f"{buys[0].price:f}",
            )
        )
    # The largest cost held at once: buys add their cost, sells take off the oldest lots.
    lots: list[list[Decimal]] = []
    peak = Decimal(0)
    for execution in executions:
        if execution.side == "buy":
            lots.append([execution.quantity, execution.price])
        else:
            left = execution.quantity
            while left > 0 and lots:
                take = min(left, lots[0][0])
                lots[0][0] -= take
                left -= take
                if lots[0][0] == 0:
                    lots.pop(0)
        peak = max(peak, sum((q * p for q, p in lots), Decimal(0)))
    currency = instrument_currency(ctx.trade.instrument_id)
    evidence.facts.append(
        Fact(
            "peak_position_cost",
            f"{peak:.2f}",
            "currency",
            f"At most {peak:.2f} {currency} was held in the position at once (at cost)",
        )
    )
    entered = [
        c
        for c in capital
        if c.account_ref == account_ref
        and c.currency == currency
        and c.effective_from <= buys[0].day
    ]
    if not entered:
        evidence.missing.append("position_pct_of_capital")
        evidence.uncertainty.append(
            "No account capital was entered for this account and date, so the position "
            "size is not compared with the account."
        )
        return
    chosen = max(entered, key=lambda c: c.effective_from)
    share = peak / chosen.amount
    evidence.facts.append(
        Fact(
            "position_pct_of_capital",
            _pct(share),
            "percent",
            f"That was {float(share):.2%} of the account capital the user entered "
            f"({chosen.amount:f} {currency} from {chosen.effective_from})",
        )
    )


def _markers(
    instrument_id: str,
    executions: Sequence[Execution],
    bars: Sequence[Mapping[str, Any]],
    daily: bool,
    cutoff: datetime,
) -> list[dict[str, Any]]:
    """Each execution done by the cutoff, on the bar that holds it (if one is drawn)."""
    zone = calendar_for(instrument_id).zone
    markers = []
    for execution in executions:
        moment = execution.at
        if moment is not None and moment > cutoff:
            continue
        if moment is None and _day_cut(instrument_id, execution.day) >= cutoff:
            continue
        for bar in bars:
            start = _parse(str(bar["interval_start_utc"]))
            end = _parse(str(bar["interval_end_utc"]))
            if daily or moment is None:
                first = start.astimezone(zone).date()
                last = (end - timedelta(seconds=1)).astimezone(zone).date()
                hit = first <= execution.day <= last
            else:
                hit = start <= moment < end
            if hit:
                markers.append(
                    {
                        "time_utc": str(bar["interval_start_utc"]),
                        "side": execution.side,
                        "quantity": f"{execution.quantity:f}",
                        "price": f"{execution.price:f}",
                    }
                )
                break
    return markers


def _image(
    instrument_id: str,
    kind: str,
    timeframe: str,
    cutoff: datetime,
    bars: list[dict[str, Any]],
    executions: Sequence[Execution],
    title: str,
) -> dict[str, Any]:
    daily = timeframe in ("1d", "1w")
    return {
        "kind": kind,
        "timeframe": timeframe,
        "title": title,
        "cutoff_utc": _utc(cutoff),
        "bars": bars,
        "markers": _markers(instrument_id, executions, bars, daily, cutoff),
    }


def _images(ctx: _Context) -> list[dict[str, Any]]:
    """Bars for the three images: up to the entry, up to the exit, and the wider trend."""
    trade, executions, evidence = ctx.trade, ctx.executions, ctx.evidence
    entry, exit_ = _first_entry(executions), _last_exit(executions)
    if entry is None:
        return []
    instrument = trade.instrument_id
    images: list[dict[str, Any]] = []
    entry_cut = entry.at or _day_cut(instrument, entry.day)
    exit_cut = None
    if exit_ is not None and trade.open_quantity == 0:
        exit_cut = exit_.at or _day_cut(instrument, exit_.day)
    before = _sessions_before(instrument, entry.day, DAILY_IMAGE_SESSIONS)
    entry_window = _window(instrument, entry.day)
    try:
        if _intraday(executions) and entry_window is not None:
            day_bars = _bars(
                ctx.get_bars,
                instrument,
                INTRADAY,
                entry_window.pre_open_utc,
                entry_window.post_close_utc,
            )
            images.append(
                _image(
                    instrument,
                    "entry",
                    INTRADAY,
                    entry_cut,
                    _ended_by(day_bars, entry_cut),
                    executions,
                    f"5-minute bars on {entry.day} up to the first buy",
                )
            )
            if exit_cut is not None and exit_ is not None:
                bars = list(day_bars)
                exit_window = _window(instrument, exit_.day)
                if exit_window is not None and exit_.day != entry.day:
                    bars += _bars(
                        ctx.get_bars,
                        instrument,
                        INTRADAY,
                        exit_window.pre_open_utc,
                        exit_window.post_close_utc,
                    )
                images.append(
                    _image(
                        instrument,
                        "exit",
                        INTRADAY,
                        exit_cut,
                        _ended_by(bars, exit_cut),
                        executions,
                        "5-minute bars from the entry day's pre-market up to the last sell",
                    )
                )
            if before:
                daily = _bars(
                    ctx.get_bars,
                    instrument,
                    "1d",
                    before[0].pre_open_utc,
                    before[-1].post_close_utc,
                )
                images.append(
                    _image(
                        instrument,
                        "context",
                        "1d",
                        entry_cut,
                        _ended_by(daily, entry_cut),
                        executions,
                        f"Daily bars for {len(before)} sessions before the entry day",
                    )
                )
        else:
            if before:
                daily = _bars(
                    ctx.get_bars,
                    instrument,
                    "1d",
                    before[0].pre_open_utc,
                    before[-1].post_close_utc,
                )
                images.append(
                    _image(
                        instrument,
                        "entry",
                        "1d",
                        entry_cut,
                        _ended_by(daily, entry_cut),
                        executions,
                        f"Daily bars for {len(before)} sessions before the first buy",
                    )
                )
            if exit_cut is not None and exit_ is not None and before:
                lead = before[-EXIT_IMAGE_LEAD_SESSIONS:]
                last = _window(instrument, exit_.day)
                end = last.post_close_utc if last else exit_cut
                daily = _bars(ctx.get_bars, instrument, "1d", lead[0].pre_open_utc, end)
                images.append(
                    _image(
                        instrument,
                        "exit",
                        "1d",
                        exit_cut,
                        _ended_by(daily, exit_cut),
                        executions,
                        "Daily bars from before the entry up to the last sell",
                    )
                )
            weeks = _sessions_before(instrument, entry.day, WEEKLY_IMAGE_WEEKS * 5)
            if weeks:
                weekly = _bars(
                    ctx.get_bars, instrument, "1w", weeks[0].pre_open_utc, weeks[-1].post_close_utc
                )
                images.append(
                    _image(
                        instrument,
                        "context",
                        "1w",
                        entry_cut,
                        _ended_by(weekly, entry_cut),
                        executions,
                        "Weekly bars for about two years before the entry",
                    )
                )
    except MarketDataError as exc:
        evidence.warnings.append(f"Bars for the chart images were not available: {exc}")
    return [image for image in images if image["bars"]]


def report_evidence(
    get_bars: GetBars,
    trade: Trade,
    account_ref: str | None,
    capital: Sequence[Capital],
    now: datetime,
) -> tuple[Evidence, list[dict[str, Any]]]:
    """The facts and chart image bars of one trade's report."""
    executions = executions_of(trade)
    evidence = trade_review(get_bars, trade.instrument_id, executions, now)
    evidence.agent_id = "trade_report"
    evidence.agent_version = "trade-report-1"
    ctx = _Context(get_bars, trade, executions, evidence)
    _market_facts(ctx)
    _path_facts(ctx)
    _size_facts(ctx, capital, account_ref)
    return evidence, _images(ctx)
