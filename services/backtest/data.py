"""Loads and pins the bars a backtest runs on, through the same bar service as the charts.

Daily and weekly runs read the whole date range in one query. Intraday runs read each
regular session separately (at most MAX_INTRADAY_SESSIONS), because aggregated bars are
built per session. Every input is pinned: source, feed, adjustment, revisions, coverage,
missing intervals, non-final bars and a SHA-256 of the bars exactly as the engine got them.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from services.market_data.calendar import CalendarError
from services.market_data.instruments import calendar_for
from services.market_data.provider import BarsQuery, BarsResult, InvalidBarsQueryError

MAX_INTRADAY_SESSIONS = 60
MAX_DAILY_DAYS = 366 * 30
MISSING_LIST_LIMIT = 50

GetBars = Callable[[BarsQuery], BarsResult]


@dataclass(frozen=True)
class EngineBar:
    time: int  # interval start, epoch seconds
    end: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    final: bool


@dataclass
class InstrumentInput:
    instrument_id: str
    time_zone: str
    bars: list[EngineBar]
    pin: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _epoch(text: str) -> int:
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sessions(instrument_id: str, start: date, end: date) -> list[Any]:
    calendar = calendar_for(instrument_id)
    windows = []
    day = start
    try:
        while day <= end:
            window = calendar.window_for_date(day)
            if window is not None:
                windows.append(window)
            day += timedelta(days=1)
    except CalendarError as exc:
        raise InvalidBarsQueryError(str(exc)) from exc
    return windows


def suspected_splits(bars: list[EngineBar]) -> list[str]:
    """UTC dates where the open sits near a whole split ratio of the previous close.

    Only a hint for unadjusted prices: the bars are never changed. A gap that happens to
    look like a split is reported too.
    """
    found: list[str] = []
    for before, bar in zip(bars, bars[1:], strict=False):
        if before.close <= 0 or bar.open <= 0:
            continue
        ratio = bar.open / before.close
        jump = ratio if ratio >= 1 else 1 / ratio
        if jump < Decimal("1.8"):
            continue
        nearest = jump.to_integral_value()
        if abs(jump - nearest) / nearest <= Decimal("0.06"):
            found.append(datetime.fromtimestamp(bar.time, timezone.utc).date().isoformat())
    return found


def load_instrument(
    get_bars: GetBars,
    instrument_id: str,
    timeframe: str,
    start: date,
    end: date,
    as_of_utc: datetime | None = None,
) -> InstrumentInput:
    if end < start:
        raise InvalidBarsQueryError("end_date must not be before start_date")
    if (end - start).days > MAX_DAILY_DAYS:
        raise InvalidBarsQueryError("a backtest may span at most 30 years")
    calendar = calendar_for(instrument_id)
    windows = _sessions(instrument_id, start, end)
    if not windows:
        raise InvalidBarsQueryError(f"{instrument_id} has no trading session in the range")
    if timeframe in ("1d", "1w"):
        # Whole days, so both regular-session and whole-day (08:00-20:00 KST) bars fit.
        ranges = [(windows[0].pre_open_utc, windows[-1].post_close_utc)]
    else:
        if len(windows) > MAX_INTRADAY_SESSIONS:
            raise InvalidBarsQueryError(
                f"intraday backtests may span at most {MAX_INTRADAY_SESSIONS} sessions"
            )
        ranges = [(w.regular_open_utc, w.regular_close_utc) for w in windows]

    documents: list[dict[str, Any]] = []
    coverages: list[dict[str, Any]] = []
    for range_start, range_end in ranges:
        result = get_bars(
            BarsQuery(instrument_id, timeframe, range_start, range_end, as_of_utc=as_of_utc)
        )
        documents.extend(result.bars)
        coverages.append(result.coverage)

    documents.sort(key=lambda bar: str(bar["interval_start_utc"]))
    bars = [
        EngineBar(
            time=_epoch(bar["interval_start_utc"]),
            end=_epoch(bar["interval_end_utc"]),
            open=Decimal(bar["open"]),
            high=Decimal(bar["high"]),
            low=Decimal(bar["low"]),
            close=Decimal(bar["close"]),
            volume=Decimal(bar["volume"]),
            final=bool(bar["is_final"]),
        )
        for bar in documents
    ]
    missing = [gap for coverage in coverages for gap in coverage["missing_intervals"]]
    non_final = sum(1 for bar in bars if not bar.final)
    adjustments = sorted({str(c["adjustment"]) for c in coverages})
    digest = hashlib.sha256(
        json.dumps(documents, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    pin = {
        "instrument_id": instrument_id,
        "timeframe": timeframe,
        "calendar_id": calendar.calendar_id,
        "calendar_version": calendar.calendar_version,
        "requested_start_utc": _utc(ranges[0][0]),
        "requested_end_utc": _utc(ranges[-1][1]),
        "as_of_utc": None if as_of_utc is None else _utc(as_of_utc),
        "source_ids": sorted({str(c["source_id"]) for c in coverages}),
        "feed_scopes": sorted({str(c["source_feed_scope"]) for c in coverages}),
        "adjustments": adjustments,
        "dataset_revisions": sorted({str(c["dataset_revision"]) for c in coverages}),
        "bar_count": len(bars),
        "expected_interval_count": sum(int(c["expected_interval_count"]) for c in coverages),
        "missing_interval_count": len(missing),
        "missing_intervals": missing[:MISSING_LIST_LIMIT],
        "non_final_bar_count": non_final,
        "bars_sha256": digest,
    }
    warnings: list[str] = []
    if missing:
        warnings.append(f"{instrument_id}: {len(missing)} expected intervals have no bar")
    if non_final:
        warnings.append(f"{instrument_id}: {non_final} bars are not final and give no signals")
    splits = suspected_splits(bars) if "raw" in adjustments else []
    pin["suspected_split_dates"] = splits
    if "raw" in adjustments:
        warnings.append(
            f"{instrument_id}: prices are not adjusted; a split or dividend in the range "
            "shows up as a price jump"
        )
    if splits:
        warnings.append(
            f"{instrument_id}: the price jumps by about a whole split ratio on "
            f"{', '.join(splits[:5])}; results around those dates are likely wrong"
        )
    if not bars:
        warnings.append(f"{instrument_id}: no bars in the range")
    return InstrumentInput(instrument_id, str(calendar.zone), bars, pin, warnings)
