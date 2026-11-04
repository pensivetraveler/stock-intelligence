"""A read-only quote for other local tools (ADR 0050): last price, today's volume and ADV.

`python -m services.market_data.quote quote --ticker TCRT [--as-of DATE] [--adv-days 20]`
prints one JSON document on stdout. It reads Alpaca market data through the first enabled
Alpaca key (Keys screen) and touches no order or account endpoint. Prices and volumes are
decimal strings; a missing value is null, never 0.

Exit codes: 0 answered, 2 usage, 3 no such ticker, 4 provider rate limit, 5 read failed.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

from services.credentials.store import SecretStorageError
from services.market_data.calendar import NEW_YORK, SessionWindow, UsEquityCalendar
from services.market_data.provider import (
    InvalidBarsQueryError,
    MarketDataError,
    ProviderRateLimitError,
)

SCHEMA = "siw_quote/0.1"
MAX_ADV_DAYS = 60
# Calendar days searched back for sessions: 60 sessions fit in well under 100 days.
SEARCH_DAYS = 120
TICKER = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")
CENT = Decimal("0.01")


class QuoteUsageError(ValueError):
    """Arguments that cannot be answered (exit 2)."""


class TickerNotFoundError(LookupError):
    """The provider has no bars for the ticker in the window (exit 3)."""


@dataclass(frozen=True)
class QuoteSource:
    """What the quote reads: bars of one ticker, the provider's splits and its labels."""

    bars: Callable[[str, str, datetime, datetime, str, int], list[dict[str, Any]]]
    splits: Callable[[str, date, date], list[Any]]
    provider: str
    feed: str
    delay_minutes: int


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _et(value: datetime) -> str:
    return value.astimezone(NEW_YORK).isoformat()


