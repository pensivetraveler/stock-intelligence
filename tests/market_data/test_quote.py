"""The quote command (ADR 0050) against a fake bar source; no test reaches the network."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction
from typing import Any

import pytest

from services.market_data.calendar import UsEquityCalendar
from services.market_data.filing_splits import ProviderSplit
from services.market_data.provider import ProviderAuthError, ProviderRateLimitError
from services.market_data.quote import QuoteSource, QuoteUsageError, build_quote, main

CALENDAR = UsEquityCalendar("XNAS")
# Wednesday 2026-11-04, 10:15 ET: the regular session is open.
NOW = datetime(2026, 11, 4, 15, 15, tzinfo=timezone.utc)


def _t(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeSource:
    """A bar every 30 minutes: 100 shares pre-market, 1,000 regular, 10 post; daily 5,000."""

    def __init__(self, *, empty: bool = False, splits: list[ProviderSplit] | None = None) -> None:
        self.empty = empty
        self.split_list = splits or []
        self.calls: list[tuple[str, str, datetime, datetime, str]] = []

    def bars(
        self, ticker: str, timeframe: str, start: datetime, end: datetime, adj: str, pages: int
    ) -> list[dict[str, Any]]:
        self.calls.append((ticker, timeframe, start, end, adj))
        if self.empty:
            return []
        rows: list[dict[str, Any]] = []
        day = start.date() - timedelta(days=1)
        while day <= end.date():
            window = CALENDAR.window_for_date(day)
            day += timedelta(days=1)
            if window is None:
                continue
            if timeframe == "1Day":
                d = window.session_date
                when = datetime(d.year, d.month, d.day, 5, tzinfo=timezone.utc)  # 00:00 or 01:00 ET
                if start <= when <= end:
                    rows.append({"t": _t(when), "c": f"{window.session_date.day}.25", "v": 5000})
                continue
            minute = window.pre_open_utc
            while minute < window.post_close_utc:
                if start <= minute < end:
                    session = CALENDAR.classify_interval(minute, minute + timedelta(minutes=1))
                    volume = {"pre": 100, "regular": 1000, "post": 10}[session]
                    rows.append({"t": _t(minute), "c": "2.50", "v": volume})
                minute += timedelta(minutes=30)
        return rows

    def splits(self, ticker: str, since: date, until: date) -> list[ProviderSplit]:
        return [item for item in self.split_list if since <= item.on <= until]

    def source(self) -> QuoteSource:
        return QuoteSource(self.bars, self.splits, "alpaca-sip", "sip", 0)


def test_live_quote_has_last_price_today_volume_and_adv() -> None:
    fake = FakeSource()
    document = build_quote(fake.source(), "TCRT", as_of=None, adv_days=20, now=NOW)
    assert document["regular_close"] == {
        "price": "3.25",
        "date": "2026-11-03",
        "basis": "alpaca_1day_bar_close",
    }
    # 13 half-hour samples of 1,000 shares in each regular session.
    assert document["adv"]["mean"] == "13000"
    assert document["adv"]["sessions_with_bars"] == 20
    assert document["adv"]["through_date"] == "2026-11-03"
    assert document["adv_daily_bar"]["median"] == "5000"
    assert document["last"]["session"] == "regular"
    assert document["last"]["at"] == "2026-11-04T10:01:00-05:00"
    today = document["today_volume"]
    assert today["session"] == "regular"
    assert today["shares"] == "2000"
    assert today["by_session"] == {"pre": "1100", "regular": "2000"}
    assert document["split_basis"]["reflects_splits_through"] == "2026-11-04"
    # Everything is read split-adjusted, so the shares line up with one basis.
    assert {call[4] for call in fake.calls} == {"split"}
    json.dumps(document)


def test_as_of_gives_that_close_and_no_live_part() -> None:
    split = ProviderSplit(date(2026, 10, 20), Fraction(1, 10))
    fake = FakeSource(splits=[split])
    document = build_quote(fake.source(), "TCRT", as_of=date(2026, 10, 9), adv_days=5, now=NOW)
    assert document["last"] is None
    assert document["today_volume"] is None
    assert document["regular_close"]["date"] == "2026-10-09"
    assert document["adv"]["through_date"] == "2026-10-09"
    assert document["split_basis"]["splits_after_through_date"] == [
        {"ex_date": "2026-10-20", "ratio": "1/10"}
    ]
    # No bar after the as-of session is read for the ADV.
    assert max(call[3] for call in fake.calls) <= datetime(2026, 10, 9, 20, tzinfo=timezone.utc)


def test_before_the_open_today_counts_pre_market() -> None:
    early = datetime(2026, 11, 4, 13, 0, tzinfo=timezone.utc)  # 08:00 ET
    document = build_quote(FakeSource().source(), "TCRT", as_of=None, adv_days=3, now=early)
    assert document["today_volume"]["session"] == "pre"
    assert document["last"]["session"] == "pre"


def test_on_a_weekend_last_is_friday_and_there_is_no_today_volume() -> None:
    saturday = datetime(2026, 11, 7, 15, 0, tzinfo=timezone.utc)
    document = build_quote(FakeSource().source(), "TCRT", as_of=None, adv_days=3, now=saturday)
    assert document["today_volume"] is None
    assert document["last"]["at"].startswith("2026-11-06T")
    assert document["last"]["session"] == "post"


@pytest.mark.parametrize(
    ("as_of", "days"),
    [(date(2026, 11, 1), 20), (date(2026, 11, 4), 20), (None, 0), (None, 61)],
)
def test_unanswerable_arguments_are_usage_errors(as_of: date | None, days: int) -> None:
    with pytest.raises(QuoteUsageError):
        build_quote(FakeSource().source(), "TCRT", as_of=as_of, adv_days=days, now=NOW)


def run(
    capsys: pytest.CaptureFixture[str], arguments: list[str], source: Any
) -> tuple[int, str, str]:
    code = main(arguments, source=source, now=lambda: NOW)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_exit_codes(capsys: pytest.CaptureFixture[str]) -> None:
    ok = FakeSource().source
    code, out, _ = run(capsys, ["quote", "--ticker", "tcrt"], ok)
    assert code == 0 and json.loads(out)["ticker"] == "TCRT"
    assert run(capsys, ["quote"], ok)[0] == 2
    assert run(capsys, ["quote", "--ticker", "bad ticker"], ok)[0] == 2
    assert run(capsys, ["quote", "--ticker", "TCRT", "--as-of", "2026-11-01"], ok)[0] == 2
    code, out, err = run(capsys, ["quote", "--ticker", "NONE"], FakeSource(empty=True).source)
    assert (code, out) == (3, "") and "no bars" in err

    def failing(error: Exception) -> Any:
        def source() -> QuoteSource:
            def bars(*_: Any) -> list[dict[str, Any]]:
                raise error

            return QuoteSource(bars, lambda *_: [], "alpaca-sip", "sip", 0)

        return source

    assert run(capsys, ["quote", "--ticker", "X"], failing(ProviderRateLimitError("slow")))[0] == 4
    code, out, err = run(capsys, ["quote", "--ticker", "X"], failing(ProviderAuthError("bad key")))
    assert (code, out) == (5, "") and "bad key" in err
