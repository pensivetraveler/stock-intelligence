"""Loads and pins the bars a backtest runs on, through the same bar service as the charts.

Daily and weekly runs read the whole date range in one query. Intraday runs read each
regular session separately (at most MAX_INTRADAY_SESSIONS), because aggregated bars are
built per session. In bar-count mode a run takes the first `bar_count` bars (at most
MAX_BAR_COUNT) from the start date, reading sessions forward until it has them; the end
date then only bounds the search, so any past window works. Every input is pinned:
source, feed, adjustment, revisions, coverage, missing intervals, non-final bars and a
SHA-256 of the bars exactly as the engine got them.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from services.market_data.calendar import CalendarError
from services.market_data.daily import aggregate_weekly
from services.market_data.instruments import calendar_for
from services.market_data.provider import BarsQuery, BarsResult, InvalidBarsQueryError

MAX_INTRADAY_SESSIONS = 60
MAX_BAR_COUNT = 5_000  # also the Python indicator limit (ADR 0023)
MAX_COUNT_SESSIONS = 1_000  # bar-count mode: sessions read at most (5,000 60m bars ~ 715)
MAX_DAILY_DAYS = 366 * 30
MISSING_LIST_LIMIT = 50
ASOF = re.compile(r"asof-(\d{4}-\d{2}-\d{2})")

GetBars = Callable[[BarsQuery], BarsResult]
#: Adjusted daily bars: (a "1d" query, "split_adjusted" | "fully_adjusted").
GetAdjusted = Callable[[BarsQuery, str], BarsResult]
ADJUSTMENTS = ("raw", "split_adjusted", "fully_adjusted")


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
    # The same warnings as codes, so a screen can word them in its own language.
    warning_details: list[dict[str, Any]] = field(default_factory=list)


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


def _bars(count: int, noun: str) -> str:
    """'1 bar is', '3 bars are'."""
    return f"{count} {noun} is" if count == 1 else f"{count} {noun}s are"


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
    adjustment: str = "raw",
    get_adjusted: GetAdjusted | None = None,
    bar_count: int | None = None,
) -> InstrumentInput:
    if adjustment not in ADJUSTMENTS:
        raise InvalidBarsQueryError("adjustment must be raw, split_adjusted or fully_adjusted")
    if adjustment != "raw" and timeframe not in ("1d", "1w"):
        raise InvalidBarsQueryError("adjusted prices are available for daily and weekly runs")
    if end < start:
        raise InvalidBarsQueryError("end_date must not be before start_date")
    if bar_count is not None and not 1 <= bar_count <= MAX_BAR_COUNT:
        raise InvalidBarsQueryError(f"bar_count must be 1 to {MAX_BAR_COUNT}")
    if (end - start).days > MAX_DAILY_DAYS:
        raise InvalidBarsQueryError("a backtest may span at most 30 years")
    calendar = calendar_for(instrument_id)
    windows = _sessions(instrument_id, start, end)
    if not windows:
        raise InvalidBarsQueryError(f"{instrument_id} has no trading session in the range")
    if timeframe in ("1d", "1w"):
        # Whole days, so both regular-session and whole-day (08:00-20:00 KST) bars fit.
        ranges = [(windows[0].pre_open_utc, windows[-1].post_close_utc)]
    elif bar_count is not None:
        ranges = [(w.regular_open_utc, w.regular_close_utc) for w in windows]
    else:
        if len(windows) > MAX_INTRADAY_SESSIONS:
            raise InvalidBarsQueryError(
                f"intraday backtests may span at most {MAX_INTRADAY_SESSIONS} sessions"
            )
        ranges = [(w.regular_open_utc, w.regular_close_utc) for w in windows]

    documents: list[dict[str, Any]] = []
    coverages: list[dict[str, Any]] = []
    for index, (range_start, range_end) in enumerate(ranges):
        if bar_count is not None and len(documents) >= bar_count:
            break
        if bar_count is not None and index >= MAX_COUNT_SESSIONS:
            break
        query = BarsQuery(instrument_id, timeframe, range_start, range_end, as_of_utc=as_of_utc)
        if adjustment == "raw":
            result = get_bars(query)
        else:
            if get_adjusted is None:
                raise InvalidBarsQueryError("adjusted prices are not available here")
            result = get_adjusted(replace(query, timeframe="1d"), adjustment)
            if timeframe == "1w":
                result = aggregate_weekly(query, result, calendar)
        documents.extend(result.bars)
        coverages.append(result.coverage)

    documents.sort(key=lambda bar: str(bar["interval_start_utc"]))
    missing = [gap for coverage in coverages for gap in coverage["missing_intervals"]]
    expected_count = sum(int(c["expected_interval_count"]) for c in coverages)
    requested_end = ranges[len(coverages) - 1][1]
    if bar_count is not None and len(documents) > bar_count:
        # Keep the first bar_count bars; what lay after the last kept bar was not used.
        documents = documents[:bar_count]
        cut = str(documents[-1]["interval_end_utc"])
        missing = [gap for gap in missing if str(gap["start_utc"]) < cut]
        expected_count = len(documents) + len(missing)
        requested_end = datetime.fromisoformat(cut.replace("Z", "+00:00"))
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
        "requested_end_utc": _utc(requested_end),
        "as_of_utc": None if as_of_utc is None else _utc(as_of_utc),
        "source_ids": sorted({str(c["source_id"]) for c in coverages}),
        "feed_scopes": sorted({str(c["source_feed_scope"]) for c in coverages}),
        "adjustments": adjustments,
        "dataset_revisions": sorted({str(c["dataset_revision"]) for c in coverages}),
        "bar_count": len(bars),
        "expected_interval_count": expected_count,
        "missing_interval_count": len(missing),
        "missing_intervals": missing[:MISSING_LIST_LIMIT],
        "non_final_bar_count": non_final,
        "bars_sha256": digest,
        "adjustment_requested": adjustment,
    }
    if bar_count is not None:
        pin["bar_count_requested"] = bar_count
    warnings: list[str] = []
    details: list[dict[str, Any]] = []

    def warn(
        code: str, text: str, count: int | None = None, dates: list[str] | None = None
    ) -> None:
        warnings.append(f"{instrument_id}: {text}")
        details.append(
            {
                "code": code,
                "instrument_id": instrument_id,
                "count": count,
                "dates": dates or [],
            }
        )

    if missing:
        warn(
            "missing_intervals",
            f"{_bars(len(missing), 'expected interval')} have no bar",
            len(missing),
        )
    if non_final:
        warn(
            "non_final_bars", f"{_bars(non_final, 'bar')} not final and give no signals", non_final
        )
    splits = suspected_splits(bars) if "raw" in adjustments else []
    pin["suspected_split_dates"] = splits
    if "raw" in adjustments:
        warn(
            "raw_prices",
            "prices are not adjusted; a split or dividend in the range shows up as a price jump",
        )
    if splits:
        warn(
            "suspected_splits",
            f"the price jumps by about a whole split ratio on {', '.join(splits[:5])}; "
            "results around those dates are likely wrong",
            dates=splits[:5],
        )
    if adjustment != "raw":
        fetched = sorted(
            {
                m.group(1)
                for c in coverages
                if (m := ASOF.search(str(c["dataset_revision"]))) is not None
            }
        )
        warn(
            "adjusted_prices",
            f"prices are adjusted with the corporate actions known on {', '.join(fetched)}; "
            "price levels before an action differ from what actually traded",
            dates=fetched,
        )
    if not bars:
        warn("no_bars", "no bars in the range")
    elif bar_count is not None and len(bars) < bar_count:
        warn(
            "fewer_bars",
            f"only {len(bars)} of the {bar_count} requested bars exist between the start "
            "date and the end of the search",
            len(bars),
        )
    return InstrumentInput(instrument_id, str(calendar.zone), bars, pin, warnings, details)
