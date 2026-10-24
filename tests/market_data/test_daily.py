"""Daily bars from Alpaca and weekly bars aggregated from them (no network)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import parse_qs

import httpx
import pytest

from packages.contracts.validator import validate_document
from services.market_data.alpaca import AlpacaConfig, AlpacaMarketDataProvider
from services.market_data.bars_service import MarketBarsService
from services.market_data.calendar import UsEquityCalendar
from services.market_data.daily import aggregate_weekly
from services.market_data.provider import BarsQuery

SPY = "us:spy:arcx:2026"
# Thanksgiving week 2026: 11-26 is a holiday and 11-27 closes early (18:00Z).
AFTER_WEEK = datetime(2026, 11, 30, 12, 0, tzinfo=timezone.utc)
WEEK = BarsQuery(
    SPY,
    "1d",
    datetime(2026, 11, 23, 0, 0, tzinfo=timezone.utc),
    datetime(2026, 11, 28, 0, 0, tzinfo=timezone.utc),
)


def day(date: str, o: str, h: str, low: str, c: str, v: int) -> str:
    # Alpaca stamps daily bars at midnight New York time (05:00Z in November).
    return f'{{"t":"{date}T05:00:00Z","o":{o},"h":{h},"l":{low},"c":{c},"v":{v}}}'


def page(*bars: str) -> str:
    return f'{{"bars":{{"SPY":[{",".join(bars)}]}},"next_page_token":null}}'


DAYS = [
    day("2026-11-23", "600.10", "605.00", "598.00", "604.00", 1000),
    day("2026-11-24", "604.00", "610.50", "603.00", "609.90", 2000),
    day("2026-11-25", "609.90", "612.00", "601.25", "602.00", 3000),
    day("2026-11-27", "602.00", "606.00", "600.00", "605.55", 500),
]


class Recorder:
    def __init__(self, text: str) -> None:
        self.text = text
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, text=self.text, headers={"Content-Type": "application/json"})


def alpaca(text: str, now: datetime = AFTER_WEEK) -> tuple[AlpacaMarketDataProvider, Recorder]:
    recorder = Recorder(text)
    adapter = AlpacaMarketDataProvider(
        AlpacaConfig(key_id="test-key-id", secret_key="test-secret-not-real"),
        transport=httpx.MockTransport(recorder),
        now=lambda: now,
    )
    return adapter, recorder


def test_daily_bars_cover_regular_sessions_with_exact_prices() -> None:
    adapter, recorder = alpaca(page(*DAYS))
    result = adapter.get_daily_bars(WEEK)

    validate_document(result.to_document())
    params = parse_qs(recorder.requests[0].url.query.decode())
    assert params["timeframe"] == ["1Day"]
    assert params["adjustment"] == ["raw"]
    assert [(bar["interval_start_utc"], bar["interval_end_utc"]) for bar in result.bars] == [
        ("2026-11-23T14:30:00Z", "2026-11-23T21:00:00Z"),
        ("2026-11-24T14:30:00Z", "2026-11-24T21:00:00Z"),
        ("2026-11-25T14:30:00Z", "2026-11-25T21:00:00Z"),
        ("2026-11-27T14:30:00Z", "2026-11-27T18:00:00Z"),  # early close
    ]
    first = result.bars[0]
    assert (first["open"], first["close"], first["volume"]) == ("600.10", "604.00", "1000")
    assert first["lineage"]["source_timeframe"] == "1d"
    assert all(bar["is_final"] and bar["session"] == "regular" for bar in result.bars)
    assert result.coverage["coverage_status"] == "complete"
    assert result.coverage["expected_interval_count"] == 4


def test_zero_volume_days_are_not_bars() -> None:
    # After a delisting Alpaca repeats the last price with no volume; no trade happened.
    flat = day("2026-11-27", "602.00", "602.00", "602.00", "602.00", 0)
    adapter, _ = alpaca(page(*DAYS[:3], flat))
    result = adapter.get_daily_bars(WEEK)

    validate_document(result.to_document())
    assert [bar["interval_start_utc"][:10] for bar in result.bars] == [
        "2026-11-23",
        "2026-11-24",
        "2026-11-25",
    ]
    (gap,) = result.coverage["missing_intervals"]
    assert (gap["start_utc"][:10], gap["reason"]) == ("2026-11-27", "provider_no_bar")


def test_daily_bars_respect_the_point_in_time_cutoff() -> None:
    adapter, _ = alpaca(page(*DAYS))
    # 11-25 closes at 21:00Z and is published 15 minutes later on the free SIP feed.
    cutoff = datetime(2026, 11, 25, 21, 5, tzinfo=timezone.utc)
    result = adapter.get_daily_bars(replace(WEEK, as_of_utc=cutoff))

    validate_document(result.to_document())
    assert [bar["interval_start_utc"][:10] for bar in result.bars] == ["2026-11-23", "2026-11-24"]
    assert {gap["reason"] for gap in result.coverage["missing_intervals"]} == {
        "not_available_as_of_cutoff"
    }


def test_today_is_provisional_and_later_sessions_are_not_yet_available() -> None:
    during_tuesday = datetime(2026, 11, 24, 18, 0, tzinfo=timezone.utc)
    adapter, recorder = alpaca(page(*DAYS[:2]), now=during_tuesday)
    result = adapter.get_daily_bars(WEEK)

    validate_document(result.to_document())
    # The request ends at "now minus the feed delay", never at the future close.
    params = parse_qs(recorder.requests[0].url.query.decode())
    assert params["end"] == ["2026-11-24T17:45:00Z"]
    today = result.bars[-1]
    assert (today["interval_start_utc"][:10], today["is_final"], today["quality"]) == (
        "2026-11-24",
        False,
        "provisional",
    )
    assert [gap["reason"] for gap in result.coverage["missing_intervals"]] == [
        "outside_entitlement",
        "outside_entitlement",
    ]


def test_weekly_bars_aggregate_daily_bars_of_one_exchange_week() -> None:
    adapter, _ = alpaca(page(*DAYS))
    weekly_query = replace(WEEK, timeframe="1w")
    result = MarketBarsService(adapter).get_bars(weekly_query)

    validate_document(result.to_document())
    (week,) = result.bars
    assert (week["interval_start_utc"], week["interval_end_utc"]) == (
        "2026-11-23T14:30:00Z",
        "2026-11-27T18:00:00Z",
    )
    assert (week["open"], week["high"], week["low"], week["close"]) == (
        "600.10",
        "612.00",
        "598.00",
        "605.55",
    )
    assert Decimal(week["volume"]) == 6500
    assert week["lineage"]["kind"] == "aggregated_1d"
    assert (
        week["lineage"]["expected_source_bar_count"],
        week["lineage"]["observed_source_bar_count"],
    ) == (4, 4)
    assert week["quality"] == "complete"
    assert ":agg:1w:" in week["dataset_revision"]


def test_a_missing_day_makes_the_week_partial() -> None:
    adapter, _ = alpaca(page(DAYS[0], DAYS[1], DAYS[3]))
    daily = adapter.get_daily_bars(WEEK)
    result = aggregate_weekly(replace(WEEK, timeframe="1w"), daily, UsEquityCalendar("XNAS"))

    validate_document(result.to_document())
    (week,) = result.bars
    assert (week["quality"], week["is_final"]) == ("partial", False)
    assert week["lineage"]["observed_source_bar_count"] == 3


@pytest.mark.parametrize("timeframe", ["1d", "1w"])
def test_daily_and_weekly_reach_the_api_contract(timeframe: str) -> None:
    adapter, _ = alpaca(page(*DAYS))
    result = MarketBarsService(adapter).get_bars(replace(WEEK, timeframe=timeframe))
    assert result.coverage["timeframe"] == timeframe
    validate_document(result.to_document())


@pytest.mark.parametrize(
    ("adjustment", "alpaca_value"), [("split_adjusted", "split"), ("fully_adjusted", "all")]
)
def test_adjusted_daily_bars_are_labelled_and_dated(adjustment: str, alpaca_value: str) -> None:
    adapter, recorder = alpaca(page(*DAYS))
    result = adapter.get_adjusted_daily_bars(WEEK, adjustment)

    validate_document(result.to_document())
    params = parse_qs(recorder.requests[0].url.query.decode())
    assert params["adjustment"] == [alpaca_value]
    assert result.coverage["adjustment"] == adjustment
    assert all(bar["adjustment"] == adjustment for bar in result.bars)
    # Adjusted history changes with later corporate actions, so the fetch date is named.
    assert result.coverage["dataset_revision"].endswith(f":1d:{alpaca_value}:asof-2026-11-30")
    with pytest.raises(Exception, match="split_adjusted or fully_adjusted"):
        adapter.get_adjusted_daily_bars(WEEK, "raw")


def test_adjusted_daily_bars_bypass_the_daily_cache(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from services.market_data.storage import CachedMarketDataProvider, LocalMarketDataStore

    adapter, recorder = alpaca(page(*DAYS))
    cached = CachedMarketDataProvider(adapter, LocalMarketDataStore(tmp_path))
    first = cached.get_adjusted_daily_bars(WEEK, "split_adjusted")
    second = cached.get_adjusted_daily_bars(WEEK, "split_adjusted")
    assert first.bars == second.bars
    assert len(recorder.requests) == 2  # fetched each time, never served from the cache


def test_korean_listings_offer_no_adjusted_bars() -> None:
    from services.market_data.market_router import MarketRouter
    from services.market_data.provider import InvalidBarsQueryError

    adapter, _ = alpaca(page(*DAYS))
    korean = replace(WEEK, instrument_id="kr:005930:xkrx:2026")

    class NoAdjusted:
        source_id = "toss-kr"

    router = MarketRouter(adapter, lambda: NoAdjusted())  # type: ignore[arg-type,return-value]
    with pytest.raises(InvalidBarsQueryError, match="not available for kr:005930"):
        router.get_adjusted_daily_bars(korean, "split_adjusted")


def test_weekly_bars_keep_the_adjustment_of_their_daily_bars() -> None:
    adapter, _ = alpaca(page(*DAYS))
    daily = adapter.get_adjusted_daily_bars(WEEK, "split_adjusted")
    weekly = aggregate_weekly(replace(WEEK, timeframe="1w"), daily, UsEquityCalendar())

    validate_document(weekly.to_document())
    assert weekly.coverage["adjustment"] == "split_adjusted"
    assert [bar["adjustment"] for bar in weekly.bars] == ["split_adjusted"]
    assert "asof-2026-11-30" in weekly.coverage["dataset_revision"]
