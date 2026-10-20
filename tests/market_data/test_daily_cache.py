"""Daily bars cached per settled calendar year (no network)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from packages.contracts.validator import validate_document
from services.market_data.calendar import UsEquityCalendar
from services.market_data.daily import (
    DailySource,
    RawDay,
    build_daily_result,
    sessions_in_range,
    validate_daily_query,
)
from services.market_data.provider import BarsQuery, BarsResult
from services.market_data.storage import CachedMarketDataProvider, LocalMarketDataStore

AAPL = "us:aapl:xnas:2026"
# 2026-10-20 is a Tuesday; at 18:00Z its session is still trading.
NOW = datetime(2026, 10, 20, 18, 0, tzinfo=timezone.utc)
CALENDAR = UsEquityCalendar()


def utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


class FakeDailyProvider:
    """Serves one flat bar for every session from 2024 on, like a provider would."""

    source_id = "fake-daily"

    def __init__(self, revision: str = "fake-daily-r1") -> None:
        self.revision = revision
        self.queries: list[BarsQuery] = []

    def capabilities(self) -> dict[str, Any]:
        return {}

    def instruments(self) -> list[dict[str, Any]]:
        return []

    def get_bars(self, query: BarsQuery) -> BarsResult:
        raise AssertionError("minute bars are not used here")

    def get_daily_bars(self, query: BarsQuery) -> BarsResult:
        validate_daily_query(query, "1d", NOW)
        self.queries.append(query)
        price = Decimal("100.00")
        raw = {
            window.session_date: RawDay(
                window.session_date, price, price, price, price, Decimal(1000)
            )
            for window in sessions_in_range(CALENDAR, query.start_utc, query.end_utc)
            if window.session_date >= date(2024, 1, 1) and window.regular_open_utc < NOW
        }
        source = DailySource(self.source_id, "fake-sip", self.revision, "raw", 15, 15)
        return build_daily_result(query, raw, source, CALENDAR, NOW)


@pytest.fixture
def store(tmp_path: Path) -> LocalMarketDataStore:
    return LocalMarketDataStore(tmp_path / "data")


def daily_files(root: Path) -> list[str]:
    return sorted(path.relative_to(root).parts[4] for path in (root / "bars").rglob("*.parquet"))


QUERY = BarsQuery(AAPL, "1d", utc(2023, 6, 1), utc(2026, 10, 21))


def test_settled_years_are_stored_once_and_reused(store: LocalMarketDataStore) -> None:
    upstream = FakeDailyProvider()
    cached = CachedMarketDataProvider(upstream, store)

    first = cached.get_daily_bars(QUERY)
    validate_document(first.to_document())
    # One provider call for whole years; the current year is fetched but not stored.
    assert [(q.start_utc, q.end_utc) for q in upstream.queries] == [
        (utc(2023, 1, 1), utc(2027, 1, 1))
    ]
    assert daily_files(store.root) == ["year=2024", "year=2025"]  # 2023 had no bars

    second = cached.get_daily_bars(QUERY)
    assert [(q.start_utc, q.end_utc) for q in upstream.queries][1:] == [
        (utc(2026, 1, 1), utc(2027, 1, 1))
    ]
    assert second.bars == first.bars
    assert second.coverage["missing_intervals"] == first.coverage["missing_intervals"]
    # Only the requested range is returned: 2023 sessions before June are not listed.
    gaps = first.coverage["missing_intervals"]
    assert gaps[0]["start_utc"] == "2023-06-01T13:30:00Z"
    assert all(gap["reason"] == "provider_no_bar" for gap in gaps)
    assert first.bars[-1]["interval_start_utc"] == "2026-10-20T13:30:00Z"
    assert first.bars[-1]["is_final"] is False


def test_a_new_revision_is_never_mixed_with_stored_years(store: LocalMarketDataStore) -> None:
    CachedMarketDataProvider(FakeDailyProvider(), store).get_daily_bars(QUERY)

    revised = FakeDailyProvider("fake-daily-r2")
    result = CachedMarketDataProvider(revised, store).get_daily_bars(QUERY)

    assert result.coverage["dataset_revision"] == "fake-daily-r2"
    assert {bar["dataset_revision"] for bar in result.bars} == {"fake-daily-r2"}
    # The current year disagreed with the stored years, so the whole range was refetched.
    assert [(q.start_utc, q.end_utc) for q in revised.queries][-1] == (
        utc(2023, 1, 1),
        utc(2027, 1, 1),
    )


def test_point_in_time_requests_reuse_only_what_was_known(
    store: LocalMarketDataStore,
) -> None:
    upstream = FakeDailyProvider()
    cached = CachedMarketDataProvider(upstream, store)
    as_of = datetime(2025, 6, 2, 12, 0, tzinfo=timezone.utc)
    replay = BarsQuery(AAPL, "1d", utc(2024, 3, 1), utc(2025, 6, 3), as_of)

    first = cached.get_daily_bars(replay)
    assert first.bars[-1]["interval_start_utc"] == "2025-05-30T13:30:00Z"
    assert first.coverage["point_in_time_cutoff_utc"] == "2025-06-02T12:00:00Z"
    # 2024 was untouched by the cutoff and is stored; 2025 was cut off and is not.
    assert daily_files(store.root) == ["year=2024"]

    again = cached.get_daily_bars(replay)
    assert [q.start_utc for q in upstream.queries] == [utc(2024, 1, 1), utc(2025, 1, 1)]
    assert again.bars == first.bars


def test_providers_without_daily_bars_still_say_so(store: LocalMarketDataStore) -> None:
    class MinutesOnly(FakeDailyProvider):
        get_daily_bars = None  # type: ignore[assignment]

    from services.market_data.provider import InvalidBarsQueryError

    with pytest.raises(InvalidBarsQueryError):
        CachedMarketDataProvider(MinutesOnly(), store).get_daily_bars(QUERY)  # type: ignore[arg-type]
