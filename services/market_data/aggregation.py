from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, cast

from packages.contracts.validator import validate_document
from services.market_data.calendar import TradingSessionName, UsEquityCalendar

# Any whole number of minutes from 2 up to a full extended session, written "Nm".
AggregateTimeframe = str
MIN_AGGREGATE_MINUTES = 2
MAX_AGGREGATE_MINUTES = 960
_TIMEFRAME = re.compile(r"^([1-9][0-9]{0,2})m$")


def timeframe_minutes(timeframe: str) -> int | None:
    """Minutes in an aggregate timeframe such as "3m" or "120m"; None if unsupported."""
    match = _TIMEFRAME.fullmatch(timeframe)
    if match is None:
        return None
    minutes = int(match.group(1))
    return minutes if MIN_AGGREGATE_MINUTES <= minutes <= MAX_AGGREGATE_MINUTES else None


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
        source_validated: bool = False,
        no_trade_minutes: frozenset[datetime] = frozenset(),
    ) -> tuple[dict[str, Any], ...]:
        """Aggregate canonical 1m bars into session buckets.

        Pass `source_validated=True` only for bars taken from a contract-validated
        response; anything else is validated bar by bar first. `no_trade_minutes` are
        minute starts the provider settled as having no trades: they are not expected
        source bars, so a bucket is not marked partial for them.
        """
        if timeframe_minutes(timeframe) is None:
            raise AggregationError(
                f"target timeframe must be {MIN_AGGREGATE_MINUTES}m to {MAX_AGGREGATE_MINUTES}m"
            )
        if as_of_utc is not None and as_of_utc.utcoffset() != timedelta(0):
            raise AggregationError("as_of_utc must be UTC")

        source = [deepcopy(bar) for bar in bars]
        if not source:
            return ()
        for bar in source:
            if not source_validated:
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

        for bar in visible:
            start = _parse_utc(cast(str, bar["interval_start_utc"]))
            end = _parse_utc(cast(str, bar["interval_end_utc"]))
            if end - start != timedelta(minutes=1) or start.second or start.microsecond:
                raise AggregationError("source bar must be an aligned one-minute interval")
            identity = (cast(str, bar["instrument_id"]), cast(str, bar["interval_start_utc"]))
            if identity in identities:
                raise AggregationError("duplicate source minute")
            identities.add(identity)

            session_date, session, bucket_start, bucket_end = self.bucket_bounds(start, timeframe)
            if bar["session"] != session:
                raise AggregationError("declared bar session conflicts with exchange calendar")
            grouped[(session_date, session, bucket_start, bucket_end)].append(bar)

        return tuple(
            self._aggregate_bucket(
                group, timeframe, session, bucket_start, bucket_end, no_trade_minutes
            )
            # Order by time: sorting the keys would order sessions by name (post < pre).
            for (_date, session, bucket_start, bucket_end), group in sorted(
                grouped.items(), key=lambda item: item[0][2]
            )
        )

    def bucket_bounds(
        self, start_utc: datetime, timeframe: AggregateTimeframe
    ) -> tuple[str, TradingSessionName, datetime, datetime]:
        """Session date, session and [start, end) of the bucket holding one source minute."""
        end_utc = start_utc + timedelta(minutes=1)
        classified = self.calendar.classify_interval(start_utc, end_utc)
        if classified not in {"pre", "regular", "post"}:
            raise AggregationError("source bar is outside a supported trading session")
        session = cast(TradingSessionName, classified)
        window = self.calendar.window_for_timestamp(start_utc)
        if window is None:
            raise AggregationError("source bar has no exchange session")
        session_start, session_end = window.bounds(session)
        target_minutes = timeframe_minutes(timeframe)
        if target_minutes is None:
            raise AggregationError(f"unsupported timeframe {timeframe!r}")
        offset_minutes = int((start_utc - session_start).total_seconds() // 60)
        bucket_start = session_start + timedelta(
            minutes=(offset_minutes // target_minutes) * target_minutes
        )
        bucket_end = min(bucket_start + timedelta(minutes=target_minutes), session_end)
        return window.session_date.isoformat(), session, bucket_start, bucket_end

    def derived_revision(self, source_revision: str, timeframe: AggregateTimeframe) -> str:
        return f"{source_revision}:agg:{timeframe}:{self.calendar.extended_hours_policy_version}"

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

    @staticmethod
    def _available_at(ordered: list[dict[str, Any]], bucket_end: datetime, complete: bool) -> str:
        """When the bar could be known: its latest source bar's availability. A complete
        bucket is also never known before it ends plus the source's publication delay, even
        when its last minutes had no trades; a partial one is the view of what had arrived."""
        # Compare instants, not text: "...:00.5Z" sorts before "...:00Z" as a string.
        latest = max(ordered, key=lambda bar: _parse_utc(cast(str, bar["available_at_utc"])))
        if not complete:
            return cast(str, latest["available_at_utc"])
        available = _parse_utc(cast(str, latest["available_at_utc"]))
        # The source's publication delay, read from its newest minute.
        last = ordered[-1]
        delay = _parse_utc(cast(str, last["available_at_utc"])) - _parse_utc(
            cast(str, last["interval_end_utc"])
        )
        floor = bucket_end + max(delay, timedelta(0))
        # Keep the source's own text (it may carry fractions of a second) when it is later.
        return cast(str, latest["available_at_utc"]) if available >= floor else _format_utc(floor)

    def _aggregate_bucket(
        self,
        bars: list[dict[str, Any]],
        timeframe: AggregateTimeframe,
        session: TradingSessionName,
        bucket_start: datetime,
        bucket_end: datetime,
        no_trade_minutes: frozenset[datetime] = frozenset(),
    ) -> dict[str, Any]:
        ordered = sorted(bars, key=lambda bar: cast(str, bar["interval_start_utc"]))
        settled_empty = sum(1 for minute in no_trade_minutes if bucket_start <= minute < bucket_end)
        expected_count = int((bucket_end - bucket_start).total_seconds() // 60) - settled_empty
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
            "dataset_revision": self.derived_revision(source_revision, timeframe),
            "available_at_utc": self._available_at(ordered, bucket_end, quality == "complete"),
        }
        validate_document(result)
        return result
