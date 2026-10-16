from __future__ import annotations

import json
import socket
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, cast

import pytest

from packages.contracts.validator import validate_document
from services.market_data.fake import DEFAULT_FIXTURE_PATH, FakeMarketDataProvider
from services.market_data.provider import (
    BarsQuery,
    FixtureIntegrityError,
    InstrumentNotFoundError,
    InvalidBarsQueryError,
)

INSTRUMENT_ID = "us:aapl:xnas:2026"
START = datetime(2026, 10, 9, 13, 30, tzinfo=timezone.utc)
END = datetime(2026, 10, 9, 13, 34, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def fixture_document() -> dict[str, Any]:
    value = json.loads(DEFAULT_FIXTURE_PATH.read_text(encoding="utf-8"))
    return cast(dict[str, Any], value)


def query(**changes: object) -> BarsQuery:
    values: dict[str, object] = {
        "instrument_id": INSTRUMENT_ID,
        "timeframe": "1m",
        "start_utc": START,
        "end_utc": END,
        "as_of_utc": None,
    }
    values.update(changes)
    return BarsQuery(**cast(dict[str, Any], values))


def provider(document: dict[str, Any] | None = None) -> FakeMarketDataProvider:
    return FakeMarketDataProvider(document or fixture_document(), now=lambda: NOW)


def test_same_query_returns_same_revision_and_document() -> None:
    fake = provider()

    first = fake.get_bars(query()).to_document()
    second = fake.get_bars(query()).to_document()

    assert first == second
    assert len(first["bars"]) == 4
    assert first["coverage"]["coverage_status"] == "complete"
    assert first["coverage"]["dataset_revision"] == "fake-aapl-20261009-r1"
    validate_document(first)


def test_missing_fixture_minute_is_reported_as_partial_without_filling() -> None:
    document = fixture_document()
    del cast(list[object], document["bars"])[2]

    result = provider(document).get_bars(query()).to_document()

    assert len(result["bars"]) == 3
    coverage = result["coverage"]
    assert coverage["coverage_status"] == "partial"
    assert coverage["missing_intervals"] == [
        {
            "start_utc": "2026-10-09T13:32:00Z",
            "end_utc": "2026-10-09T13:33:00Z",
            "reason": "fixture_missing",
        }
    ]


def test_duplicate_bar_is_rejected_at_fixture_boundary() -> None:
    document = fixture_document()
    bars = cast(list[dict[str, Any]], document["bars"])
    bars.append(deepcopy(bars[0]))

    with pytest.raises(FixtureIntegrityError, match="duplicate canonical bar identity"):
        provider(document)


def test_point_in_time_cutoff_hides_unavailable_bars() -> None:
    result = provider().get_bars(
        query(as_of_utc=datetime(2026, 10, 9, 13, 32, 30, tzinfo=timezone.utc))
    )

    assert len(result.bars) == 2
    assert result.coverage["coverage_status"] == "partial"
    assert {item["reason"] for item in result.coverage["missing_intervals"]} == {
        "not_available_as_of_cutoff"
    }


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"timeframe": "5m"}, "only the 1m"),
        ({"end_utc": START}, "must be after"),
        ({"end_utc": datetime(2026, 10, 11, 13, 31, tzinfo=timezone.utc)}, "cannot exceed"),
        ({"start_utc": datetime(2026, 10, 9, 22, 30)}, "must be UTC"),
        (
            {
                "start_utc": datetime(1990, 1, 3, 15, 0, tzinfo=timezone.utc),
                "end_utc": datetime(1990, 1, 3, 15, 5, tzinfo=timezone.utc),
            },
            "outside the supported exchange calendar range",
        ),
        (
            {"as_of_utc": datetime(2026, 10, 13, tzinfo=timezone.utc)},
            "cannot be in the future",
        ),
    ],
)
def test_invalid_query_is_rejected(changes: dict[str, object], message: str) -> None:
    with pytest.raises(InvalidBarsQueryError, match=message):
        provider().get_bars(query(**changes))


def test_unknown_instrument_is_rejected() -> None:
    with pytest.raises(InstrumentNotFoundError):
        provider().get_bars(query(instrument_id="us:missing:xnas:2026"))


def test_exchange_holiday_is_empty_coverage_not_missing_minutes() -> None:
    result = provider().get_bars(
        query(
            start_utc=datetime(2026, 11, 26, 14, 30, tzinfo=timezone.utc),
            end_utc=datetime(2026, 11, 26, 15, 30, tzinfo=timezone.utc),
        )
    )

    assert result.bars == ()
    assert result.coverage["coverage_status"] == "empty"
    assert result.coverage["expected_interval_count"] == 0
    assert result.coverage["missing_intervals"] == []


def test_provider_path_performs_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network access is forbidden for FakeMarketDataProvider")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    result = provider().get_bars(query())

    assert len(result.bars) == 4
