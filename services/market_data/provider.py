from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol


@dataclass(frozen=True)
class BarsQuery:
    instrument_id: str
    timeframe: str
    start_utc: datetime
    end_utc: datetime
    as_of_utc: datetime | None = None


@dataclass(frozen=True)
class BarsResult:
    bars: tuple[dict[str, Any], ...]
    coverage: dict[str, Any]

    def to_document(self) -> dict[str, Any]:
        return {
            "kind": "market_bars_response",
            "schema_version": "0.1",
            "bars": [dict(bar) for bar in self.bars],
            "coverage": dict(self.coverage),
        }


class MarketDataError(ValueError):
    """Base class for safe provider-facing errors."""


class InvalidBarsQueryError(MarketDataError):
    """The caller requested a range the provider contract cannot represent."""


class InstrumentNotFoundError(MarketDataError):
    """The requested instrument is not present in this provider."""


class FixtureIntegrityError(MarketDataError):
    """A synthetic fixture violates the canonical market-data contract."""


def validate_bars_query(query: BarsQuery) -> None:
    """Provider-independent rules, checked before any cache lookup.

    Cache keys use whole-second UTC text. A sub-second cutoff would otherwise share a
    key with an earlier cutoff and could serve bars published after it.
    """
    timestamps = (
        ("start_utc", query.start_utc),
        ("end_utc", query.end_utc),
        ("as_of_utc", query.as_of_utc),
    )
    for name, value in timestamps:
        if value is None:
            continue
        if value.utcoffset() != timedelta(0):
            raise InvalidBarsQueryError(f"{name} must be UTC")
        if value.microsecond:
            raise InvalidBarsQueryError(f"{name} must not have sub-second precision")
    if query.end_utc <= query.start_utc:
        raise InvalidBarsQueryError("end_utc must be after start_utc")


class MarketDataProvider(Protocol):
    @property
    def source_id(self) -> str: ...

    def capabilities(self) -> dict[str, Any]: ...

    def instruments(self) -> list[dict[str, Any]]:
        """Items of a `market_instruments_response`: instrument, revision, session dates."""
        ...

    def get_bars(self, query: BarsQuery) -> BarsResult:
        """Bars for the query as a result already validated against the shared contract."""
        ...
