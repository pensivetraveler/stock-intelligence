"""Live check for a broker adapter with the user's own keys (one 30-minute request).

    python -m services.market_data.broker_check --provider kis --symbol SPY [--label 해외주식]

Uses the keys registered on the Keys screen (all enabled keys in order, or the one named
by --label). Prints the result summary and the first raw record exactly as the broker
sent it (market data only, never credentials), so the time zone and bar-start meaning of
the timestamp can be confirmed before relying on the adapter.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any

from services.market_data.calendar import UsEquityCalendar
from services.market_data.provider import BarsQuery, MarketDataError
from services.market_data.selection import (
    ProviderConfigError,
    default_credential_store,
    pooled_provider,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m services.market_data.broker_check")
    parser.add_argument("--provider", choices=("kis", "toss", "kiwoom", "meritz"), required=True)
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--date", type=date.fromisoformat, default=None)
    parser.add_argument("--label", default=None, help="use only the registered key with this label")
    args = parser.parse_args(argv)
    try:
        store = default_credential_store()
        provider: Any = pooled_provider(
            args.provider,
            store,
            only=(lambda record: record.label == args.label) if args.label else None,
        )
    except ProviderConfigError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 2
    listings = provider.instruments()
    symbol = (args.symbol or listings[0]["instrument"]["symbol"]).upper()
    match = [item for item in listings if item["instrument"]["symbol"] == symbol]
    if not match:
        print(json.dumps({"status": "error", "error": f"{symbol} is not configured"}))
        return 2
    calendar = UsEquityCalendar("XNAS")
    day = args.date or (datetime.now(timezone.utc) - timedelta(days=1)).date()
    window = calendar.window_for_date(day)
    while window is None:
        day -= timedelta(days=1)
        window = calendar.window_for_date(day)
    start = window.regular_open_utc
    query = BarsQuery(
        match[0]["instrument"]["instrument_id"], "1m", start, start + timedelta(minutes=30)
    )
    try:
        result = provider.get_bars(query)
    except MarketDataError as exc:
        print(json.dumps({"status": "error", "error_type": type(exc).__name__, "error": str(exc)}))
        return 1
    print(
        json.dumps(
            {
                "status": "ok",
                "source_id": result.coverage["source_id"],
                "key_label": next(
                    (
                        record.label
                        for record in store.list(args.provider)
                        if record.credential_id == provider.last_credential_id
                    ),
                    None,
                ),
                "symbol": symbol,
                "session_date": day.isoformat(),
                "range_utc": [
                    result.coverage["requested_start_utc"],
                    result.coverage["requested_end_utc"],
                ],
                "bars": len(result.bars),
                "expected_minutes": result.coverage["expected_interval_count"],
                "missing_reasons": sorted(
                    {gap["reason"] for gap in result.coverage["missing_intervals"]}
                ),
                "first_raw_record": provider.last_raw_sample,
                "first_bar_start_utc": result.bars[0]["interval_start_utc"]
                if result.bars
                else None,
                "note": (
                    "Compare first_raw_record's time with first_bar_start_utc to confirm "
                    "the time zone and bar start."
                ),
            },
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
