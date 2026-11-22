"""The structure check reads daily bars from the Alpaca adapter's get_daily_bars."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from services.market_data.provider import BarsQuery, InvalidBarsQueryError
from services.screening.structure.service import daily_bars_from


def _bar(start: str, close: str, **extra: Any) -> dict[str, Any]:
    return {
        "interval_start_utc": start,
        "high": close,
        "low": close,
        "close": close,
        "is_final": True,
        "source_id": "alpaca-sip",
        "adjustment": "raw",
        **extra,
    }


class MinuteOnlyAdapter:
    """Like AlpacaMarketDataProvider: get_bars refuses anything but 1m."""

    def __init__(self) -> None:
        self.daily_queries: list[BarsQuery] = []

    def get_bars(self, query: BarsQuery) -> Any:
        raise InvalidBarsQueryError("the Alpaca adapter serves canonical 1m bars")

    def get_daily_bars(self, query: BarsQuery, adjustment: str = "raw") -> Any:
        self.daily_queries.append(query)
        return SimpleNamespace(
            bars=[
                _bar("2026-11-12T14:30:00Z", "1.00"),
                _bar("2026-11-13T14:30:00Z", "1.20"),
                _bar("2026-11-13T14:30:00Z", "9.99", is_final=False),
                _bar("2026-11-13T14:30:00Z", "9.99", adjustment="split_adjusted"),
            ]
        )


def test_daily_bars_come_from_get_daily_bars_not_the_minute_only_get_bars() -> None:
    adapter = MinuteOnlyAdapter()
    as_of = datetime(2026, 11, 14, 1, 0, tzinfo=timezone.utc)

    bars = daily_bars_from(adapter)("US-XNAS-MRLN", date(2026, 11, 12), date(2026, 11, 13), as_of)

    assert [(bar["date"], bar["close"]) for bar in bars] == [
        ("2026-11-12", Decimal("1.00")),
        ("2026-11-13", Decimal("1.20")),
    ]
    (query,) = adapter.daily_queries
    assert query.timeframe == "1d"
    assert query.as_of_utc == as_of


def test_an_adapter_without_get_daily_bars_still_uses_get_bars() -> None:
    seen: list[str] = []

    class BarsService:
        def get_bars(self, query: BarsQuery) -> Any:
            seen.append(query.timeframe)
            return SimpleNamespace(bars=[_bar("2026-11-13T14:30:00Z", "2.50")])

    bars = daily_bars_from(BarsService())(
        "US-XNAS-MRLN",
        date(2026, 11, 13),
        date(2026, 11, 13),
        datetime(2026, 11, 14, tzinfo=timezone.utc),
    )

    assert seen == ["1d"]
    assert [bar["close"] for bar in bars] == [Decimal("2.50")]
