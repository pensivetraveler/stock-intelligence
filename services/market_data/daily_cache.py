"""Local cache for daily bars, kept as one coverage record per calendar year.

A request is split into calendar years (UTC; every regular open is on the same date in
UTC and New York). A year is served from the cache only when it was stored settled:
every bar final and every missing session a settled fact. Other years, including the
current one, are fetched from the provider in one call, and each fetched year that is
settled is stored for next time. The current year never is, so it is always fresh.

Years are never combined across sources, feeds, adjustments or revisions: if the cache
and the provider disagree on that identity, the provider's data alone is served.

A point-in-time request reuses a stored year only when every bar in it was available
by the cutoff, which is then exactly what the provider would return; a fetched year is
stored without the cutoff only when the cutoff hid nothing in it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, cast

from services.market_data.daily import assemble_result, utc_text
from services.market_data.provider import SETTLED_MISSING_REASONS, BarsQuery, BarsResult

if TYPE_CHECKING:
    from services.market_data.storage import LocalMarketDataStore

IDENTITY_KEYS = ("source_id", "source_feed_scope", "adjustment", "dataset_revision")


def year_range(year: int) -> tuple[datetime, datetime]:
    return (
        datetime(year, 1, 1, tzinfo=timezone.utc),
        datetime(year + 1, 1, 1, tzinfo=timezone.utc),
    )


def _identity(result: BarsResult) -> tuple[str, ...]:
    return tuple(cast(str, result.coverage[key]) for key in IDENTITY_KEYS)


def _is_settled(result: BarsResult) -> bool:
    gaps = cast(list[dict[str, str]], result.coverage["missing_intervals"])
    return all(gap["reason"] in SETTLED_MISSING_REASONS for gap in gaps) and all(
        cast(bool, bar["is_final"]) for bar in result.bars
    )


def _slice(
    result: BarsResult, start: datetime, end: datetime
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    low, high = utc_text(start), utc_text(end)
    bars = [dict(bar) for bar in result.bars if low <= bar["interval_start_utc"] < high]
    gaps = cast(list[dict[str, str]], result.coverage["missing_intervals"])
    return bars, [gap for gap in gaps if low <= gap["start_utc"] < high]


class DailyBarsCache:
    def __init__(
        self,
        store: LocalMarketDataStore,
        source_id: str,
        fetch: Callable[[BarsQuery], BarsResult],
    ) -> None:
        self.store = store
        self.source_id = source_id
        self.fetch = fetch

    def get(self, query: BarsQuery) -> BarsResult:
        last_year = (query.end_utc - timedelta(microseconds=1)).year
        years = list(range(query.start_utc.year, last_year + 1))
        cached = {year: chunk for year in years if (chunk := self._read(query, year)) is not None}
        needed = [year for year in years if year not in cached]
        fetched: BarsResult | None = None
        if needed:
            fetched = self._fetch_years(query, needed[0], needed[-1])
        identities = {_identity(chunk) for chunk in cached.values()}
        if fetched is not None:
            identities.add(_identity(fetched))
        if len(identities) > 1:
            # A stored year has another revision or source: never mix them.
            cached = {}
            fetched = self._fetch_years(query, years[0], years[-1])

        bars: list[dict[str, Any]] = []
        missing: list[dict[str, str]] = []
        for year in years:
            piece = cached.get(year, fetched)
            assert piece is not None
            start, end = year_range(year)
            year_bars, year_gaps = _slice(
                piece, max(start, query.start_utc), min(end, query.end_utc)
            )
            bars.extend(year_bars)
            missing.extend(year_gaps)
        exemplar = fetched or next(iter(cached.values()))
        source_id, feed_scope, adjustment, revision = _identity(exemplar)
        return assemble_result(
            query,
            "1d",
            bars,
            missing,
            len(bars) + len(missing),
            source_id,
            feed_scope,
            adjustment,
            revision,
        )

    def _year_query(self, query: BarsQuery, year: int) -> BarsQuery:
        start, end = year_range(year)
        return BarsQuery(query.instrument_id, "1d", start, end, None)

    def _read(self, query: BarsQuery, year: int) -> BarsResult | None:
        from services.market_data.storage import AmbiguousRevisionError

        try:
            chunk = self.store.read_exact(self._year_query(query, year), self.source_id)
        except AmbiguousRevisionError:
            return None
        if chunk is None or not _is_settled(chunk):
            return None
        cutoff = query.as_of_utc
        if cutoff is not None and any(
            cast(str, bar["available_at_utc"]) > utc_text(cutoff) for bar in chunk.bars
        ):
            return None
        return chunk

    def _fetch_years(self, query: BarsQuery, first: int, last: int) -> BarsResult:
        start, _ = year_range(first)
        _, end = year_range(last)
        fetched = self.fetch(replace(query, start_utc=start, end_utc=end))
        for year in range(first, last + 1):
            self._store_year(query, year, fetched)
        return fetched

    def _store_year(self, query: BarsQuery, year: int, fetched: BarsResult) -> None:
        start, end = year_range(year)
        bars, gaps = _slice(fetched, start, end)
        if not all(gap["reason"] in SETTLED_MISSING_REASONS for gap in gaps):
            return  # also skips years the cutoff hid anything in
        if not all(cast(bool, bar["is_final"]) for bar in bars):
            return
        source_id, feed_scope, adjustment, revision = _identity(fetched)
        chunk = assemble_result(
            self._year_query(query, year),
            "1d",
            bars,
            gaps,
            len(bars) + len(gaps),
            source_id,
            feed_scope,
            adjustment,
            revision,
        )
        self.store.write_result(chunk)
