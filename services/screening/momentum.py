"""The momentum check (ADR 0055, U0): what the market had done when a symbol entered the
condition list.

A Kiwoom condition event carries no price. This check takes the 1m bars of the data
provider (Alpaca SIP by default) that had closed by the time SIW first saw the symbol, and
reports the last close, the change from the previous session's close and the traded volume
and an estimated dollar volume since the premarket open. The values are a label on the
candidate, not a filter. They come from a different source than the condition, which ran on
Kiwoom's own prices, and are kept apart from it.

They are rebuilt when the check runs, from bars fetched then. They describe the minutes
before the entry; they are not what SIW saw at the entry.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from services.backtest.data import GetBars
from services.market_data.calendar import SessionWindow, UsEquityCalendar
from services.market_data.provider import BarsQuery, InvalidBarsQueryError, MarketDataError
from services.screening.candidates import WatchedSymbol, utc_text

CHECK_VERSION = "momentum-1"
LABEL = "편입 직전 확정 1분봉"
DOLLAR_VOLUME_METHOD = "sum over 1m bars of (high + low + close) / 3 * volume"
# Reasons a minute is missing that a later run can fill.
UNSETTLED = frozenset({"outside_entitlement", "not_yet_final", "not_available_as_of_cutoff"})
MAX_DAYS_BACK = 10
CENT = Decimal("0.01")


def _previous_window(window: SessionWindow, calendar: UsEquityCalendar) -> SessionWindow | None:
    day = window.session_date
    for _ in range(MAX_DAYS_BACK):
        day -= timedelta(days=1)
        found = calendar.window_for_date(day)
        if found is not None:
            return found
    return None


def _previous_close(
    instrument_id: str, window: SessionWindow, get_bars: GetBars, calendar: UsEquityCalendar
) -> tuple[dict[str, Any] | None, str | None]:
    """The previous session's daily close as traded, or why there is none."""
    previous = _previous_window(window, calendar)
    if previous is None:
        return None, "no previous session in the last ten days"
    try:
        daily = get_bars(BarsQuery(instrument_id, "1d", previous.pre_open_utc, window.pre_open_utc))
    except MarketDataError as exc:
        return None, f"daily bars: {str(exc)[:200]}"
    # The range holds one session (sessions are picked by their regular open); a bar that
    # ends after today's premarket open would not be the previous session's.
    bars = [
        bar
        for bar in daily.bars
        if datetime.fromisoformat(str(bar["interval_end_utc"]).replace("Z", "+00:00"))
        <= window.pre_open_utc
    ]
    if not bars:
        return None, f"no daily bar for {previous.session_date.isoformat()}"
    last = bars[-1]
    return {
        "value": str(last["close"]),
        "session_date": previous.session_date.isoformat(),
        "adjustment": str(last.get("adjustment", "unknown")),
        "source_id": str(last.get("source_id", "")),
    }, None


def momentum_check(
    instrument_id: str,
    window: SessionWindow,
    seen: WatchedSymbol,
    get_bars: GetBars,
    calendar: UsEquityCalendar,
) -> dict[str, Any]:
    """`status`, `result`, `versions`, `warnings` and `sources` of one momentum check."""
    first_seen = datetime.fromisoformat(seen.first_seen_utc.replace("Z", "+00:00"))
    # Only minutes that had closed when SIW first saw the symbol.
    end = first_seen.replace(second=0, microsecond=0)
    result: dict[str, Any] = {
        "label": LABEL,
        "first_seen_utc": seen.first_seen_utc,
        "entry_time_known": seen.first_seen_how == "enter_event",
        "first_seen_how": seen.first_seen_how,
        "entered_after_utc": seen.entered_after_utc,
        "bars_until_utc": utc_text(end),
        "price": None,
        "previous_close": None,
        "change_percent": None,
        "volume": None,
        "dollar_volume_estimate": None,
        "dollar_volume_method": DOLLAR_VOLUME_METHOD,
        "bars_used": 0,
        "minutes_expected": 0,
        "missing_minutes": {},
        "missing": [],
    }
    outcome: dict[str, Any] = {
        "status": "ok",
        "result": result,
        "versions": {"check": CHECK_VERSION},
        "warnings": [
            "Rebuilt from bars fetched when the check ran: the market before the entry, "
            "not what SIW saw at the entry.",
            "The condition ran on Kiwoom's prices; these come from another source and can differ.",
        ],
        "sources": {},
    }
    if end <= window.pre_open_utc:
        outcome["status"] = "no_data"
        result["missing"].append("seen in the first minute of the premarket: no closed bar")
        return outcome
    try:
        minutes = get_bars(BarsQuery(instrument_id, "1m", window.pre_open_utc, end))
    except InvalidBarsQueryError as exc:
        outcome["status"] = "error"
        result["missing"].append(f"1m bars: {str(exc)[:200]}")
        return outcome
    coverage = minutes.coverage
    outcome["sources"]["bars"] = {
        key: coverage.get(key)
        for key in ("source_id", "source_feed_scope", "adjustment", "dataset_revision")
    }
    reasons = Counter(str(gap["reason"]) for gap in coverage.get("missing_intervals", []))
    result["missing_minutes"] = dict(sorted(reasons.items()))
    result["minutes_expected"] = int(coverage.get("expected_interval_count", 0))
    bars = [bar for bar in minutes.bars if bar.get("volume_unit") == "shares"]
    result["bars_used"] = len(bars)
    if not bars:
        outcome["status"] = "no_data"
        result["missing"].append("no 1m bar before the entry")
        return outcome
    last = bars[-1]
    volume = sum((Decimal(str(bar["volume"])) for bar in bars), Decimal(0))
    dollars = sum(
        (
            (Decimal(str(bar["high"])) + Decimal(str(bar["low"])) + Decimal(str(bar["close"])))
            / 3
            * Decimal(str(bar["volume"]))
            for bar in bars
        ),
        Decimal(0),
    )
    price = Decimal(str(last["close"]))
    result["price"] = {
        "value": str(last["close"]),
        "bar_end_utc": str(last["interval_end_utc"]),
        "session": str(last.get("session", "unknown")),
    }
    result["volume"] = format(volume.normalize(), "f")
    result["dollar_volume_estimate"] = format(dollars.quantize(CENT), "f")
    previous, why = _previous_close(instrument_id, window, get_bars, calendar)
    result["previous_close"] = previous
    if previous is None:
        result["missing"].append(f"previous close: {why}")
    else:
        base = Decimal(previous["value"])
        if base > 0:
            change = (price / base - 1) * 100
            result["change_percent"] = format(change.quantize(CENT), "f")
        if previous["adjustment"] == "raw":
            outcome["warnings"].append(
                "The previous close is as traded: a split or reverse split at today's open "
                "makes the change meaningless."
            )
    if any(reason in UNSETTLED for reason in reasons):
        # Some minutes may still arrive (entitlement delay, not final yet): run again later.
        outcome["status"] = "incomplete"
    return outcome
