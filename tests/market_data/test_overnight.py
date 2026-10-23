"""US day market (overnight session, ADR 0030): policy windows and Alpaca BOATS bars."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from packages.contracts.validator import validate_document
from services.api.main import create_app
from services.market_data.alpaca import AlpacaConfig, AlpacaMarketDataProvider
from services.market_data.calendar import UsEquityCalendar
from services.market_data.fake import FakeMarketDataProvider
from services.market_data.overnight import overnight_session_of, overnight_window
from services.market_data.provider import InvalidBarsQueryError

NOW = datetime(2026, 10, 22, 6, 10, tzinfo=timezone.utc)  # 02:10 New York, mid-session
AAPL = "us:aapl:xnas:2026"
CALENDAR = UsEquityCalendar("XNAS")


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def test_the_session_runs_20_to_04_new_york_before_its_trading_day() -> None:
    assert overnight_window(CALENDAR, date(2026, 10, 22)) == (
        utc("2026-10-22T00:00:00Z"),
        utc("2026-10-22T08:00:00Z"),
    )
    # Monday's session starts on Sunday evening.
    assert overnight_window(CALENDAR, date(2026, 10, 19))[0] == utc("2026-10-19T00:00:00Z")
    # Standard time: 20:00 EST = 01:00 UTC.
    assert overnight_window(CALENDAR, date(2026, 12, 1)) == (
        utc("2026-12-01T01:00:00Z"),
        utc("2026-12-01T09:00:00Z"),
    )
    assert overnight_window(CALENDAR, date(2026, 10, 24)) is None  # Saturday
    assert overnight_window(CALENDAR, date(2026, 11, 26)) is None  # Thanksgiving
    assert overnight_session_of(CALENDAR, utc("2026-10-22T02:00:00Z")) == date(2026, 10, 22)
    assert overnight_session_of(CALENDAR, utc("2026-10-22T12:00:00Z")) is None
    # Friday evening leads to no session (Saturday is not a trading day).
    assert overnight_session_of(CALENDAR, utc("2026-10-24T01:00:00Z")) is None


@pytest.mark.parametrize(
    ("session_date", "expected"),
    [
        # Toss's market calendar `dayMarket` sessions, read on 2026-10-22 (ADR 0030).
        (date(2025, 11, 27), None),  # Thanksgiving: no session on Wednesday night
        (date(2025, 11, 28), ("2025-11-28T01:00:00Z", "2025-11-28T09:00:00Z")),  # early close
        (date(2025, 12, 1), ("2025-12-01T01:00:00Z", "2025-12-01T09:00:00Z")),
        (date(2025, 12, 25), None),
        (date(2025, 12, 26), ("2025-12-26T01:00:00Z", "2025-12-26T09:00:00Z")),
        (date(2026, 3, 9), ("2026-03-09T00:00:00Z", "2026-03-09T08:00:00Z")),  # DST began
        (date(2026, 7, 3), None),
        (date(2026, 7, 6), ("2026-07-06T00:00:00Z", "2026-07-06T08:00:00Z")),
        (date(2026, 11, 2), ("2026-11-02T01:00:00Z", "2026-11-02T09:00:00Z")),  # DST ended
        (date(2026, 12, 28), ("2026-12-28T01:00:00Z", "2026-12-28T09:00:00Z")),
    ],
)
def test_holidays_and_early_closes_match_the_toss_calendar(
    session_date: date, expected: tuple[str, str] | None
) -> None:
    window = overnight_window(CALENDAR, session_date)
    assert window == (None if expected is None else (utc(expected[0]), utc(expected[1])))


# Blue Ocean ATS's own holiday notice (blueocean-tech.io/trading-updates, read 2026-10-23):
# the evenings it stays closed. Each is the start of the session into the next day.
BOATS_CLOSED_EVENINGS = [
    "2024-12-31",
    "2025-01-19",
    "2025-02-16",
    "2025-04-17",
    "2025-05-25",
    "2025-06-18",
    "2025-07-03",
    "2025-08-31",
    "2025-11-26",
    "2025-12-24",
    "2025-12-31",
    "2026-01-18",
    "2026-02-15",
    "2026-04-02",
    "2026-05-24",
    "2026-06-18",
    "2026-07-02",
    "2026-09-06",
    "2026-11-25",
    "2026-12-24",
    "2026-12-31",
    "2027-01-17",
    "2027-02-14",
    "2027-03-25",
    "2027-05-30",
    "2027-06-17",
    "2027-07-04",
    "2027-09-05",
    "2027-11-24",
    "2027-12-23",
]


@pytest.mark.parametrize("evening", BOATS_CLOSED_EVENINGS)
def test_blue_oceans_closed_evenings_have_no_session(evening: str) -> None:
    assert overnight_window(CALENDAR, date.fromisoformat(evening) + timedelta(days=1)) is None


def minute(stamp: str, o: str, h: str, low: str, c: str, v: int) -> str:
    return f'{{"t":"2026-10-22T{stamp}:00Z","o":{o},"h":{h},"l":{low},"c":{c},"v":{v}}}'


class Boats:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        bars = ",".join(
            [
                minute("00:00", "250.10", "250.30", "250.00", "250.20", 300),
                minute("00:01", "250.20", "250.60", "250.20", "250.50", 100),
                minute("00:07", "250.40", "250.40", "249.90", "250.00", 50),
                minute("03:00", "251.00", "251.00", "251.00", "251.00", 10),
            ]
        )
        return httpx.Response(
            200,
            text=f'{{"bars":{{"AAPL":[{bars}]}},"next_page_token":null}}',
            headers={"Content-Type": "application/json"},
        )


def adapter(boats: Boats) -> AlpacaMarketDataProvider:
    return AlpacaMarketDataProvider(
        AlpacaConfig(key_id="test-key-id", secret_key="test-secret-not-real"),
        transport=httpx.MockTransport(boats),
        now=lambda: NOW,
    )


def test_boats_minutes_are_their_own_labelled_stream() -> None:
    boats = Boats()
    result = adapter(boats).get_overnight_bars(AAPL, "1m", date(2026, 10, 22), date(2026, 10, 22))
    validate_document(result.to_document())
    (request,) = boats.requests
    assert request.url.params["feed"] == "boats"
    assert request.url.params["start"] == "2026-10-22T00:00:00Z"
    assert request.url.params["end"] == "2026-10-22T06:10:00Z"  # never past now
    assert [bar["interval_start_utc"][11:16] for bar in result.bars] == [
        "00:00",
        "00:01",
        "00:07",
        "03:00",
    ]
    assert {bar["session"] for bar in result.bars} == {"overnight"}
    assert {bar["source_id"] for bar in result.bars} == {"alpaca-boats"}
    coverage = result.coverage
    assert coverage["source_feed_scope"] == "alpaca-boats"
    assert coverage["expected_interval_count"] == 480
    reasons = {gap["reason"] for gap in coverage["missing_intervals"]}
    assert reasons == {"provider_no_bar", "not_yet_final", "outside_entitlement"}


def test_buckets_count_from_the_session_start() -> None:
    result = adapter(Boats()).get_overnight_bars(AAPL, "5m", date(2026, 10, 22), date(2026, 10, 22))
    first, second = result.bars[0], result.bars[1]
    assert (first["interval_start_utc"], first["interval_end_utc"]) == (
        "2026-10-22T00:00:00Z",
        "2026-10-22T00:05:00Z",
    )
    assert (first["open"], first["high"], first["low"], first["close"]) == (
        "250.10",
        "250.60",
        "250.00",
        "250.50",
    )
    assert first["volume"] == "400"
    assert first["lineage"]["kind"] == "aggregated_1m"
    assert first["lineage"]["observed_source_bar_count"] == 2
    assert second["interval_start_utc"] == "2026-10-22T00:05:00Z"
    assert result.coverage["expected_interval_count"] == 96


def test_as_of_hides_bars_not_yet_available() -> None:
    result = adapter(Boats()).get_overnight_bars(
        AAPL,
        "1m",
        date(2026, 10, 22),
        date(2026, 10, 22),
        as_of_utc=utc("2026-10-22T00:03:00Z"),
    )
    assert [bar["interval_start_utc"][11:16] for bar in result.bars] == ["00:00", "00:01"]
    hidden = [
        gap
        for gap in result.coverage["missing_intervals"]
        if gap["start_utc"] == "2026-10-22T00:07:00Z"
    ]
    assert hidden[0]["reason"] == "not_available_as_of_cutoff"


def test_queries_are_bounded() -> None:
    boats = Boats()
    with pytest.raises(InvalidBarsQueryError):
        adapter(boats).get_overnight_bars(AAPL, "1m", date(2026, 10, 1), date(2026, 10, 22))
    with pytest.raises(InvalidBarsQueryError):
        adapter(boats).get_overnight_bars(AAPL, "1d", date(2026, 10, 22), date(2026, 10, 22))
    # Only non-trading days: nothing is requested and the answer is empty.
    weekend = adapter(boats).get_overnight_bars(AAPL, "1m", date(2026, 10, 24), date(2026, 10, 25))
    assert weekend.bars == () and weekend.coverage["expected_interval_count"] == 0
    assert boats.requests == []


def test_overnight_bars_api(tmp_path: Any) -> None:
    source = adapter(Boats())
    app = create_app(FakeMarketDataProvider.from_path(), overnight_source=lambda: source)
    with TestClient(app) as client:
        params = {"instrument_id": AAPL, "from_date": "2026-10-22", "to_date": "2026-10-22"}
        answer = client.get("/v1/market/overnight-bars", params={**params, "timeframe": "15m"})
        assert answer.status_code == 200, answer.text
        validate_document(answer.json())
        assert answer.json()["bars"][0]["session"] == "overnight"
        korean = client.get(
            "/v1/market/overnight-bars", params={**params, "instrument_id": "kr:005930:xkrx:2026"}
        )
        assert korean.status_code == 422
    missing = create_app(FakeMarketDataProvider.from_path(), overnight_source=lambda: None)
    with TestClient(missing) as client:
        assert client.get("/v1/market/overnight-bars", params=params).status_code == 404
