from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from packages.contracts.validator import validate_document
from services.market_data.bars_service import MarketBarsService
from services.market_data.fake import FakeMarketDataProvider
from services.market_data.provider import BarsQuery, InvalidBarsQueryError
from tests.conftest import SESSION_OPEN, SyntheticDocument

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)
INSTRUMENT_ID = "us:aapl:xnas:2026"


def service(document: dict[str, object]) -> MarketBarsService:
    return MarketBarsService(FakeMarketDataProvider(document, now=lambda: NOW))


def query(
    timeframe: str, minutes: int, *, start: datetime = SESSION_OPEN, **extra: object
) -> BarsQuery:
    return BarsQuery(
        INSTRUMENT_ID,
        timeframe,
        start,
        start + timedelta(minutes=minutes),
        **extra,  # type: ignore[arg-type]
    )


def test_complete_buckets_have_complete_coverage_and_derived_revision(
    synthetic_document: SyntheticDocument,
) -> None:
    result = service(synthetic_document(30)).get_bars(query("5m", 30))

    validate_document(result.to_document())
    assert [bar["interval_start_utc"][11:16] for bar in result.bars] == [
        "13:30",
        "13:35",
        "13:40",
        "13:45",
        "13:50",
        "13:55",
    ]
    assert all(bar["quality"] == "complete" for bar in result.bars)
    coverage = result.coverage
    assert (coverage["timeframe"], coverage["coverage_status"]) == ("5m", "complete")
    assert coverage["expected_interval_count"] == coverage["observed_interval_count"] == 6
    assert coverage["dataset_revision"] == result.bars[0]["dataset_revision"]
    assert coverage["dataset_revision"].startswith("fake-aapl-20261009-r1:agg:5m:")


def test_missing_bucket_and_partial_bucket_are_explicit(
    synthetic_document: SyntheticDocument,
) -> None:
    document = synthetic_document(15, skip={5, 6, 7, 8, 9, 12})

    result = service(document).get_bars(query("5m", 15))

    assert [bar["interval_start_utc"][11:16] for bar in result.bars] == ["13:30", "13:40"]
    assert result.bars[1]["quality"] == "partial"
    assert result.bars[1]["is_final"] is False
    assert result.coverage["coverage_status"] == "partial"
    assert result.coverage["missing_intervals"] == [
        {
            "start_utc": "2026-10-09T13:35:00Z",
            "end_utc": "2026-10-09T13:40:00Z",
            "reason": "fixture_missing",
        }
    ]


def test_cutoff_inside_bucket_yields_partial_bar_and_unpublished_buckets(
    synthetic_document: SyntheticDocument,
) -> None:
    cutoff = datetime(2026, 10, 9, 13, 33, 2, tzinfo=timezone.utc)

    result = service(synthetic_document(10)).get_bars(query("5m", 10, as_of_utc=cutoff))

    assert len(result.bars) == 1
    assert result.bars[0]["lineage"]["observed_source_bar_count"] == 3
    assert result.bars[0]["is_final"] is False
    assert result.coverage["point_in_time_cutoff_utc"] == "2026-10-09T13:33:02Z"
    assert [item["reason"] for item in result.coverage["missing_intervals"]] == [
        "not_available_as_of_cutoff"
    ]


def test_last_regular_hour_bucket_is_capped_at_the_close(
    synthetic_document: SyntheticDocument,
) -> None:
    result = service(synthetic_document(390)).get_bars(query("60m", 390))

    assert result.coverage["expected_interval_count"] == 7
    assert result.bars[-1]["interval_start_utc"] == "2026-10-09T19:30:00Z"
    assert result.bars[-1]["interval_end_utc"] == "2026-10-09T20:00:00Z"
    assert result.bars[-1]["quality"] == "complete"


@pytest.mark.parametrize(
    ("timeframe", "start_offset", "minutes"),
    [("5m", 1, 9), ("15m", 0, 20), ("30m", 15, 30)],
)
def test_range_must_align_to_session_buckets(
    synthetic_document: SyntheticDocument, timeframe: str, start_offset: int, minutes: int
) -> None:
    misaligned = query(timeframe, minutes, start=SESSION_OPEN + timedelta(minutes=start_offset))

    with pytest.raises(InvalidBarsQueryError, match="align to"):
        service(synthetic_document(60)).get_bars(misaligned)


def test_one_minute_queries_pass_through_unchanged(
    synthetic_document: SyntheticDocument,
) -> None:
    document = synthetic_document(10)
    direct = FakeMarketDataProvider(document, now=lambda: NOW).get_bars(query("1m", 10))

    assert service(document).get_bars(query("1m", 10)) == direct


def test_any_minute_timeframe_is_aggregated_by_session_buckets(
    synthetic_document: SyntheticDocument,
) -> None:
    three = service(synthetic_document(9)).get_bars(query("3m", 9))
    validate_document(three.to_document())
    assert [bar["interval_start_utc"][11:16] for bar in three.bars] == [
        "13:30",
        "13:33",
        "13:36",
    ]
    assert three.coverage["dataset_revision"].startswith("fake-aapl-20261009-r1:agg:3m:")

    # 120m over the regular session: 13:30, 15:30, 17:30, and 19:30 capped at the close.
    two_hours = service(synthetic_document(390)).get_bars(query("120m", 390))
    validate_document(two_hours.to_document())
    assert [
        (bar["interval_start_utc"][11:16], bar["interval_end_utc"][11:16]) for bar in two_hours.bars
    ] == [("13:30", "15:30"), ("15:30", "17:30"), ("17:30", "19:30"), ("19:30", "20:00")]


@pytest.mark.parametrize("timeframe", ["1h", "0m", "961m", "1d", "05m"])
def test_unsupported_timeframes_are_rejected(
    synthetic_document: SyntheticDocument, timeframe: str
) -> None:
    with pytest.raises(InvalidBarsQueryError, match="timeframe must be"):
        service(synthetic_document(30)).get_bars(query(timeframe, 30))
