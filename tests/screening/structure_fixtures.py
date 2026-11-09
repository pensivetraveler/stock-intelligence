"""Shared fixtures for the structure model tests (ADR 0053). No test reaches the network."""

from __future__ import annotations

import copy
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from services.market_data.calendar import UsEquityCalendar
from services.screening.structure.check import closed_sessions
from services.screening.structure.service import research_baseline
from services.screening.structure.sessions import canonical_sha256

CALENDAR = UsEquityCalendar("XNAS")
#: 10:00 ET Friday 2026-11-06; the 20 closed sessions before it end on Thursday.
CUTOFF = datetime(2026, 11, 6, 15, 0, tzinfo=timezone.utc)
NOW = CUTOFF + timedelta(minutes=30)


def windows(count: int = 20, known_until: datetime = CUTOFF) -> list[Any]:
    return closed_sessions(CALENDAR, known_until, count)


def session(
    day: date,
    levels: list[tuple[str, str, str]],
    last_price: str = "2.00",
    read_at: str | None = None,
) -> dict[str, Any]:
    window = CALENDAR.window_for_date(day)
    assert window is not None
    document: dict[str, Any] = {
        "symbol": "STAK",
        "session_date": day.isoformat(),
        "source_id": "alpaca-sip",
        "feed": "sip",
        "exchange_code": "D",
        "levels_version": "structure-session-levels-1",
        "condition_policy_version": "off-exchange-conditions-1",
        "range_utc": [
            window.pre_open_utc.isoformat().replace("+00:00", "Z"),
            window.post_close_utc.isoformat().replace("+00:00", "Z"),
        ],
        "read_at_utc": read_at or window.post_close_utc.isoformat().replace("+00:00", "Z"),
        "pages": 1,
        "trades": len(levels),
        "total_volume": str(sum(Decimal(o) + Decimal(lt) for _, o, lt in levels)),
        "off_exchange_volume": str(sum(Decimal(o) for _, o, _ in levels)),
        "off_market_price_volume": "0",
        "late_report_volume": "0",
        "removed_trades": 0,
        "last_price": last_price,
        "last_time_utc": window.post_close_utc.isoformat().replace("+00:00", "Z"),
        "levels": [list(level) for level in levels],
    }
    document["sha256"] = canonical_sha256(document)
    return document


def profile_sessions(count: int = 20) -> list[dict[str, Any]]:
    """Volume piles at 1.80 and 2.40 (around a 2.00 reference), thin in between."""
    found = []
    for index, window in enumerate(windows(count)):
        bump = str(100 + index)
        found.append(
            session(
                window.session_date,
                [
                    ("1.80", "5000", bump),
                    ("1.81", "4000", "300"),
                    ("1.86", "30", "20"),
                    ("1.90", "40", "20"),
                    ("1.95", "50", "40"),
                    ("2.00", "300", "200"),
                    ("2.04", "30", "10"),
                    ("2.10", "20", "10"),
                    ("2.20", "25", "10"),
                    ("2.30", "35", "10"),
                    ("2.40", "6000", "900"),
                    ("2.41", "3500", "400"),
                ],
            )
        )
    return found


def daily_bars(count: int = 25) -> list[dict[str, Any]]:
    bars = []
    for index, window in enumerate(windows(count)):
        close = Decimal("1.9") + Decimal(index % 5) / 20
        bars.append(
            {
                "date": window.session_date.isoformat(),
                "high": close + Decimal("0.1"),
                "low": close - Decimal("0.1"),
                "close": close,
            }
        )
    return bars


def reference(value: str = "2.00") -> dict[str, Any]:
    return {
        "value": value,
        "timestamp_utc": "2026-11-06T14:59:00Z",
        "session": "regular",
        "source": "alpaca-sip",
    }


PASS = {"status": "PASS", "splits": [], "reason": None}


def approved(evaluation_sha256: str = "a" * 64) -> dict[str, Any]:
    document = copy.deepcopy(research_baseline())
    document.update(
        {
            "parameter_set_id": "test-approved/2.0.0",
            "approval_status": "SHADOW_APPROVED",
            "training_window": ["2025-01-01", "2025-12-31"],
            "validation_window": ["2026-01-15", "2026-03-31"],
            "eligible_universe": "test universe",
            "selection_metric": "spearman",
            "approved_at": "2026-11-01T00:00:00Z",
            "band_cut_points": {"p50": "20", "p75": "40", "p90": "60"},
            "calibration_report_sha256": evaluation_sha256,
        }
    )
    return document


class FakeTrades:
    """`trade_pages` over trades keyed by the session date of `start` (New York)."""

    def __init__(self, by_day: dict[str, list[dict[str, Any]]]) -> None:
        self.by_day = by_day
        self.calls: list[tuple[str, str, str]] = []

    def __call__(
        self, symbol: str, start: datetime, end: datetime, sort: str = "asc"
    ) -> Iterator[list[dict[str, Any]]]:
        self.calls.append((symbol, start.isoformat(), sort))
        day = (start + timedelta(hours=12)).date().isoformat()
        trades = [
            t
            for t in self.by_day.get(day, [])
            if start.isoformat().replace("+00:00", "Z")
            <= t["t"]
            < end.isoformat().replace("+00:00", "Z")
        ]
        trades.sort(key=lambda t: str(t["t"]), reverse=sort == "desc")
        yield trades


def trade(t: str, p: str, s: int, x: str = "D", c: tuple[str, ...] = ("@",), **extra: Any) -> dict:
    return {"t": t, "p": Decimal(p), "s": s, "x": x, "c": list(c), "z": "C", **extra}