def _start(item: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(str(item["t"]).replace("Z", "+00:00"))


def _number(value: Decimal) -> str:
    return format(value.normalize(), "f") if value == value.to_integral() else format(value, "f")


def _sessions(calendar: Any, through: date, count: int) -> list[SessionWindow]:
    """The `count` sessions ending on `through` (inclusive), oldest first."""
    found: list[SessionWindow] = []
    day = through
    for _ in range(SEARCH_DAYS):
        window = calendar.window_for_date(day)
        if window is not None:
            found.append(window)
            if len(found) == count:
                return found[::-1]
        day -= timedelta(days=1)
    raise QuoteUsageError("the calendar has too few sessions before that date")


def build_quote(
    source: QuoteSource,
    ticker: str,
    *,
    as_of: date | None,
    adv_days: int,
    now: datetime,
) -> dict[str, Any]:
    if not 1 <= adv_days <= MAX_ADV_DAYS:
        raise QuoteUsageError(f"--adv-days must be 1-{MAX_ADV_DAYS}")
    calendar = UsEquityCalendar("XNAS")
    today = now.astimezone(NEW_YORK).date()
    if as_of is not None:
        window = calendar.window_for_date(as_of)
        if window is None:
            raise QuoteUsageError(f"{as_of} is not a US trading day")
        if window.regular_close_utc > now:
            raise QuoteUsageError(f"the {as_of} regular session has not closed yet")
        through = as_of
    else:
        # The latest regular session that has closed.
        through = today
        while True:
            window = calendar.window_for_date(through)
            if window is not None and window.regular_close_utc <= now:
                break
            through -= timedelta(days=1)
    sessions = _sessions(calendar, through, adv_days)

    # Split-adjusted to the retrieval date: every value here is on one share basis.
    first, last_session = sessions[0], sessions[-1]
    day_start = datetime.combine(first.session_date, time(0, 0), NEW_YORK)
    daily = source.bars(ticker, "1Day", day_start, last_session.regular_close_utc, "split", 4)
    daily_by_date = {_start(item).astimezone(NEW_YORK).date(): item for item in daily}
    minute_pages = adv_days * 1_000 // 10_000 + 3
    minutes = source.bars(
        ticker,
        "1Min",
        first.regular_open_utc,
        last_session.regular_close_utc,
        "split",
        minute_pages,
    )
    if not daily and not minutes:
        raise TickerNotFoundError(f"Alpaca has no bars for {ticker} in the ADV window")

    regular_volume: dict[date, Decimal] = {}
    by_date = {window.session_date: window for window in sessions}
    for item in minutes:
        start = _start(item)
        window = by_date.get(start.astimezone(NEW_YORK).date())
        if window is None or not window.regular_open_utc <= start < window.regular_close_utc:
            continue
        regular_volume[window.session_date] = regular_volume.get(
            window.session_date, Decimal(0)
        ) + Decimal(str(item["v"]))
    with_bars = [
        regular_volume[w.session_date] for w in sessions if w.session_date in regular_volume
    ]
    daily_volumes = [
        Decimal(str(daily_by_date[w.session_date]["v"]))
        for w in sessions
        if w.session_date in daily_by_date
    ]

    def average(values: list[Decimal], basis: str) -> dict[str, Any]:
        return {
            "days": adv_days,
            "basis": basis,
            "sessions_with_bars": len(values),
            "mean": _number((sum(values, Decimal(0)) / len(values)).quantize(CENT))
            if values
            else None,
            "median": _number(Decimal(statistics.median(values)).quantize(CENT))
            if values
            else None,
            "through_date": through.isoformat(),
        }

    close_bar = daily_by_date.get(through)
    splits_after = (
        source.splits(ticker, through + timedelta(days=1), today) if through < today else []
    )
    document: dict[str, Any] = {
        "schema": SCHEMA,
        "ticker": ticker,
        "provider": source.provider,
        "feed": source.feed,
        "delay_minutes": source.delay_minutes,
        "retrieved_at": _utc(now),
        "as_of": as_of.isoformat() if as_of is not None else None,
        "last": None,
        "regular_close": {
            "price": _number(Decimal(str(close_bar["c"]))) if close_bar else None,
            "date": through.isoformat(),
            "basis": "alpaca_1day_bar_close",
        },
        "today_volume": None,
        "adv": average(with_bars, "regular_session_1m_sum"),
        "adv_daily_bar": average(daily_volumes, "alpaca_1day_bar_volume"),
        "split_basis": {
            "adjustment": "split",
            "reflects_splits_through": today.isoformat(),
            "splits_after_through_date": [
                {"ex_date": action.on.isoformat(), "ratio": str(action.ratio)}
                for action in splits_after
            ],
        },
    }
    if as_of is None:
        document.update(_live_part(source, calendar, ticker, now, today))
    return document


def _live_part(
    source: QuoteSource, calendar: Any, ticker: str, now: datetime, today: date
) -> dict[str, Any]:
    """The latest 1m bar and today's volume by session, from the extended-hours day."""
    day = today
    window = calendar.window_for_date(day)
    while window is None or window.pre_open_utc > now:
        day -= timedelta(days=1)
        window = calendar.window_for_date(day)
    end = min(now, window.post_close_utc)
    bars = source.bars(ticker, "1Min", window.pre_open_utc, end, "split", 2)
    totals: dict[str, Decimal] = {}
    latest: tuple[datetime, dict[str, Any], str] | None = None
    for item in bars:
        start = _start(item)
        session = calendar.classify_interval(start, start + timedelta(minutes=1))
        if session not in ("pre", "regular", "post"):
            continue
        totals[session] = totals.get(session, Decimal(0)) + Decimal(str(item["v"]))
        if latest is None or start > latest[0]:
            latest = (start, item, session)
    last = None
    if latest is not None:
        last = {
            "price": _number(Decimal(str(latest[1]["c"]))),
            "at": _et(latest[0] + timedelta(minutes=1)),
            "session": latest[2],
            "basis": "1m_bar_close",
        }
    today_volume = None
    if day == today and totals:
        session = "regular" if now >= window.regular_open_utc else "pre"
        through = latest[0] + timedelta(minutes=1) if latest is not None else end
        today_volume = {
            "shares": _number(totals.get(session, Decimal(0))) if session in totals else None,
            "through": _et(min(through, window.bounds(session)[1])),
            "session": session,
            "by_session": {name: _number(value) for name, value in sorted(totals.items())},
        }
    return {"last": last, "today_volume": today_volume}


def _alpaca_source() -> QuoteSource:
    from services.api.main import _LazyBoats, _LazyCredentials

    adapter = _LazyBoats(_LazyCredentials(None).get).get()
    if adapter is None:
        raise MarketDataError("no enabled Alpaca key is registered (Keys screen, SIW_DATA_DIR)")
    config = adapter.config
    return QuoteSource(
        bars=adapter.stock_bars,
        splits=adapter.split_actions,
        provider=adapter.source_id,
        feed=config.feed,
        delay_minutes=config.publication_delay_minutes,
    )


def main(
    arguments: Sequence[str] | None = None,
    *,
    source: Callable[[], QuoteSource] = _alpaca_source,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> int:
    parser = argparse.ArgumentParser(prog="python -m services.market_data.quote")
    commands = parser.add_subparsers(dest="command", required=True)
    quote = commands.add_parser("quote", help="one ticker's quote as JSON")
    quote.add_argument("--ticker", required=True)
    quote.add_argument("--as-of", type=date.fromisoformat, default=None)
    quote.add_argument("--adv-days", type=int, default=20)
    try:
        args = parser.parse_args(arguments)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 2
    ticker = str(args.ticker).strip().upper()
    if not TICKER.fullmatch(ticker):
        print("usage: --ticker is 1-10 letters, digits or dots", file=sys.stderr)
        return 2
    try:
        document = build_quote(
            source(), ticker, as_of=args.as_of, adv_days=args.adv_days, now=now()
        )
    except QuoteUsageError as exc:
        print(f"usage: {exc}", file=sys.stderr)
        return 2
    except TickerNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except ProviderRateLimitError as exc:
        print(str(exc), file=sys.stderr)
        return 4
    except InvalidBarsQueryError as exc:
        print(f"Alpaca refused the query: {exc}", file=sys.stderr)
        return 3 if "symbol" in str(exc).lower() else 5
    except (MarketDataError, SecretStorageError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 5
    print(json.dumps(document, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
