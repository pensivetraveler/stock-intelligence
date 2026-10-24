"""Maintenance check for ticker reuse the history does not record (ADR 0033 rule 6).

Read-only. Every stored US broker fill without an `instrument_resolution` is compared
with the raw SIP 1-hour bars of its chart in the hour before, of and after the fill
(day-market fills are skipped: BOATS is not in SIP). An instrument whose fills mostly
lie outside those bars is probably another company's chart; fix it with a registry
entry that has `reused_on` (see ADR 0033).

Run from the repository root with the Alpaca key in the environment:

    set -a; source ./.env; set +a
    uv run python scripts/check_ticker_reuse.py [--min-fills 3] [--share 0.5]

Prints instrument IDs and fill counts only, never prices, amounts or accounts. It makes
one paged bar request per instrument (about 2,500 for five years of trades).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
TOLERANCE = Decimal("0.03")
Span = tuple[Decimal, Decimal]


def hour_key(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H")


def count_outside(
    fills: Iterable[tuple[datetime, Decimal]], hourly: dict[str, Span]
) -> tuple[int, int]:
    """(fills outside their bars, fills with no bar near them). A fill fits when it lies
    within the low and high of the hours around it, 3% either way."""
    outside = no_bar = 0
    for moment, price in fills:
        spans = [hourly.get(hour_key(moment + timedelta(hours=k))) for k in (-1, 0, 1)]
        found = [span for span in spans if span is not None]
        if not found:
            no_bar += 1
            continue
        low, high = min(s[0] for s in found), max(s[1] for s in found)
        if price < low * (1 - TOLERANCE) or price > high * (1 + TOLERANCE):
            outside += 1
    return outside, no_bar


def flagged(
    results: dict[str, dict[str, int]], min_fills: int, share: float
) -> list[tuple[str, dict[str, int]]]:
    out = []
    for instrument, item in results.items():
        compared = item["fills"] - item["no_bar"]
        if compared >= min_fills and item["outside"] / compared >= share:
            out.append((instrument, item))
    return sorted(out, key=lambda pair: -pair[1]["outside"])


def main() -> int:  # pragma: no cover - needs the network and the user's data
    import httpx

    sys.path.insert(0, os.getcwd())
    from services.journal.store import JournalStore

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--min-fills", type=int, default=3)
    parser.add_argument("--share", type=float, default=0.5)
    args = parser.parse_args()

    root = Path(os.environ["SIW_DATA_DIR"])
    registry = json.loads((root / "symbols" / "delisted-instruments.json").read_text())
    client = httpx.Client(
        base_url="https://data.alpaca.markets",
        headers={
            "APCA-API-KEY-ID": os.environ["APCA_API_KEY_ID"],
            "APCA-API-SECRET-KEY": os.environ["APCA_API_SECRET_KEY"],
        },
        timeout=60,
    )

    def hourly_bars(ticker: str, start: str, end: str, asof: str | None) -> dict[str, Span]:
        bars: dict[str, Span] = {}
        token = None
        while True:
            params = {
                "timeframe": "1Hour",
                "start": start,
                "end": end,
                "adjustment": "raw",
                "feed": "sip",
                "limit": "10000",
            }
            if asof:
                params["asof"] = asof
            if token:
                params["page_token"] = token
            for _ in range(5):
                response = client.get(f"/v2/stocks/{ticker}/bars", params=params)
                if response.status_code != 429:
                    break
                time.sleep(3)
            if response.status_code != 200:
                return bars
            body = response.json()
            for bar in body.get("bars") or []:
                bars[bar["t"][:13]] = (Decimal(str(bar["l"])), Decimal(str(bar["h"])))
            token = body.get("next_page_token")
            if not token:
                return bars

    by_instrument: defaultdict[str, list[tuple[datetime, Decimal]]] = defaultdict(list)
    for execution in JournalStore(root).broker_executions():
        instrument = execution["instrument_id"]
        stamp = execution.get("executed_at_utc")
        if not instrument.startswith("us:") or execution.get("instrument_resolution") or not stamp:
            continue
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if not 4 <= moment.astimezone(NY).hour < 20:
            continue
        by_instrument[instrument].append((moment, Decimal(execution["price"])))

    results: dict[str, dict[str, int]] = {}
    for instrument, fills in sorted(by_instrument.items()):
        symbol = instrument.split(":")[1].upper()
        item = registry.get(symbol) or {}
        days = sorted(moment.date() for moment, _ in fills)
        hourly = hourly_bars(
            item.get("ticker") or symbol,
            days[0].isoformat(),
            (days[-1] + timedelta(days=2)).isoformat(),
            item.get("asof"),
        )
        outside, no_bar = count_outside(fills, hourly)
        results[instrument] = {"fills": len(fills), "outside": outside, "no_bar": no_bar}

    print(f"instruments {len(results)}, fills {sum(r['fills'] for r in results.values())}")
    for instrument, item in flagged(results, args.min_fills, args.share):
        print(f"{instrument}: {item['outside']} of {item['fills']} fills outside their bars")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
