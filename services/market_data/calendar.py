from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from functools import cache
from typing import Any, Literal
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
from exchange_calendars.errors import DateOutOfBounds

SessionName = Literal["pre", "regular", "post", "closed", "unknown"]
TradingSessionName = Literal["pre", "regular", "post"]
NEW_YORK = ZoneInfo("America/New_York")
# Fixed bounds keep the supported range and the schedule fingerprint independent of
# the date the process happens to start on.
CALENDAR_FIRST_SESSION = date(2000, 1, 3)
CALENDAR_LAST_SESSION = date(2030, 12, 31)
EXTENDED_HOURS_POLICY_VERSION = "us-equity-extended-hours-v1-2026-10-12"


class CalendarError(ValueError):
    """The requested time cannot be represented by the configured calendar policy."""


def _utc_text(value: Any) -> str:
    converted = value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
    if not isinstance(converted, datetime) or converted.utcoffset() != timedelta(0):
        raise CalendarError("calendar returned a non-UTC session boundary")
    return converted.isoformat(timespec="seconds").replace("+00:00", "Z")


@cache
def _load_calendar(calendar_id: str) -> tuple[Any, str]:
    """Load one exchange calendar per process and fingerprint its regular schedule.

    The version changes only when a session date, open or close inside the fixed
    bounds changes, not when the library version changes without schedule changes.
    """
    calendar: Any = xcals.get_calendar(
        calendar_id,
        start=CALENDAR_FIRST_SESSION.isoformat(),
        end=CALENDAR_LAST_SESSION.isoformat(),
    )
    schedule = calendar.schedule
    lines = [
        f"{session.date().isoformat()},{_utc_text(market_open)},{_utc_text(market_close)}"
        for session, market_open, market_close in zip(
            schedule.index, schedule["open"], schedule["close"], strict=True
        )
    ]
    digest = hashlib.sha256("\n".join(lines).encode()).hexdigest()
    version = (
        f"xnys-regular-{CALENDAR_FIRST_SESSION.isoformat()}-{CALENDAR_LAST_SESSION.isoformat()}"
        f"-sha256-{digest[:16]}"
    )
    return calendar, version


def us_equity_calendar_version() -> str:
    return _load_calendar("XNAS")[1]


