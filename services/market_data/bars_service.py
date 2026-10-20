from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, cast

from packages.contracts.validator import validate_document
from services.market_data.aggregation import (
    MAX_AGGREGATE_MINUTES,
    MIN_AGGREGATE_MINUTES,
    AggregateTimeframe,
    MinuteBarAggregator,
    timeframe_minutes,
)
from services.market_data.calendar import CalendarError
from services.market_data.daily import DailyBarsProvider, aggregate_weekly
from services.market_data.instruments import calendar_for
from services.market_data.provider import (
    SETTLED_MISSING_REASONS,
    BarsQuery,
    BarsResult,
    InvalidBarsQueryError,
    MarketDataProvider,
    validate_bars_query,
)


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class MarketBarsService:
    """Serves canonical 1m bars directly and larger timeframes by session aggregation.

    Aggregates are derived on every request from the provider's 1m result, so they
    inherit its cache, revision and point-in-time cutoff and are never stored.
    """

    def __init__(
        self,
        provider: MarketDataProvider,
        aggregator: MinuteBarAggregator | None = None,
    ) -> None:
        self.provider = provider
        self._fixed_aggregator = aggregator
        self.aggregator = aggregator or MinuteBarAggregator()

    def get_bars(self, query: BarsQuery) -> BarsResult:
        # Each instrument is aggregated on its own exchange's calendar.
        if self._fixed_aggregator is None:
            self.aggregator = MinuteBarAggregator(calendar_for(query.instrument_id))
        if query.timeframe == "1m":
            return self.provider.get_bars(query)
        if query.timeframe in ("1d", "1w"):
            return self._daily_or_weekly(query)
        if timeframe_minutes(query.timeframe) is None:
            raise InvalidBarsQueryError(
                f"timeframe must be 1m, {MIN_AGGREGATE_MINUTES}m to "
                f"{MAX_AGGREGATE_MINUTES}m, 1d or 1w"
            )
        timeframe: AggregateTimeframe = query.timeframe
        validate_bars_query(query)
        buckets = self._expected_buckets(query, timeframe)

        source = self.provider.get_bars(replace(query, timeframe="1m"))
        # Providers return contract-validated responses, so the 1m bars are not re-validated.
        source_missing = cast(list[dict[str, str]], source.coverage["missing_intervals"])
        no_trade = frozenset(
            _parse_utc(gap["start_utc"])
            for gap in source_missing
            if gap["reason"] in SETTLED_MISSING_REASONS
        )
        bars = self.aggregator.aggregate(
            source.bars,
            timeframe,
            as_of_utc=query.as_of_utc,
            source_validated=True,
            no_trade_minutes=no_trade,
        )
        observed = {cast(str, bar["interval_start_utc"]) for bar in bars}
        missing = [
            {
                "start_utc": _format_utc(start),
                "end_utc": _format_utc(end),
                "reason": self._missing_reason(source_missing, start, end),
            }
            for start, end in buckets
            if _format_utc(start) not in observed
        ]
        coverage: dict[str, Any] = {
            **source.coverage,
            "timeframe": timeframe,
            # Coverage counts buckets that have a bar; partially observed buckets are
            # present but carry `quality: partial` and `is_final: false`.
            "coverage_status": (
                "complete" if bars and not missing else ("partial" if bars else "empty")
            ),
            "observed_interval_count": len(bars),
            "expected_interval_count": len(buckets),
            "missing_intervals": missing,
            "dataset_revision": self.aggregator.derived_revision(
                cast(str, source.coverage["dataset_revision"]), timeframe
            ),
        }
        result = BarsResult(bars, coverage)
        validate_document(result.to_document())
        return result

    def _daily_or_weekly(self, query: BarsQuery) -> BarsResult:
        """1d from the provider's daily bars; 1w aggregated from those daily bars."""
        if not isinstance(self.provider, DailyBarsProvider):
            raise InvalidBarsQueryError("this data provider does not offer daily bars yet")
        if query.timeframe == "1d":
            return self.provider.get_daily_bars(query)
        daily = self.provider.get_daily_bars(replace(query, timeframe="1d"))
        return aggregate_weekly(query, daily, self.aggregator.calendar)

    def _expected_buckets(
        self, query: BarsQuery, timeframe: AggregateTimeframe
    ) -> tuple[tuple[datetime, datetime], ...]:
        try:
            minutes = self.aggregator.calendar.trading_minutes(query.start_utc, query.end_utc)
        except CalendarError as exc:
            raise InvalidBarsQueryError(str(exc)) from exc
        buckets: dict[datetime, datetime] = {}
        for start, _session in minutes:
            _date, _bucket_session, bucket_start, bucket_end = self.aggregator.bucket_bounds(
                start, timeframe
            )
            if bucket_start < query.start_utc or bucket_end > query.end_utc:
                raise InvalidBarsQueryError(
                    f"start_utc and end_utc must align to {timeframe} session bucket boundaries"
                )
            buckets[bucket_start] = bucket_end
        return tuple(sorted(buckets.items()))

    @staticmethod
    def _missing_reason(
        source_missing: list[dict[str, str]], start: datetime, end: datetime
    ) -> str:
        # A bucket without bars is missing because of its source minutes. The most
        # actionable reason wins; only a bucket whose every minute had no trades is
        # reported as settled "provider_no_bar".
        reasons = {
            item["reason"]
            for item in source_missing
            if start <= _parse_utc(item["start_utc"]) < end
        }
        for reason in (
            "not_available_as_of_cutoff",
            "outside_entitlement",
            "not_yet_final",
            "fixture_missing",
        ):
            if reason in reasons:
                return reason
        return "provider_no_bar" if reasons else "fixture_missing"
