"""Adjusted chart bars come from the provider's adjusted daily series, never as of a past time."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from services.market_data.daily import adjusted_bars
from services.market_data.provider import BarsQuery, BarsResult, InvalidBarsQueryError

UTC = timezone.utc


def query(timeframe: str = "1d", as_of: datetime | None = None) -> BarsQuery:
    return BarsQuery(
        instrument_id="us:aapl:xnas:2026",
        timeframe=timeframe,
        start_utc=datetime(2026, 10, 5, tzinfo=UTC),
        end_utc=datetime(2026, 10, 10, tzinfo=UTC),
        as_of_utc=as_of,
    )


class Adjusting:
    def __init__(self) -> None:
        self.asked: list[tuple[str, str]] = []

    def get_adjusted_daily_bars(self, q: BarsQuery, adjustment: str) -> BarsResult:
        self.asked.append((q.timeframe, adjustment))
        return BarsResult(bars=[], coverage={"adjustment": adjustment})  # type: ignore[arg-type]


def test_daily_bars_ask_the_provider_for_that_adjustment() -> None:
    provider = Adjusting()
    result: Any = adjusted_bars(provider, query(), "split_adjusted")
    assert provider.asked == [("1d", "split_adjusted")]
    assert result.coverage["adjustment"] == "split_adjusted"


def test_weekly_bars_are_built_from_adjusted_daily_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[str] = []
    monkeypatch.setattr(
        "services.market_data.daily.aggregate_weekly",
        lambda q, daily, _calendar: built.append(q.timeframe) or daily,
    )
    provider = Adjusting()
    adjusted_bars(provider, query("1w"), "split_adjusted")
    assert provider.asked == [("1d", "split_adjusted")]
    assert built == ["1w"]


@pytest.mark.parametrize(
    ("q", "provider", "message"),
    [
        (query("5m"), Adjusting(), "daily and weekly"),
        (query(as_of=datetime(2026, 10, 8, tzinfo=UTC)), Adjusting(), "as of a past time"),
        (query(), object(), "not available"),
    ],
)
def test_refusals(q: BarsQuery, provider: object, message: str) -> None:
    with pytest.raises(InvalidBarsQueryError, match=message):
        adjusted_bars(provider, q, "split_adjusted")
