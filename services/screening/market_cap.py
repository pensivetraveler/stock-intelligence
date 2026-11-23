"""The cutoff market cap SIW sends with a news check (`context`, news_check 1.1).

The news-analysis service cannot judge "the revenue increase is small next to the market
cap" without a market cap, and it never fills one itself. SIW has no market cap source, so
it multiplies two values it already reads:

- shares outstanding. First the dilution analyzer's as-of count from a stored dilution
  check (check C) of an earlier session (2026-11-23 user decision, ADR 0057): the analyzer
  reads 10-Q/10-K, 8-K, 424B and reverse splits. Its as-of is a New York date and may hold
  filings made later that day, so only an analysis dated before the cutoff's New York date
  is used, and every filing it cites must be dated before that day too. Without one, Toss's
  stock reference count (`/v1/market/stock-info`'s source), marked with a warning: Toss
  does not say when the count was last updated (its as-of is when SIW read it), and it was
  seen a quarter behind and missing post-split issuance (ADR 0057, 2026-11-15).
- the close of the last final 1m bar that ended at or before the cutoff (Alpaca SIP by
  default), read as of the cutoff.

Both must be known at or before the cutoff (no future data). Toss can only be read now, so
a check for a past session sends no market cap. When either value is missing nothing is
sent and the reason is kept; nothing is made up. No prior revenue is sent: SIW has no
source for it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any

from services.market_data.calendar import NEW_YORK
from services.market_data.provider import BarsQuery, MarketDataError
from services.screening.candidates import utc_text

SOURCE = "siw:toss-shares_x_1m-close"
DILUTION_SOURCE = "siw:dilution-shares_x_1m-close"
#: How many calendar days back an earlier session's dilution check is looked for.
DILUTION_LOOKBACK_DAYS = 10
#: Only values the analyzer marks as facts (not unverified, user input or scenarios).
DILUTION_STATUSES = frozenset({"confirmed", "computed", "inferred"})
TOSS_FALLBACK_WARNING = (
    "시가총액 주식 수: 희석 분석기 값이 없어 Toss 발행주식수를 썼다 — 갱신 시점을 모르고 "
    "실제보다 적을 수 있다(ADR 0057 2026-11-23 결정)."
)
#: How far back from the cutoff the last 1m close is looked for.
PRICE_LOOKBACK = timedelta(hours=16)


@dataclass(frozen=True)
class Shares:
    count: Decimal
    observed_at_utc: datetime
    source_id: str
    #: `SOURCE` (Toss) or `DILUTION_SOURCE` (the dilution analyzer).
    kind: str = SOURCE


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


def _filing_date(text: Any) -> date | None:
    try:
        return date.fromisoformat(str(text))
    except ValueError:
        return None


def shares_from_dilution(
    documents: Iterable[Mapping[str, Any]], cutoff: datetime
) -> tuple[Shares | None, str | None]:
    """The newest usable analyzer share count among stored dilution checks, or why none.

    Usable: made at or before the cutoff, analysis date and value date before the cutoff's
    New York date, a positive amount the analyzer marks as a fact, and every cited filing
    dated before that day (an analysis dated the cutoff's day may include later filings).
    """
    day = cutoff.astimezone(NEW_YORK).date()
    best: tuple[date, datetime, Shares] | None = None
    why = "no earlier dilution analysis"
    for document in documents:
        if document.get("check") != "dilution":
            continue
        session = _filing_date(document.get("session_date"))
        created = document.get("created_at_utc")
        if session is None or session >= day or not created or _parse(str(created)) > cutoff:
            continue
        result = document.get("result") or {}
        contract = result.get("contract") if isinstance(result, Mapping) else None
        counts = (
            (contract or {}).get("shares_outstanding") if isinstance(contract, Mapping) else None
        )
        value = (counts or {}).get("as_of") if isinstance(counts, Mapping) else None
        if not isinstance(value, Mapping) or value.get("amount") in (None, ""):
            why = "the dilution analysis has no as-of share count"
            continue
        if value.get("status") not in DILUTION_STATUSES:
            why = f"the dilution share count is {value.get('status')}"
            continue
        value_day = _filing_date(value.get("as_of"))
        filed = [_filing_date(s.get("filing_date")) for s in value.get("sources") or ()]
        if value_day is None or value_day >= day or any(f is None or f >= day for f in filed):
            why = "the dilution share count cites a filing of the cutoff's day or later"
            continue
        try:
            count = Decimal(str(value["amount"]))
        except InvalidOperation:
            continue
        if not count.is_finite() or count <= 0:
            continue
        made = _parse(str(created))
        rid = str(result.get("result_id") or document.get("check_id") or "")[:16]
        # SIW knew the count when it stored the check.
        shares = Shares(count, made, f"dilution-{rid}" if rid else "dilution", DILUTION_SOURCE)
        if best is None or (value_day, made) > (best[0], best[1]):
            best = (value_day, made, shares)
    return (best[2], None) if best is not None else (None, why)


def last_close(
    instrument_id: str, cutoff: datetime, get_bars: Callable[[BarsQuery], Any]
) -> tuple[Decimal, datetime, str] | None:
    """(close, bar end, source) of the last final 1m bar ending at or before the cutoff."""
    # 1m sources take whole-minute ranges; a 1m bar ending at or before the cutoff ends at
    # or before the cutoff's minute, so the range stops there and the bars are still read
    # as known at the cutoff itself.
    end = cutoff.replace(second=0, microsecond=0)
    result = get_bars(BarsQuery(instrument_id, "1m", end - PRICE_LOOKBACK, end, as_of_utc=cutoff))
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
        "market_cap_source": f"{shares.kind}:{shares.source_id}:{bar_source}"[:80],
    }, None