@dataclass(frozen=True)
class SessionWindow:
    calendar_id: str
    canonical_calendar_id: str
    session_date: date
    pre_open_utc: datetime
    regular_open_utc: datetime
    regular_close_utc: datetime
    post_close_utc: datetime
    is_early_close: bool
    calendar_version: str
    extended_hours_policy_version: str

    def bounds(self, session: TradingSessionName) -> tuple[datetime, datetime]:
        if session == "pre":
            return self.pre_open_utc, self.regular_open_utc
        if session == "regular":
            return self.regular_open_utc, self.regular_close_utc
        return self.regular_close_utc, self.post_close_utc

    def expected_minutes(self, session: TradingSessionName) -> int:
        start, end = self.bounds(session)
        return int((end - start).total_seconds() // 60)


@cache
def _window_for_date(calendar_id: str, session_date: date) -> SessionWindow | None:
    """Session windows per (calendar, date); the schedule is fixed for the process."""
    return UsEquityCalendar(calendar_id)._compute_window(session_date)


class UsEquityCalendar:
    """US equity schedule with explicit versioned extended-hours policy.

    `exchange_calendars` currently resolves XNAS to its canonical XNYS calendar.
    Regular sessions and holidays come from that calendar. Extended sessions are
    a separate application policy and are not treated as provider entitlement.
    """

    def __init__(self, calendar_id: str = "XNAS") -> None:
        if calendar_id not in {"XNAS", "XNYS"}:
            raise CalendarError("Phase 1C supports only XNAS/XNYS US equity calendars")
        self.calendar_id = calendar_id
        self.canonical_calendar_id = "XNYS"
        self.extended_hours_policy_version = EXTENDED_HOURS_POLICY_VERSION
        self._calendar, self.calendar_version = _load_calendar(calendar_id)

    def window_for_date(self, session_date: date) -> SessionWindow | None:
        # Bar paths look a window up once per minute; the pandas lookups behind it hold
        # the GIL, so concurrent requests would otherwise serialize on this CPU work.
        return _window_for_date(self.calendar_id, session_date)

    def _compute_window(self, session_date: date) -> SessionWindow | None:
        label = session_date.isoformat()
        try:
            is_session = self._calendar.is_session(label)
        except DateOutOfBounds as exc:
            raise CalendarError(
                f"{label} is outside the supported exchange calendar range "
                f"{CALENDAR_FIRST_SESSION.isoformat()}..{CALENDAR_LAST_SESSION.isoformat()}"
            ) from exc
        if not is_session:
            return None
        regular_open = self._as_datetime(self._calendar.session_open(label))
        regular_close = self._as_datetime(self._calendar.session_close(label))
        local_close = regular_close.astimezone(NEW_YORK)
        is_early_close = local_close.time() < time(16, 0)
        pre_open = datetime.combine(session_date, time(4, 0), NEW_YORK).astimezone(timezone.utc)
        post_close_time = time(17, 0) if is_early_close else time(20, 0)
        post_close = datetime.combine(session_date, post_close_time, NEW_YORK).astimezone(
            timezone.utc
        )
        return SessionWindow(
            calendar_id=self.calendar_id,
            canonical_calendar_id=self.canonical_calendar_id,
            session_date=session_date,
            pre_open_utc=pre_open,
            regular_open_utc=regular_open,
            regular_close_utc=regular_close,
            post_close_utc=post_close,
            is_early_close=is_early_close,
            calendar_version=self.calendar_version,
            extended_hours_policy_version=self.extended_hours_policy_version,
        )

    def session_window_document(self, session_date: date) -> dict[str, Any]:
        """Contract `session_window` for one exchange-local date (null bounds when closed)."""
        window = self.window_for_date(session_date)

        def text(value: datetime | None) -> str | None:
            return None if value is None else _utc_text(value)

        return {
            "kind": "session_window",
            "schema_version": "0.1",
            "calendar_id": self.calendar_id,
            "session_date": session_date.isoformat(),
            "is_trading_day": window is not None,
            "is_early_close": window.is_early_close if window else False,
            "pre_open_utc": text(window.pre_open_utc if window else None),
            "regular_open_utc": text(window.regular_open_utc if window else None),
            "regular_close_utc": text(window.regular_close_utc if window else None),
            "post_close_utc": text(window.post_close_utc if window else None),
            "calendar_version": self.calendar_version,
            "extended_hours_policy_version": self.extended_hours_policy_version,
        }

    def window_for_timestamp(self, timestamp_utc: datetime) -> SessionWindow | None:
        self._require_utc(timestamp_utc, "timestamp_utc")
        return self.window_for_date(timestamp_utc.astimezone(NEW_YORK).date())

    def classify_interval(self, start_utc: datetime, end_utc: datetime) -> SessionName:
        self._require_utc(start_utc, "start_utc")
        self._require_utc(end_utc, "end_utc")
        if end_utc <= start_utc:
            raise CalendarError("interval end must be after start")
        window = self.window_for_timestamp(start_utc)
        if window is None:
            return "closed"
        for session in ("pre", "regular", "post"):
            lower, upper = window.bounds(session)
            if lower <= start_utc and end_utc <= upper:
                return session
        for boundary in (
            window.pre_open_utc,
            window.regular_open_utc,
            window.regular_close_utc,
            window.post_close_utc,
        ):
            if start_utc < boundary < end_utc:
                return "unknown"
        return "closed"

    def trading_minutes(
        self, start_utc: datetime, end_utc: datetime
    ) -> tuple[tuple[datetime, TradingSessionName], ...]:
        """Start of every one-minute interval in [start, end) inside pre/regular/post."""
        self._require_utc(start_utc, "start_utc")
        self._require_utc(end_utc, "end_utc")
        minutes: list[tuple[datetime, TradingSessionName]] = []
        start = start_utc
        while start < end_utc:
            session = self.classify_interval(start, start + timedelta(minutes=1))
            if session in ("pre", "regular", "post"):
                minutes.append((start, session))
            start += timedelta(minutes=1)
        return tuple(minutes)

    @staticmethod
    def _as_datetime(value: object) -> datetime:
        method = getattr(value, "to_pydatetime", None)
        converted = method() if callable(method) else value
        if not isinstance(converted, datetime):
            raise CalendarError("calendar returned a non-datetime session boundary")
        if converted.utcoffset() != timedelta(0):
            raise CalendarError("calendar session boundary must be UTC")
        return converted.astimezone(timezone.utc)

    @staticmethod
    def _require_utc(value: datetime, name: str) -> None:
        if value.utcoffset() != timedelta(0):
            raise CalendarError(f"{name} must be UTC")
