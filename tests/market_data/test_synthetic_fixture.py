from __future__ import annotations

import json

from services.market_data.fake import DEFAULT_FIXTURE_PATH, FakeMarketDataProvider
from services.market_data.synthetic import INSTRUMENT_ID, instrument_refs, synthetic_bars


def test_committed_fixture_matches_the_deterministic_generator() -> None:
    document = json.loads(DEFAULT_FIXTURE_PATH.read_text(encoding="utf-8"))

    committed = [bar for bar in document["bars"] if bar["instrument_id"] == INSTRUMENT_ID]
    assert committed == synthetic_bars(), (
        "Regenerate with `uv run python -m services.market_data.synthetic && pnpm format`."
    )
    assert document["instruments"] == instrument_refs()


def test_synthetic_day_has_extended_sessions_a_gap_and_a_provisional_minute() -> None:
    bars = synthetic_bars()

    assert {bar["session"] for bar in bars} == {"pre", "regular", "post"}
    assert len(bars) == 507
    assert [bar["interval_start_utc"] for bar in bars if bar["quality"] != "complete"] == [
        "2026-10-09T20:59:00Z"
    ]


def test_provider_lists_instruments_that_have_bars() -> None:
    items = FakeMarketDataProvider.from_path().instruments()

    assert [item["instrument"]["symbol"] for item in items] == ["AAPL", "SYNTH"]
    assert all(item["session_dates"] == ["2026-10-09"] for item in items)
    assert items[1]["dataset_revision"] == "synthetic-synth-20261009-r1"
