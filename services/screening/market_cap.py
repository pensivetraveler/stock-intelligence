"""The cutoff market cap SIW sends with a news check (`context`, news_check 1.1).

The news-analysis service cannot judge "the revenue increase is small next to the market
cap" without a market cap, and it never fills one itself. SIW has no market cap source, so
it multiplies two values it already reads:

- shares outstanding from Toss's stock reference (`/v1/market/stock-info`'s source). Toss
  does not say when the count was last updated, so its as-of time is when SIW read it.
  Thinly traded small caps can issue shares faster than a reference count catches up;
  the value is an estimate, marked by its source.
- the close of the last final 1m bar that ended at or before the cutoff (Alpaca SIP by
  default), read as of the cutoff.

Both must be known at or before the cutoff (no future data). Toss can only be read now, so
a check for a past session sends no market cap. When either value is missing nothing is
sent and the reason is kept; nothing is made up. No prior revenue is sent: SIW has no
source for it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any

from services.market_data.provider import BarsQuery, MarketDataError
from services.screening.candidates import utc_text

SOURCE = "siw:toss-shares_x_1m-close"
#: How far back from the cutoff the last 1m close is looked for.
PRICE_LOOKBACK = timedelta(hours=16)


@dataclass(frozen=True)
class Shares:
    count: Decimal
    observed_at_utc: datetime
    source_id: str


ReadShares = Callable[[str, str], Shares | None]


def _parse(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def shares_from_stock_info(document: dict[str, Any]) -> Shares | None:
    """Toss's `stock_info` document reduced to its share count, or None without one."""
    raw = document.get("shares_outstanding")
    if raw in (None, ""):
        return None
    try:
        count = Decimal(str(raw))
    except InvalidOperation:
        return None
    if not count.is_finite() or count <= 0:
        return None
    return Shares(
        count, _parse(str(document["observed_at_utc"])), str(document.get("source_id") or "toss")
    )


def last_close(
    instrument_id: str, cutoff: datetime, get_bars: Callable[[BarsQuery], Any]
) -> tuple[Decimal, datetime, str] | None:
    """(close, bar end, source) of the last final 1m bar ending at or before the cutoff."""
    result = get_bars(
        BarsQuery(instrument_id, "1m", cutoff - PRICE_LOOKBACK, cutoff, as_of_utc=cutoff)
    )
    best: tuple[Decimal, datetime, str] | None = None
    for bar in result.bars:
        if not bar.get("is_final", True) or bar.get("volume_unit", "shares") != "shares":
            continue
        end = (
            _parse(str(bar["interval_end_utc"]))
            if bar.get("interval_end_utc")
            else _parse(str(bar["interval_start_utc"])) + timedelta(minutes=1)
        )
        if end > cutoff:
            continue
        if best is None or end > best[1]:
            source = str(bar.get("source_id") or result.coverage.get("source_id") or "bars")
            best = (Decimal(str(bar["close"])), end, source)
    return best


def market_cap_context(
    instrument_id: str,
    cutoff: datetime,
    shares: Shares | None,
    get_bars: Callable[[BarsQuery], Any] | None,
) -> tuple[dict[str, Any] | None, str | None]:
    """(`context` for the news request, or None; why nothing was sent)."""
    # The service gets the cutoff in whole seconds; nothing may be later than that.
    cutoff = cutoff.replace(microsecond=0)
    if shares is None:
        return None, "no share count (Toss not configured, not read, or no value)"
    if shares.observed_at_utc > cutoff:
        return None, "the share count was read after the cutoff"
    if get_bars is None:
        return None, "no bars source for the price"
    try:
        found = last_close(instrument_id, cutoff, get_bars)
    except MarketDataError as exc:
        return None, f"1m bars: {str(exc)[:200]}"
    if found is None:
        return None, "no final 1m bar before the cutoff"
    close, bar_end, bar_source = found
    if close <= 0:
        return None, "the last close is not positive"
    value = (shares.count * close).quantize(Decimal(1), rounding=ROUND_HALF_EVEN)
    as_of = max(shares.observed_at_utc, bar_end)
    return {
        "market_cap_usd": int(value),
        "market_cap_as_of_utc": utc_text(as_of),
        "market_cap_source": f"{SOURCE}:{shares.source_id}:{bar_source}"[:80],
    }, None
