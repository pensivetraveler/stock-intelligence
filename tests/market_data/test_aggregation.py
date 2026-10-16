from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from packages.contracts.validator import validate_document
from services.market_data.aggregation import AggregationError, MinuteBarAggregator
from services.market_data.calendar import UsEquityCalendar, us_equity_calendar_version


def utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def utc_text(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def make_bars(
    start: datetime,
    count: int,
    *,
    session: str = "regular",
    revision: str = "source-r1",
    final: bool = True,
) -> list[dict[str, Any]]:
    bars = []
    for offset in range(count):
        interval_start = start + timedelta(minutes=offset)
        interval_end = interval_start + timedelta(minutes=1)
        open_price = Decimal(100 + offset)
        bars.append(
            {
                "kind": "price_bar",
                "schema_version": "0.1",
                "instrument_id": "us:test:xnas:2026",
                "timeframe": "1m",
                "interval_start_utc": utc_text(interval_start),
                "interval_end_utc": utc_text(interval_end),
                "open": format(open_price, "f"),
                "high": format(open_price + 2, "f"),
                "low": format(open_price - 1, "f"),
                "close": format(open_price + 1, "f"),
                "volume": str(10 + offset),
                "volume_unit": "shares",
                "session": session,
                "is_final": final,
                "quality": "complete" if final else "provisional",
                "lineage": {
                    "kind": "provider",
                    "source_timeframe": "1m",
                    "expected_source_bar_count": 1,
                    "observed_source_bar_count": 1,
                    "source_dataset_revision": None,
                    "calendar_id": "XNAS",
                    "calendar_version": us_equity_calendar_version(),
                },
                "adjustment": "raw",
                "source_id": "synthetic-test",
                "source_feed_scope": "synthetic-1m",
                "dataset_revision": revision,
                "available_at_utc": utc_text(interval_end + timedelta(seconds=2)),
            }
        )
    return bars


def test_complete_five_minute_ohlcv_and_lineage() -> None:
    result = MinuteBarAggregator().aggregate(make_bars(utc(2026, 10, 9, 13, 30), 5), "5m")

    assert len(result) == 1
    bar = result[0]
    assert bar["interval_start_utc"] == "2026-10-09T13:30:00Z"
    assert bar["interval_end_utc"] == "2026-10-09T13:35:00Z"
    assert (bar["open"], bar["high"], bar["low"], bar["close"]) == (
        "100",
        "106",
        "99",
        "105",
    )
    assert bar["volume"] == "60"
    assert bar["quality"] == "complete"
    assert bar["is_final"] is True
    assert bar["lineage"]["expected_source_bar_count"] == 5
    assert bar["lineage"]["observed_source_bar_count"] == 5
    assert bar["lineage"]["source_dataset_revision"] == "source-r1"
    validate_document(bar)


def test_missing_minute_propagates_partial_quality_without_fabrication() -> None:
    bars = make_bars(utc(2026, 10, 9, 13, 30), 5)
    del bars[2]

    bar = MinuteBarAggregator().aggregate(bars, "5m")[0]

    assert bar["quality"] == "partial"
    assert bar["is_final"] is False
    assert bar["lineage"]["expected_source_bar_count"] == 5
    assert bar["lineage"]["observed_source_bar_count"] == 4
    assert bar["volume"] == "48"


def test_provisional_source_and_point_in_time_cutoff_propagate_uncertainty() -> None:
    aggregator = MinuteBarAggregator()
    provisional = make_bars(utc(2026, 10, 9, 13, 30), 5)
    provisional[-1]["is_final"] = False
    provisional[-1]["quality"] = "provisional"

    provisional_result = aggregator.aggregate(provisional, "5m")[0]
    cutoff_result = aggregator.aggregate(
        make_bars(utc(2026, 10, 9, 13, 30), 5),
        "5m",
        as_of_utc=utc(2026, 10, 9, 13, 34),
    )[0]

    assert provisional_result["quality"] == "provisional"
    assert provisional_result["is_final"] is False
    assert cutoff_result["quality"] == "partial"
    assert cutoff_result["lineage"]["observed_source_bar_count"] == 3


def test_pre_and_regular_bars_never_share_aggregate() -> None:
    pre = make_bars(utc(2026, 10, 9, 13, 25), 5, session="pre")
    regular = make_bars(utc(2026, 10, 9, 13, 30), 5, session="regular")

    result = MinuteBarAggregator().aggregate([*pre, *regular], "5m")

    assert [bar["session"] for bar in result] == ["pre", "regular"]
    assert result[0]["interval_end_utc"] == result[1]["interval_start_utc"]


def test_sixty_minute_last_regular_bucket_is_capped_at_close() -> None:
    last_half_hour = make_bars(utc(2026, 10, 9, 19, 30), 30)

    bar = MinuteBarAggregator().aggregate(last_half_hour, "60m")[0]

    assert bar["interval_start_utc"] == "2026-10-09T19:30:00Z"
    assert bar["interval_end_utc"] == "2026-10-09T20:00:00Z"
    assert bar["lineage"]["expected_source_bar_count"] == 30
    assert bar["quality"] == "complete"


def test_early_close_last_bucket_uses_shortened_session_boundary() -> None:
    last_half_hour = make_bars(utc(2026, 11, 27, 17, 30), 30)

    bar = MinuteBarAggregator().aggregate(last_half_hour, "60m")[0]

    assert bar["interval_end_utc"] == "2026-11-27T18:00:00Z"
    assert bar["lineage"]["expected_source_bar_count"] == 30
    assert bar["quality"] == "complete"


def test_dst_dates_align_to_local_session_open() -> None:
    aggregator = MinuteBarAggregator(UsEquityCalendar("XNAS"))

    before = aggregator.aggregate(make_bars(utc(2026, 3, 6, 14, 30), 5), "5m")[0]
    after = aggregator.aggregate(make_bars(utc(2026, 3, 9, 13, 30), 5), "5m")[0]

    assert before["interval_start_utc"] == "2026-03-06T14:30:00Z"
    assert after["interval_start_utc"] == "2026-03-09T13:30:00Z"


def test_duplicate_mixed_revision_and_session_mismatch_are_rejected() -> None:
    aggregator = MinuteBarAggregator()
    base = make_bars(utc(2026, 10, 9, 13, 30), 2)
    duplicate = [*base, deepcopy(base[0])]
    mixed = deepcopy(base)
    mixed[1]["dataset_revision"] = "source-r2"
    mismatch = deepcopy(base)
    mismatch[0]["session"] = "pre"

    with pytest.raises(AggregationError, match="duplicate source minute"):
        aggregator.aggregate(duplicate, "5m")
    with pytest.raises(AggregationError, match="different dataset_revision"):
        aggregator.aggregate(mixed, "5m")
    with pytest.raises(AggregationError, match="conflicts with exchange calendar"):
        aggregator.aggregate(mismatch, "5m")


def test_available_at_uses_latest_instant_not_latest_text() -> None:
    bars = make_bars(utc(2026, 10, 9, 13, 30), 5)
    bars[3]["available_at_utc"] = "2026-10-09T13:35:00.500Z"
    bars[4]["available_at_utc"] = "2026-10-09T13:35:00Z"

    result = MinuteBarAggregator().aggregate(bars, "5m")

    assert result[0]["available_at_utc"] == "2026-10-09T13:35:00.500Z"


def test_extended_day_buckets_are_returned_in_time_order() -> None:
    pre = make_bars(utc(2026, 10, 9, 13, 25), 5, session="pre")
    regular = make_bars(utc(2026, 10, 9, 13, 30), 5, session="regular")
    post = make_bars(utc(2026, 10, 9, 20, 0), 5, session="post")

    result = MinuteBarAggregator().aggregate([*post, *regular, *pre], "5m")

    assert [bar["session"] for bar in result] == ["pre", "regular", "post"]
    starts = [bar["interval_start_utc"] for bar in result]
    assert starts == sorted(starts)
