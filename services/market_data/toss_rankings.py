"""Toss stock rankings as one typed list for the Rankings panel (ADR 0049).

Two documented Toss endpoints (official OpenAPI 1.2.28, tag "Ranking") are read on demand:

- `GET /api/v1/rankings`: the top 100 by trading amount or volume (whole market or Toss
  Securities fills only), or by change rate (gainers, losers), for US or KR stocks.
- `GET /api/v1/rankings/investor-trading`: Korean stocks foreigners, institutions or
  individuals bought or sold most (net, in KRW), for the latest business day only.

Values stay the exact decimal strings Toss sent; `rankedAt` becomes UTC. Each row carries
the listing the symbol directory knows for it, so the screen can open it on a chart; the
ranking itself never becomes chart data. Nothing is stored.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
from typing import Any, Literal

from packages.contracts.validator import validate_document
from services.market_data.provider import InvalidBarsQueryError, ProviderDataError
from services.market_data.toss_research import _utc
from services.market_data.toss_session import TossSession

Market = Literal["US", "KR"]
PRICE_RANKINGS = (
    "MARKET_TRADING_AMOUNT",
    "MARKET_TRADING_VOLUME",
    "TOP_GAINERS",
    "TOP_LOSERS",
    "TOSS_SECURITIES_TRADING_AMOUNT",
    "TOSS_SECURITIES_TRADING_VOLUME",
)
INVESTOR_RANKINGS = ("NET_BUY", "NET_SELL")
DURATIONS = ("realtime", "1d", "1w", "1mo", "3mo", "6mo", "1y")
INVESTORS = ("foreigner", "institution", "individual")
MAX_COUNT = 100

# The symbol directory's listing of one symbol: {"listing", "name", "local_name"}.
Listings = Callable[[Market, Iterable[str]], Mapping[str, Mapping[str, Any]]]


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def _check(
    ranking: str,
    market: str,
    duration: str | None,
    investor: str | None,
    exclude_caution: bool,
    count: int,
) -> None:
    if not 1 <= count <= MAX_COUNT:
        raise InvalidBarsQueryError(f"count must be between 1 and {MAX_COUNT}")
    if ranking in INVESTOR_RANKINGS:
        if market != "KR":
            raise InvalidBarsQueryError("investor rankings exist for Korean stocks only")
        if investor not in INVESTORS:
            raise InvalidBarsQueryError(f"{ranking} needs an investor")
        if duration is not None:
            raise InvalidBarsQueryError("investor rankings cover the latest business day only")
        if exclude_caution:
            raise InvalidBarsQueryError("investor rankings cannot exclude caution stocks")
        return
    if ranking not in PRICE_RANKINGS:
        raise InvalidBarsQueryError(f"unknown ranking {ranking!r}")
    if investor is not None:
        raise InvalidBarsQueryError(f"{ranking} takes no investor")
    if duration not in DURATIONS:
        raise InvalidBarsQueryError(f"{ranking} needs a duration")
    if ranking in ("TOP_GAINERS", "TOP_LOSERS") and duration == "realtime":
        # Toss answers 400 unsupported-ranking-duration.
        raise InvalidBarsQueryError(f"{ranking} has no realtime ranking")


def stock_rankings(
    session: TossSession,
    ranking: str,
    market: Market,
    *,
    duration: str | None,
    investor: str | None,
    exclude_caution: bool,
    count: int,
    listings: Listings,
    now: Callable[[], datetime],
) -> dict[str, Any]:
    _check(ranking, market, duration, investor, exclude_caution, count)
    if ranking in INVESTOR_RANKINGS:
        result = session.get("/api/v1/rankings/investor-trading", params={"count": str(count)})
        group = result.get(investor) if isinstance(result, dict) else None
        key = "netBuyRankings" if ranking == "NET_BUY" else "netSellRankings"
        amount = "netBuyAmount" if ranking == "NET_BUY" else "netSellAmount"
        items = group.get(key) if isinstance(group, dict) else None
        ranked_at = group.get("rankedAt") if isinstance(group, dict) else None
    else:
        params = {"type": ranking, "marketCountry": market, "duration": str(duration)}
        if exclude_caution:
            params["excludeInvestmentCaution"] = "true"
        params["count"] = str(count)
        result = session.get("/api/v1/rankings", params=params)
        items = result.get("rankings") if isinstance(result, dict) else None
        ranked_at = result.get("rankedAt") if isinstance(result, dict) else None
        amount = None
    if not isinstance(items, list):
        raise ProviderDataError(f"Toss returned no ranking list for {ranking}")
    entries = [item for item in items[:MAX_COUNT] if isinstance(item, dict)]
    known = listings(market, [str(item.get("symbol", "")) for item in entries])
    rows: list[dict[str, Any]] = []
    for item in entries:
        symbol = str(item.get("symbol", ""))
        raw_price = item.get("price")
        price: dict[str, Any] = raw_price if isinstance(raw_price, dict) else {}
        found = known.get(symbol.upper())
        rows.append(
            {
                "rank": item.get("rank"),
                "symbol": symbol,
                "currency": _text(item.get("currency")),
                "last_price": _text(price.get("lastPrice")),
                "base_price": _text(price.get("basePrice")),
                "change_rate": _text(price.get("changeRate")),
                "trading_volume": _text(item.get("tradingVolume")),
                "trading_amount": _text(item.get("tradingAmount")),
                "net_amount": _text(item.get(amount)) if amount else None,
                "listing": found["listing"] if found else None,
                "name": found["name"] if found else None,
                "local_name": found["local_name"] if found else None,
            }
        )
    document = {
        "kind": "stock_rankings_response",
        "schema_version": "0.1",
        "source_id": "toss-kr" if market == "KR" else "toss-us",
        "ranking": ranking,
        "market": market,
        "duration": duration,
        "investor": investor,
        "exclude_caution": exclude_caution,
        "observed_at_utc": now()
        .astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "ranked_at_utc": _utc(str(ranked_at)) if ranked_at else None,
        "rows": rows,
    }
    try:
        validate_document(document)
    except ValueError as exc:
        raise ProviderDataError(f"Toss ranking data violates the contract: {exc}") from exc
    return document
