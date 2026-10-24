"""Route each request to the market's source: US listings to the configured provider,
Korean listings to Toss, the only Korean source available (stage K1 of Korean support).

Everything that is not about one instrument (capabilities, the instrument list, the symbol
directory, key status) is the US provider's, unchanged.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from services.market_data.daily import AdjustedDailyProvider, DailyBarsProvider
from services.market_data.instruments import market_of
from services.market_data.provider import (
    BarsQuery,
    BarsResult,
    InvalidBarsQueryError,
    MarketDataProvider,
    ProviderAuthError,
)


class MarketRouter:
    def __init__(
        self,
        us: MarketDataProvider,
        kr: Callable[[], MarketDataProvider | None],
    ) -> None:
        self.us = us
        self._kr = kr

    @property
    def upstream(self) -> Any:
        """What the US provider wraps (the key pool), for key status."""
        return getattr(self.us, "upstream", self.us)

    @property
    def source_id(self) -> str:
        return self.us.source_id

    @property
    def list_assets(self) -> Callable[[], list[dict[str, str]]] | None:
        return getattr(self.us, "list_assets", None)

    def capabilities(self) -> dict[str, Any]:
        return self.us.capabilities()

    def instruments(self) -> list[dict[str, Any]]:
        return self.us.instruments()

    def get_bars(self, query: BarsQuery) -> BarsResult:
        return self._for(query).get_bars(query)

    def get_daily_bars(self, query: BarsQuery) -> BarsResult:
        provider = self._for(query)
        if not isinstance(provider, DailyBarsProvider):
            raise InvalidBarsQueryError("this data provider does not offer daily bars yet")
        return provider.get_daily_bars(query)

    def get_adjusted_daily_bars(self, query: BarsQuery, adjustment: str) -> BarsResult:
        provider = self._for(query)
        if not isinstance(provider, AdjustedDailyProvider):
            raise InvalidBarsQueryError(
                f"adjusted prices are not available for {query.instrument_id} "
                f"from {provider.source_id}; use raw prices"
            )
        return provider.get_adjusted_daily_bars(query, adjustment)

    def daily_dataset_revision_for(self, query: BarsQuery) -> str | None:
        """The daily revision of the provider serving `query`, when it names one."""
        if market_of(query.instrument_id) == "KR":
            return None  # Korean daily bars are not cached under a named revision
        return cast(str | None, getattr(self.us, "daily_dataset_revision", None))

    def _for(self, query: BarsQuery) -> MarketDataProvider:
        if market_of(query.instrument_id) != "KR":
            return self.us
        provider = self._kr()
        if provider is None:
            raise ProviderAuthError(
                "Korean stocks come from Toss: register an enabled real-account Toss key "
                "on the Keys screen"
            )
        return provider
