from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal, cast

from packages.contracts.validator import validate_document
from services.market_data.calendar import TradingSessionName, UsEquityCalendar

AggregateTimeframe = Literal["5m", "15m", "30m", "60m"]
TARGET_MINUTES: dict[AggregateTimeframe, int] = {
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "60m": 60,
}


class AggregationError(ValueError):
    """Source bars cannot be safely aggregated under one lineage."""


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() != timedelta(0):
        raise AggregationError("bar timestamps must be UTC")
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


class MinuteBarAggregator:
    """Session-aligned, provenance-preserving aggregation from canonical 1m bars."""

    def __init__(self, calendar: UsEquityCalendar | None = None) -> None:
        self.calendar = calendar or UsEquityCalendar("XNAS")

    def aggregate(
        self,
        bars: Iterable[dict[str, Any]],
        timeframe: AggregateTimeframe,
        *,
        as_of_utc: datetime | None = None,
    ) -> tuple[dict[str, Any], ...]:
        if timeframe not in TARGET_MINUTES:
            raise AggregationError("target timeframe must be 5m, 15m, 30m, or 60m")
        if as_of_utc is not None and as_of_utc.utcoffset() != timedelta(0):
            raise AggregationError("as_of_utc must be UTC")

        source = [deepcopy(bar) for bar in bars]
        if not source:
            return ()
        for bar in source:
            validate_document(bar)
            if bar["timeframe"] != "1m":
                raise AggregationError("only canonical 1m bars can be aggregated")
        self._validate_common_lineage(source)

        visible = [
            bar
            for bar in source
            if as_of_utc is None or _parse_utc(cast(str, bar["available_at_utc"])) <= as_of_utc
        ]
        identities: set[tuple[str, str]] = set()
        grouped: defaultdict[
            tuple[str, TradingSessionName, datetime, datetime], list[dict[str, Any]]
        ] = defaultdict(list)
        target_minutes = TARGET_MINUTES[timeframe]

        for bar in visible:
            start = _parse_utc(cast(str, bar["interval_start_utc"]))
            end = _parse_utc(cast(str, bar["interval_end_utc"]))
            if end - start != timedelta(minutes=1) or start.second or start.microsecond:
                raise AggregationError("source bar must be an aligned one-minute interval")
            identity = (cast(str, bar["instrument_id"]), cast(str, bar["interval_start_utc"]))
            if identity in identities:
                raise AggregationError("duplicate source minute")
            identities.add(identity)

            classified = self.calendar.classify_interval(start, end)
            if classified not in {"pre", "regular", "post"}:
                raise AggregationError("source bar is outside a supported trading session")
            session = cast(TradingSessionName, classified)
            if bar["session"] != session:
                raise AggregationError("declared bar session conflicts with exchange calendar")
            window = self.calendar.window_for_timestamp(start)
            if window is None:
                raise AggregationError("source bar has no exchange session")
            session_start, session_end = window.bounds(session)
            offset_minutes = int((start - session_start).total_seconds() // 60)
            bucket_start = session_start + timedelta(
                minutes=(offset_minutes // target_minutes) * target_minutes
            )
            bucket_end = min(bucket_start + timedelta(minutes=target_minutes), session_end)
            grouped[(window.session_date.isoformat(), session, bucket_start, bucket_end)].append(
                bar
            )

        return tuple(
            self._aggregate_bucket(group, timeframe, session, bucket_start, bucket_end)
            for (_date, session, bucket_start, bucket_end), group in sorted(grouped.items())
        )

    def _validate_common_lineage(self, bars: list[dict[str, Any]]) -> None:
        keys = (
            "instrument_id",
            "source_id",
            "source_feed_scope",
            "volume_unit",
            "adjustment",
            "dataset_revision",
        )
        first = bars[0]
        for bar in bars[1:]:
            for key in keys:
                if bar[key] != first[key]:
                    raise AggregationError(f"cannot mix source bars with different {key}")
        for bar in bars:
            lineage = cast(dict[str, Any], bar["lineage"])
            if lineage["calendar_id"] != self.calendar.calendar_id:
                raise AggregationError("source bar calendar does not match aggregator calendar")
            if lineage["calendar_version"] != self.calendar.calendar_version:
                raise AggregationError(
                    "source bar calendar version does not match aggregator calendar"
                )

    def _aggregate_bucket(
        self,
        bars: list[dict[str, Any]],
        timeframe: AggregateTimeframe,
        session: TradingSessionName,
        bucket_start: datetime,
        bucket_end: datetime,
    ) -> dict[str, Any]:
        ordered = sorted(bars, key=lambda bar: cast(str, bar["interval_start_utc"]))
        expected_count = int((bucket_end - bucket_start).total_seconds() // 60)
        observed_count = len(ordered)
        has_provisional = any(
            not cast(bool, bar["is_final"]) or bar["quality"] == "provisional" for bar in ordered
        )
        if has_provisional:
            quality = "provisional"
        elif observed_count == expected_count and all(
            bar["quality"] == "complete" for bar in ordered
        ):
            quality = "complete"
        else:
            quality = "partial"

        first = ordered[0]
        source_revision = cast(str, first["dataset_revision"])
        result: dict[str, Any] = {
            "kind": "price_bar",
            "schema_version": "0.1",
            "instrument_id": first["instrument_id"],
            "timeframe": timeframe,
            "interval_start_utc": _format_utc(bucket_start),
            "interval_end_utc": _format_utc(bucket_end),
            "open": first["open"],
            "high": _decimal_text(max(Decimal(cast(str, bar["high"])) for bar in ordered)),
            "low": _decimal_text(min(Decimal(cast(str, bar["low"])) for bar in ordered)),
            "close": ordered[-1]["close"],
            "volume": _decimal_text(
                sum((Decimal(cast(str, bar["volume"])) for bar in ordered), start=Decimal(0))
            ),
            "volume_unit": first["volume_unit"],
            "session": session,
            "is_final": quality == "complete",
            "quality": quality,
            "lineage": {
                "kind": "aggregated_1m",
                "source_timeframe": "1m",
                "expected_source_bar_count": expected_count,
                "observed_source_bar_count": observed_count,
                "source_dataset_revision": source_revision,
                "calendar_id": self.calendar.calendar_id,
                "calendar_version": self.calendar.calendar_version,
            },
            "adjustment": first["adjustment"],
            "source_id": first["source_id"],
            "source_feed_scope": first["source_feed_scope"],
            "dataset_revision": (
                f"{source_revision}:agg:{timeframe}:{self.calendar.extended_hours_policy_version}"
            ),
            # Compare instants, not text: "...:00.5Z" sorts before "...:00Z" as a string.
            "available_at_utc": max(
                ordered, key=lambda bar: _parse_utc(cast(str, bar["available_at_utc"]))
            )["available_at_utc"],
        }
        validate_document(result)
        return result
