from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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


class MarketDataProvider(Protocol):
    @property
    def source_id(self) -> str: ...

    def capabilities(self) -> dict[str, Any]: ...

    def get_bars(self, query: BarsQuery) -> BarsResult: ...
