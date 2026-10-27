"""Period review statistics (ADR 0042, step 4a). No Claude here.

Trades closed in the period are summed per currency (dollars and won never mix). Net
profit is after the fees the fills reported. Stored principle checks feed the principles
section and the principles what-if total; trades not checked yet are counted, not
guessed.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from services.agents.evidence import instrument_currency
from services.market_data.calendar import CalendarError
from services.market_data.instruments import calendar_for
from services.review.report import executions_of, peak_cost
from services.review.trades import Trade, trade_document

ZERO = Decimal(0)
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
# Position-size buckets by peak cost; won steps are the dollar steps times 1,000.
SIZE_STEPS = {
    "USD": (Decimal(1000), Decimal(5000), Decimal(20000)),
    "KRW": (Decimal(1_000_000), Decimal(5_000_000), Decimal(20_000_000)),
}


def _text(value: Decimal) -> str:
    return "0" if value == 0 else format(value.quantize(Decimal("0.0001")).normalize(), "f")


def _ratio(value: Decimal | None) -> str | None:
    return None if value is None else format(value.quantize(Decimal("0.0001")).normalize(), "f")


@dataclass
class Row:
    """One closed trade as the period statistics see it."""

    trade_id: str
    instrument_id: str
    currency: str
    net: Decimal
    gross: Decimal
    fees: Decimal
    opened_on: date
    opened_at: datetime | None
    closed_on: date
    closed_at: datetime | None
    holding_days: int
    peak: Decimal
    tags: list[str] = field(default_factory=list)
    check: Mapping[str, Any] | None = None


def rows_for(
    trades: Iterable[Trade],
    first: date,
    last: date,
    notes: Mapping[str, Mapping[str, Any]],
    checks: Mapping[str, Mapping[str, Any]],
) -> list[Row]:
    """Trades closed between the dates (inclusive), in closing order."""
    rows = []
    for trade in trades:
        document = trade_document(trade)
        if document["status"] != "closed" or document["closed_on"] is None:
            continue
        closed_on = date.fromisoformat(document["closed_on"])
        if not first <= closed_on <= last:
            continue
        note = notes.get(trade.trade_id)
        rows.append(
            Row(
                trade.trade_id,
                trade.instrument_id,
                instrument_currency(trade.instrument_id),
                Decimal(document["net_pnl"]),
                Decimal(document["gross_pnl"]),
                Decimal(document["fees"]),
                date.fromisoformat(document["opened_on"]),
                _parse(document["opened_at_utc"]),
                closed_on,
                _parse(document["closed_at_utc"]),
                int(document["holding_days"] or 0),
                peak_cost(executions_of(trade)),
                list(note["tags"]) if note else [],
                checks.get(trade.trade_id),
            )
        )
    return sorted(rows, key=lambda r: (r.closed_on, r.closed_at or datetime.min, r.trade_id))


def _parse(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value.replace("Z", "+00:00"))


def summary(rows: Sequence[Row]) -> dict[str, Any]:
    """Basic results and risk for rows of one currency."""
    wins = [r.net for r in rows if r.net > 0]
    losses = [r.net for r in rows if r.net < 0]
    total = sum((r.net for r in rows), ZERO)
    won, lost = sum(wins, ZERO), sum(losses, ZERO)
    peak = running = ZERO
    drawdown = ZERO
    streak = longest = 0
    for row in rows:
        running += row.net
        peak = max(peak, running)
        drawdown = max(drawdown, peak - running)
        streak = streak + 1 if row.net < 0 else 0
        longest = max(longest, streak)
    count = len(rows)
    return {
        "trades": count,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": _ratio(Decimal(len(wins)) / count) if count else None,
        "net_pnl": _text(total),
        "gross_pnl": _text(sum((r.gross for r in rows), ZERO)),
        "fees": _text(sum((r.fees for r in rows), ZERO)),
        "average_win": _text(won / len(wins)) if wins else None,
        "average_loss": _text(lost / len(losses)) if losses else None,
        "profit_factor": _ratio(won / -lost) if lost else None,
        "expectancy": _text(total / count) if count else None,
        "max_drawdown": _text(drawdown),
        "longest_losing_streak": longest,
        "largest_loss_share": _ratio(min(losses) / lost) if losses else None,
    }


def _time_bucket(row: Row) -> str:
    if row.opened_at is None:
        return "unknown"
    try:
        window = calendar_for(row.instrument_id).window_for_date(row.opened_on)
    except CalendarError:
        window = None
    if window is None:
        return "unknown"
    if row.opened_at < window.regular_open_utc:
        return "pre"
    if row.opened_at < window.regular_open_utc + timedelta(minutes=30):
        return "first30"
    if row.opened_at < window.regular_close_utc:
        return "regular"
    return "after"


def _holding_bucket(row: Row) -> str:
    if row.holding_days == 0:
        return "intraday"
    if row.holding_days <= 5:
        return "1-5d"
    if row.holding_days <= 20:
        return "6-20d"
    return "21d+"


def _size_bucket(row: Row) -> str:
    low, mid, high = SIZE_STEPS.get(row.currency, SIZE_STEPS["USD"])
    if row.peak < low:
        return f"<{low:f}"
    if row.peak < mid:
        return f"{low:f}-{mid:f}"
    if row.peak < high:
        return f"{mid:f}-{high:f}"
    return f">={high:f}"


def _symbol(instrument_id: str) -> str:
    return (instrument_id.split(":")[1] if ":" in instrument_id else instrument_id).upper()


def groups(rows: Sequence[Row]) -> dict[str, list[dict[str, Any]]]:
    """Each grouping as rows of key, trades, win rate, net and average net."""
    keys: dict[str, Any] = {
        "tag": lambda r: r.tags or ["(none)"],
        "ticker": lambda r: [_symbol(r.instrument_id)],
        "time_of_day": lambda r: [_time_bucket(r)],
        "weekday": lambda r: [WEEKDAYS[r.opened_on.weekday()]],
        "holding": lambda r: [_holding_bucket(r)],
        "size": lambda r: [_size_bucket(r)],
    }
    out: dict[str, list[dict[str, Any]]] = {}
    for name, key in keys.items():
        buckets: dict[str, list[Row]] = defaultdict(list)
        for row in rows:
            for value in key(row):
                buckets[value].append(row)
        table: list[dict[str, Any]] = []
        for value, members in buckets.items():
            net = sum((m.net for m in members), ZERO)
            wins = sum(1 for m in members if m.net > 0)
            table.append(
                {
                    "key": value,
                    "trades": len(members),
                    "win_rate": _ratio(Decimal(wins) / len(members)),
                    "net_pnl": _text(net),
                    "average_net": _text(net / len(members)),
                }
            )
        # Busiest first; the ticker table keeps the 30 busiest.
        table.sort(key=lambda item: (-item["trades"], item["key"]))
        out[name] = table[:30] if name == "ticker" else table
    return out


def principles_section(rows: Sequence[Row], items: Mapping[str, str]) -> dict[str, Any]:
    """Broken counts per item and profit of trades that broke something vs not."""
    checked = [r for r in rows if r.check is not None]
    per_item: dict[str, dict[str, Any]] = {}
    broken_rows: list[Row] = []
    kept_rows: list[Row] = []
    for row in checked:
        assert row.check is not None
        broke = False
        for result in row.check.get("results", []):
            entry = per_item.setdefault(
                result["item_id"],
                {
                    "item_id": result["item_id"],
                    "text": result.get("text") or items.get(result["item_id"], ""),
                    "kind": result["kind"],
                    "broken": 0,
                    "kept": 0,
                    "other": 0,
                },
            )
            if result["status"] == "broken":
                entry["broken"] += 1
                broke = True
            elif result["status"] == "kept":
                entry["kept"] += 1
            else:
                entry["other"] += 1
        (broken_rows if broke else kept_rows).append(row)
    return {
        "checked": len(checked),
        "unchecked": len(rows) - len(checked),
        "items": sorted(per_item.values(), key=lambda item: -int(item["broken"])),
        "broken_trades": len(broken_rows),
        "broken_net_pnl": _text(sum((r.net for r in broken_rows), ZERO)),
        "kept_trades": len(kept_rows),
        "kept_net_pnl": _text(sum((r.net for r in kept_rows), ZERO)),
    }


def what_if_section(rows: Sequence[Row]) -> dict[str, Any]:
    """The principles what-if summed over trades where it was computed (hindsight)."""
    computed = [
        r
        for r in rows
        if r.check is not None
        and r.check.get("what_if")
        and r.check["what_if"].get("status") == "computed"
    ]
    hypothetical = sum((Decimal(r.check["what_if"]["gross_pnl"]) for r in computed), ZERO)  # type: ignore[index]
    actual = sum((Decimal(r.check["what_if"]["actual_gross_pnl"]) for r in computed), ZERO)  # type: ignore[index]
    return {
        "hindsight": True,
        "computed": len(computed),
        "missing": len(rows) - len(computed),
        "gross_pnl": _text(hypothetical),
        "actual_gross_pnl": _text(actual),
        "difference": _text(hypothetical - actual),
    }


def curve(rows: Sequence[Row]) -> list[dict[str, Any]]:
    running = ZERO
    points = []
    for row in rows:
        running += row.net
        at = row.closed_at.isoformat().replace("+00:00", "Z") if row.closed_at else None
        points.append(
            {
                "closed_on": row.closed_on.isoformat(),
                "closed_at_utc": at,
                "cumulative": _text(running),
            }
        )
    return points


def daily(rows: Sequence[Row]) -> list[dict[str, Any]]:
    per_day: dict[date, list[Row]] = defaultdict(list)
    for row in rows:
        per_day[row.closed_on].append(row)
    return [
        {
            "date": day.isoformat(),
            "trades": len(members),
            "net_pnl": _text(sum((m.net for m in members), ZERO)),
        }
        for day, members in sorted(per_day.items())
    ]


def by_currency(rows: Sequence[Row]) -> dict[str, list[Row]]:
    split: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        split[row.currency].append(row)
    return dict(sorted(split.items()))


def period_sections(
    rows: Sequence[Row], previous: Sequence[Row], items: Mapping[str, str]
) -> list[dict[str, Any]]:
    """One section per currency traded in the period (or the period before)."""
    now_split, before_split = by_currency(rows), by_currency(previous)
    sections = []
    for currency in sorted(set(now_split) | set(before_split)):
        current = now_split.get(currency, [])
        earlier = before_split.get(currency, [])
        sections.append(
            {
                "currency": currency,
                "summary": summary(current),
                "previous_summary": summary(earlier),
                "principles": principles_section(current, items),
                "what_if": what_if_section(current),
                "groups": groups(current),
                "curve": curve(current),
                "daily": daily(current),
            }
        )
    return sections
