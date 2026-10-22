"""The US day market (overnight session) policy, ADR 0030.

The overnight session of US trading day D trades on Blue Ocean ATS from 20:00 New York
time on the previous calendar day to 04:00 on D, and belongs to D (Monday's starts on
Sunday evening). There is no session into a day that is not a trading day. This is an
application policy, separate from the exchange calendar's pre/regular/post sessions, and
its behaviour around holidays and early closes is assumed, not verified.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from services.market_data.calendar import NEW_YORK, ExchangeCalendar

OVERNIGHT_POLICY_VERSION = "us-overnight-boats-v1-2026-10-22"
OVERNIGHT_OPEN = time(20, 0)  # New York, on the previous calendar day
OVERNIGHT_CLOSE = time(4, 0)  # New York, on the session's trading day


def overnight_window(
    calendar: ExchangeCalendar, session_date: date
) -> tuple[datetime, datetime] | None:
    """[start, end) in UTC of the overnight session belonging to `session_date`."""
    if calendar.market != "US" or calendar.window_for_date(session_date) is None:
        return None
    start = datetime.combine(session_date - timedelta(days=1), OVERNIGHT_OPEN, NEW_YORK)
    end = datetime.combine(session_date, OVERNIGHT_CLOSE, NEW_YORK)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def overnight_session_of(calendar: ExchangeCalendar, moment: datetime) -> date | None:
    """The trading day whose overnight session holds `moment` (UTC), if any."""
    local = moment.astimezone(NEW_YORK)
    # 20:00-24:00 belongs to the next day's session, 00:00-04:00 to the same day's.
    candidate = local.date() + timedelta(days=1) if local.time() >= OVERNIGHT_OPEN else local.date()
    window = overnight_window(calendar, candidate)
    if window is not None and window[0] <= moment < window[1]:
        return candidate
    return None
