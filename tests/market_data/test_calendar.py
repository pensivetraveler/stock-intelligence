from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from services.market_data.calendar import CalendarError, UsEquityCalendar


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


def test_calendar_rejects_naive_time_and_unsupported_calendar() -> None:
    calendar = UsEquityCalendar("XNAS")

    with pytest.raises(CalendarError, match="must be UTC"):
        calendar.classify_interval(datetime(2026, 10, 9, 13, 30), datetime(2026, 10, 9, 13, 31))
    with pytest.raises(CalendarError, match="only XNAS/XNYS"):
        UsEquityCalendar("XLON")
