"""Deterministic generator for the SYNTH instrument in the fake-provider fixture.

Run `uv run python -m services.market_data.synthetic && pnpm format` to rewrite the fixture. The
data is invented: a seeded random walk with no relation to any real security. A
test checks that the committed fixture still matches this generator.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from services.market_data.calendar import UsEquityCalendar, us_equity_calendar_version
from services.market_data.fake import DEFAULT_FIXTURE_PATH

INSTRUMENT_ID = "us:synth:xnas:2026"
DATASET_REVISION = "synthetic-synth-20261009-r1"
SEED = 20261009
# 2026-10-09 (EDT): pre 08:30-09:30 ET, the full regular session, post 16:00-17:00 ET.
FIRST_MINUTE = datetime(2026, 10, 9, 12, 30, tzinfo=timezone.utc)
MINUTE_COUNT = 510
# A short outage inside the regular session, left missing on purpose (11:00-11:03 ET).
MISSING_MINUTES = frozenset(FIRST_MINUTE + timedelta(minutes=offset) for offset in range(150, 153))
CENT = Decimal("0.01")


def _utc_text(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def instrument_refs() -> list[dict[str, Any]]:
    return [
        {
            "kind": "instrument_ref",
            "schema_version": "0.1",
            "instrument_id": instrument_id,
            "symbol": symbol,
            "venue": "XNAS",
            "asset_class": "us_equity",
            "currency": "USD",
            "identity_as_of": "2026-10-09T00:00:00Z",
            "identity_source": "synthetic-fixture",
        }
        for instrument_id, symbol in (("us:aapl:xnas:2026", "AAPL"), (INSTRUMENT_ID, "SYNTH"))
    ]


def synthetic_bars() -> list[dict[str, Any]]:
    rng = random.Random(SEED)
    calendar = UsEquityCalendar("XNAS")
    calendar_version = us_equity_calendar_version()
    close = Decimal("50.00")
    bars: list[dict[str, Any]] = []
    for offset in range(MINUTE_COUNT):
        start = FIRST_MINUTE + timedelta(minutes=offset)
        end = start + timedelta(minutes=1)
        session = calendar.classify_interval(start, end)
        # Draw for every minute, including missing ones, so the walk stays stable.
        step = Decimal(rng.randint(-8, 8)) * CENT
        wick_up = Decimal(rng.randint(0, 5)) * CENT
        wick_down = Decimal(rng.randint(0, 5)) * CENT
        volume = rng.randint(1_000, 5_000) if session == "regular" else rng.randint(50, 400)
        open_price = close
        close = max(open_price + step, Decimal("1.00"))
        if start in MISSING_MINUTES:
            continue
        last = offset == MINUTE_COUNT - 1
        bars.append(
            {
                "kind": "price_bar",
                "schema_version": "0.1",
                "instrument_id": INSTRUMENT_ID,
                "timeframe": "1m",
                "interval_start_utc": _utc_text(start),
                "interval_end_utc": _utc_text(end),
                "open": str(open_price),
                "high": str(max(open_price, close) + wick_up),
                "low": str(min(open_price, close) - wick_down),
                "close": str(close),
                "volume": str(volume),
                "volume_unit": "shares",
                "session": session,
                # The final post-market minute is left unconfirmed to exercise
                # provisional-bar handling end to end.
                "is_final": not last,
                "quality": "provisional" if last else "complete",
                "lineage": {
                    "kind": "provider",
                    "source_timeframe": "1m",
                    "expected_source_bar_count": 1,
                    "observed_source_bar_count": 1,
                    "source_dataset_revision": None,
                    "calendar_id": "XNAS",
                    "calendar_version": calendar_version,
                },
                "adjustment": "raw",
                "source_id": "fake-market-data",
                "source_feed_scope": "synthetic-us-equity-1m",
                "dataset_revision": DATASET_REVISION,
                "available_at_utc": _utc_text(end + timedelta(seconds=2)),
            }
        )
    return bars


def write_fixture(path: Path = DEFAULT_FIXTURE_PATH) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    document["instruments"] = instrument_refs()
    document["bars"] = [
        bar for bar in document["bars"] if bar["instrument_id"] != INSTRUMENT_ID
    ] + synthetic_bars()
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    write_fixture()
