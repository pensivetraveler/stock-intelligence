"""Choose the upstream market-data provider from the environment.

Synthetic data is the default; a real vendor is used only when explicitly named, so no
vendor is ever a silent default (docs/PROJECT_INSTRUCTIONS.md).
"""

from __future__ import annotations

import os

from services.market_data.fake import FakeMarketDataProvider
from services.market_data.provider import MarketDataProvider

PROVIDERS = ("fake", "alpaca")


class ProviderConfigError(ValueError):
    """SIW_MARKET_DATA_PROVIDER or the chosen provider's settings are unusable."""


def provider_from_env() -> MarketDataProvider:
    choice = os.getenv("SIW_MARKET_DATA_PROVIDER", "fake").strip().lower()
    if choice == "fake":
        return FakeMarketDataProvider.from_path()
    if choice == "alpaca":
        # Imported only when chosen, so the default path never touches vendor code.
        from services.market_data.alpaca import (
            AlpacaConfig,
            AlpacaConfigError,
            AlpacaMarketDataProvider,
        )

        try:
            return AlpacaMarketDataProvider(AlpacaConfig.from_env())
        except AlpacaConfigError as exc:
            raise ProviderConfigError(str(exc)) from exc
    raise ProviderConfigError(f"SIW_MARKET_DATA_PROVIDER must be one of {', '.join(PROVIDERS)}")
