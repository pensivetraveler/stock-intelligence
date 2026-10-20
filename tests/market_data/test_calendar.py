from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from services.market_data.calendar import (
    CALENDAR_FIRST_SESSION,
    CALENDAR_LAST_SESSION,
    CalendarError,
    UsEquityCalendar,
    us_equity_calendar_version,
)
from services.market_data.fake import DEFAULT_FIXTURE_PATH


def utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def test_dst_changes_utc_boundaries_without_changing_local_regular_hours() -> None:
    calendar = UsEquityCalendar("XNAS")

    before_dst = calendar.window_for_date(date(2026, 3, 6))
    after_dst = calendar.window_for_date(date(2026, 3, 9))

    assert before_dst is not None
    assert after_dst is not None
    assert before_dst.regular_open_utc == utc(2026, 3, 6, 14, 30)
    assert before_dst.regular_close_utc == utc(2026, 3, 6, 21)
    assert after_dst.regular_open_utc == utc(2026, 3, 9, 13, 30)
    assert after_dst.regular_close_utc == utc(2026, 3, 9, 20)
    assert before_dst.expected_minutes("regular") == 390
    assert after_dst.expected_minutes("regular") == 390


def test_2026_holiday_and_early_close_schedule() -> None:
    calendar = UsEquityCalendar("XNAS")

    assert calendar.window_for_date(date(2026, 11, 26)) is None
    early = calendar.window_for_date(date(2026, 11, 27))

    assert early is not None
    assert early.is_early_close
    assert early.regular_open_utc == utc(2026, 11, 27, 14, 30)
    assert early.regular_close_utc == utc(2026, 11, 27, 18)
    assert early.post_close_utc == utc(2026, 11, 27, 22)
    assert early.expected_minutes("regular") == 210
    assert early.expected_minutes("post") == 240


def test_session_classification_is_boundary_explicit() -> None:
    calendar = UsEquityCalendar("XNAS")

    assert calendar.classify_interval(utc(2026, 10, 9, 12), utc(2026, 10, 9, 12, 1)) == "pre"
    assert (
        calendar.classify_interval(utc(2026, 10, 9, 13, 30), utc(2026, 10, 9, 13, 31)) == "regular"
    )
    assert calendar.classify_interval(utc(2026, 10, 9, 20, 30), utc(2026, 10, 9, 20, 31)) == "post"
    assert calendar.classify_interval(utc(2026, 10, 9, 5), utc(2026, 10, 9, 5, 1)) == "closed"
    assert calendar.classify_interval(utc(2026, 11, 26, 15), utc(2026, 11, 26, 15, 1)) == "closed"
    assert (
        calendar.classify_interval(
            utc(2026, 10, 9, 13, 29) + timedelta(seconds=30),
            utc(2026, 10, 9, 13, 30) + timedelta(seconds=30),
        )
        == "unknown"
    )


def test_session_windows_are_computed_once_per_calendar_and_date() -> None:
    # Bar paths look a window up per minute; a fresh lookup each time made concurrent
    # bar requests contend for the GIL.
    first = UsEquityCalendar("XNAS").window_for_date(date(2026, 10, 16))
    again = UsEquityCalendar("XNAS").window_for_date(date(2026, 10, 16))
    other = UsEquityCalendar("XNYS").window_for_date(date(2026, 10, 16))

    assert first is not None and first is again
    assert other is not None and other.calendar_id == "XNYS"
    assert UsEquityCalendar("XNAS").window_for_date(date(2026, 11, 26)) is None


def test_calendar_rejects_naive_time_and_unsupported_calendar() -> None:
    calendar = UsEquityCalendar("XNAS")

    with pytest.raises(CalendarError, match="must be UTC"):
        calendar.classify_interval(datetime(2026, 10, 9, 13, 30), datetime(2026, 10, 9, 13, 31))
    with pytest.raises(CalendarError, match="unsupported calendar"):
        UsEquityCalendar("XLON")


def test_dates_outside_loaded_calendar_range_raise_calendar_error() -> None:
    calendar = UsEquityCalendar("XNAS")

    with pytest.raises(CalendarError, match="outside the supported exchange calendar range"):
        calendar.window_for_date(date(1990, 1, 3))
    with pytest.raises(CalendarError, match="outside the supported exchange calendar range"):
        calendar.classify_interval(utc(2099, 1, 2, 15), utc(2099, 1, 2, 15, 1))


def test_supported_calendar_range_is_fixed_and_inclusive() -> None:
    calendar = UsEquityCalendar("XNAS")

    assert date(2000, 1, 3) == CALENDAR_FIRST_SESSION
    assert date(2030, 12, 31) == CALENDAR_LAST_SESSION
    assert calendar.window_for_date(CALENDAR_FIRST_SESSION) is not None
    assert calendar.window_for_date(CALENDAR_LAST_SESSION) is not None
    with pytest.raises(CalendarError):
        calendar.window_for_date(date(1999, 12, 31))
    with pytest.raises(CalendarError):
        calendar.window_for_date(date(2031, 1, 2))


def test_calendar_version_fingerprints_the_shared_regular_schedule() -> None:
    version = us_equity_calendar_version()

    assert version.startswith("xnys-regular-2000-01-03-2030-12-31-sha256-")
    assert UsEquityCalendar("XNAS").calendar_version == version
    assert UsEquityCalendar("XNYS").calendar_version == version


def test_fixture_lineage_matches_current_calendar_schedule() -> None:
    fixture = json.loads(DEFAULT_FIXTURE_PATH.read_text(encoding="utf-8"))
    versions = {bar["lineage"]["calendar_version"] for bar in fixture["bars"]}

    assert versions == {us_equity_calendar_version()}, (
        "The exchange calendar schedule changed. Review the schedule difference, "
        "then regenerate calendar_version in the test fixtures."
    )


def test_korean_sessions_add_nxt_hours_around_the_krx_regular_session() -> None:
    from services.market_data.calendar import ExchangeCalendar

    calendar = ExchangeCalendar("XKRX")
    assert calendar.market == "KR"
    assert calendar.calendar_version.startswith("xkrx-regular-")
    window = calendar.window_for_date(date(2026, 10, 20))
    assert window is not None
    # 08:00 / 09:00 / 15:30 / 20:00 KST.
    assert window.pre_open_utc == datetime(2026, 10, 19, 23, 0, tzinfo=timezone.utc)
    assert window.regular_open_utc == datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)
    assert window.regular_close_utc == datetime(2026, 10, 20, 6, 30, tzinfo=timezone.utc)
    assert window.post_close_utc == datetime(2026, 10, 20, 11, 0, tzinfo=timezone.utc)
    # The first session of the year opens an hour late; National Foundation Day is closed.
    late = calendar.window_for_date(date(2026, 1, 2))
    assert late is not None and late.regular_open_utc.hour == 1
    assert calendar.window_for_date(date(2026, 10, 3)) is None
    # KOSDAQ listings trade on the same schedule.
    assert ExchangeCalendar("XKOS").window_for_date(date(2026, 10, 20)) == window.__class__(
        **{**window.__dict__, "calendar_id": "XKOS"}
    )
    # A KST morning minute belongs to that Korean date, not the previous UTC/NY day.
    assert (
        calendar.classify_interval(
            datetime(2026, 10, 19, 23, 30, tzinfo=timezone.utc),
            datetime(2026, 10, 19, 23, 31, tzinfo=timezone.utc),
        )
        == "pre"
    )
