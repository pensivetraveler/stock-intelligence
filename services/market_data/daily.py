"""Daily bars from a provider and weekly bars aggregated from them.

A daily bar covers one trading session's regular hours: [regular open, regular close),
so early closes end early. A weekly bar runs from the first session's open to the last
session's close of a Monday-to-Friday exchange week and is derived from daily bars only
(never from minutes), keeping the daily revision as its source.

Providers that serve daily bars implement `DailyBarsProvider`; the rest are told so.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal, Protocol, runtime_checkable

from packages.contracts.validator import validate_document
from services.market_data.calendar import (
    CALENDAR_FIRST_SESSION,
    CALENDAR_LAST_SESSION,
    NEW_YORK,
    SessionWindow,
    UsEquityCalendar,
)
from services.market_data.provider import (
    BarsQuery,
    BarsResult,
    InvalidBarsQueryError,
    ProviderDataError,
    validate_bars_query,
)

#: The longest range a daily request may cover (about 40 years).
MAX_DAILY_RANGE = timedelta(days=366 * 40)


@runtime_checkable
class DailyBarsProvider(Protocol):
    def get_daily_bars(self, query: BarsQuery) -> BarsResult:
        """Daily bars (timeframe "1d") for sessions whose regular open is in the range."""
        ...


def utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate_daily_query(query: BarsQuery, timeframe: str, now: datetime) -> None:
    if query.timeframe != timeframe:
        raise InvalidBarsQueryError(f"expected a {timeframe} query")
    validate_bars_query(query)
    if query.end_utc - query.start_utc > MAX_DAILY_RANGE:
        raise InvalidBarsQueryError("daily range cannot exceed 40 years")
    if query.as_of_utc is not None and query.as_of_utc > now:
        raise InvalidBarsQueryError("as_of_utc cannot be in the future")


def sessions_in_range(
    calendar: UsEquityCalendar, start_utc: datetime, end_utc: datetime
) -> list[SessionWindow]:
    """Trading sessions whose regular open falls in [start_utc, end_utc), oldest first."""
    sessions: list[SessionWindow] = []
    # Only dates the calendar knows; a range may start or end just outside them.
    day = max((start_utc - timedelta(days=1)).date(), CALENDAR_FIRST_SESSION)
    last = min(end_utc.date(), CALENDAR_LAST_SESSION)
    while day <= last:
        window = calendar.window_for_date(day)
        if window is not None and start_utc <= window.regular_open_utc < end_utc:
            sessions.append(window)
        day += timedelta(days=1)
    return sessions


@dataclass(frozen=True)
class RawDay:
    session_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True)
class DailySource:
    source_id: str
    feed_scope: str
    dataset_revision: str
    adjustment: str
    publication_delay_minutes: int
    finality_minutes: int
    # What one daily bar spans: the regular session (US providers) or the whole trading
    # day with extended hours (Toss Korean candles, 08:00-20:00 KST, checked 2026-10-20).
    span: Literal["regular", "all_day"] = "regular"


def day_bounds(window: SessionWindow, span: str) -> tuple[datetime, datetime]:
    if span == "all_day":
        return window.pre_open_utc, window.post_close_utc
    return window.regular_open_utc, window.regular_close_utc


def sessions_for_span(
    calendar: UsEquityCalendar, start_utc: datetime, end_utc: datetime, span: str
) -> list[SessionWindow]:
    """Sessions whose whole bar (regular session or whole day) lies in [start_utc, end_utc).

    For regular-session bars this is the same as the regular open being in range; a
    whole-day span starts hours before the regular open and ends hours after the close.
    """
    margin = timedelta(days=1)
    candidates = sessions_in_range(calendar, start_utc - margin, end_utc + margin)
    return [
        window
        for window in candidates
        if start_utc <= day_bounds(window, span)[0] and day_bounds(window, span)[1] <= end_utc
    ]


def build_daily_result(
    query: BarsQuery,
    raw: dict[date, RawDay],
    source: DailySource,
    calendar: UsEquityCalendar,
    now: datetime,
) -> BarsResult:
    """Canonical daily bars plus coverage for every expected session."""
    sessions = sessions_for_span(calendar, query.start_utc, query.end_utc, source.span)
    delay = timedelta(minutes=source.publication_delay_minutes)
    final_before = now - timedelta(minutes=source.finality_minutes)
    visible: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    for window in sessions:
        start, end = day_bounds(window, source.span)
        day = raw.get(window.session_date)
        if start >= now:
            reason = "outside_entitlement"
        elif day is None:
            reason = "not_yet_final" if end > final_before else "provider_no_bar"
        else:
            final = end <= final_before
            # A session still in progress is only as recent as the feed allows.
            available_at = (end if final else min(now, end)) + delay
            if query.as_of_utc is not None and available_at > query.as_of_utc:
                reason = "not_available_as_of_cutoff"
            else:
                visible.append(
                    _daily_bar(
                        query.instrument_id, day, window, final, available_at, source, calendar
                    )
                )
                continue
        missing.append({"start_utc": utc_text(start), "end_utc": utc_text(end), "reason": reason})
    return assemble_result(
        query,
        "1d",
        visible,
        missing,
        len(sessions),
        source.source_id,
        source.feed_scope,
        source.adjustment,
        source.dataset_revision,
    )


def _daily_bar(
    instrument_id: str,
    day: RawDay,
    window: SessionWindow,
    final: bool,
    available_at: datetime,
    source: DailySource,
    calendar: UsEquityCalendar,
) -> dict[str, Any]:
    return {
        "kind": "price_bar",
        "schema_version": "0.1",
        "instrument_id": instrument_id,
        "timeframe": "1d",
        "interval_start_utc": utc_text(day_bounds(window, source.span)[0]),
        "interval_end_utc": utc_text(day_bounds(window, source.span)[1]),
        "open": format(day.open, "f"),
        "high": format(day.high, "f"),
        "low": format(day.low, "f"),
        "close": format(day.close, "f"),
        "volume": format(day.volume, "f"),
        "volume_unit": "shares",
        "session": source.span,
        "is_final": final,
        "quality": "complete" if final else "provisional",
        "lineage": {
            "kind": "provider",
            "source_timeframe": "1d",
            "expected_source_bar_count": 1,
            "observed_source_bar_count": 1,
            "source_dataset_revision": None,
            "calendar_id": calendar.calendar_id,
            "calendar_version": calendar.calendar_version,
        },
        "adjustment": source.adjustment,
        "source_id": source.source_id,
        "source_feed_scope": source.feed_scope,
        "dataset_revision": source.dataset_revision,
        "available_at_utc": utc_text(available_at),
    }


def aggregate_weekly(query: BarsQuery, daily: BarsResult, calendar: UsEquityCalendar) -> BarsResult:
    """Weekly bars from daily bars: one per exchange week that has a session in range."""
    span = "all_day" if any(bar["session"] == "all_day" for bar in daily.bars) else "regular"
    sessions = sessions_for_span(calendar, query.start_utc, query.end_utc, span)
    weeks: dict[tuple[int, int], list[SessionWindow]] = {}
    for window in sessions:
        weeks.setdefault(window.session_date.isocalendar()[:2], []).append(window)

    def key(window: SessionWindow) -> str:
        return utc_text(day_bounds(window, span)[0])

    by_start = {bar["interval_start_utc"]: bar for bar in daily.bars}
    reasons = {gap["start_utc"]: gap["reason"] for gap in daily.coverage["missing_intervals"]}
    revision = (
        f"{daily.coverage['dataset_revision']}:agg:1w:{calendar.extended_hours_policy_version}"
    )
    visible: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    for windows in weeks.values():
        start = day_bounds(windows[0], span)[0]
        end = day_bounds(windows[-1], span)[1]
        bars = [by_start[key(w)] for w in windows if key(w) in by_start]
        if not bars:
            reason = reasons.get(key(windows[0]), "provider_no_bar")
            missing.append(
                {"start_utc": utc_text(start), "end_utc": utc_text(end), "reason": reason}
            )
            continue
        complete = len(bars) == len(windows) and all(bar["is_final"] for bar in bars)
        visible.append(
            {
                "kind": "price_bar",
                "schema_version": "0.1",
                "instrument_id": query.instrument_id,
                "timeframe": "1w",
                "interval_start_utc": utc_text(start),
                "interval_end_utc": utc_text(end),
                "open": bars[0]["open"],
                "high": format(max(Decimal(bar["high"]) for bar in bars), "f"),
                "low": format(min(Decimal(bar["low"]) for bar in bars), "f"),
                "close": bars[-1]["close"],
                "volume": format(sum((Decimal(bar["volume"]) for bar in bars), Decimal(0)), "f"),
                "volume_unit": "shares",
                "session": span,
                "is_final": complete,
                "quality": "complete"
                if complete
                else ("partial" if len(bars) < len(windows) else "provisional"),
                "lineage": {
                    "kind": "aggregated_1d",
                    "source_timeframe": "1d",
                    "expected_source_bar_count": len(windows),
                    "observed_source_bar_count": len(bars),
                    "source_dataset_revision": daily.coverage["dataset_revision"],
                    "calendar_id": calendar.calendar_id,
                    "calendar_version": calendar.calendar_version,
                },
                "adjustment": daily.coverage["adjustment"],
                "source_id": daily.coverage["source_id"],
                "source_feed_scope": daily.coverage["source_feed_scope"],
                "dataset_revision": revision,
                "available_at_utc": max(bar["available_at_utc"] for bar in bars),
            }
        )
    coverage = daily.coverage
    return assemble_result(
        query,
        "1w",
        visible,
        missing,
        len(weeks),
        coverage["source_id"],
        coverage["source_feed_scope"],
        coverage["adjustment"],
        revision,
    )


def assemble_result(
    query: BarsQuery,
    timeframe: str,
    visible: list[dict[str, Any]],
    missing: list[dict[str, str]],
    expected: int,
    source_id: str,
    feed_scope: str,
    adjustment: str,
    revision: str,
) -> BarsResult:
    coverage = {
        "kind": "coverage_manifest",
        "schema_version": "0.1",
        "instrument_id": query.instrument_id,
        "timeframe": timeframe,
        "requested_start_utc": utc_text(query.start_utc),
        "requested_end_utc": utc_text(query.end_utc),
        "coverage_status": "complete"
        if visible and not missing
        else ("partial" if visible else "empty"),
        "verification_status": "unverified",
        "observed_interval_count": len(visible),
        "expected_interval_count": expected,
        "missing_intervals": missing,
        "source_id": source_id,
        "source_feed_scope": feed_scope,
        "adjustment": adjustment,
        "dataset_revision": revision,
        "point_in_time_cutoff_utc": utc_text(query.as_of_utc)
        if query.as_of_utc is not None
        else None,
    }
    result = BarsResult(tuple(visible), coverage)
    try:
        validate_document(result.to_document())
    except ValueError as exc:
        raise ProviderDataError(f"daily data violates the contract: {exc}") from exc
    return result


def session_date_of(timestamp: str) -> date:
    """The exchange date of a provider's daily timestamp (often midnight New York)."""
    return _parse(timestamp).astimezone(NEW_YORK).date()
