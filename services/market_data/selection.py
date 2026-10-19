"""Choose the upstream market-data provider from the environment.

Synthetic data is the default; a real vendor is used only when explicitly named, so no
vendor is ever a silent default (docs/PROJECT_INSTRUCTIONS.md). Real providers take
their keys from the registered credentials (Keys screen), never from the environment.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path

from services.credentials.catalog import adapter_settings
from services.credentials.pool import PooledProvider
from services.credentials.store import CredentialRecord, CredentialStore, KeyringSecretBackend
from services.market_data.fake import FakeMarketDataProvider
from services.market_data.provider import MarketDataProvider

PROVIDERS = ("fake", "alpaca", "kis", "toss", "kiwoom", "meritz")
KEYED_PROVIDERS = PROVIDERS[1:]


class ProviderConfigError(ValueError):
    """SIW_MARKET_DATA_PROVIDER or the chosen provider's settings are unusable."""


def chosen_provider() -> str:
    choice = os.getenv("SIW_MARKET_DATA_PROVIDER", "fake").strip().lower()
    if choice not in PROVIDERS:
        raise ProviderConfigError(f"SIW_MARKET_DATA_PROVIDER must be one of {', '.join(PROVIDERS)}")
    return choice


def default_credential_store() -> CredentialStore:
    configured = os.getenv("SIW_DATA_DIR", "").strip()
    if not configured:
        raise ProviderConfigError(
            "set SIW_DATA_DIR: registered keys keep their labels there and secrets in the "
            "OS keychain"
        )
    return CredentialStore(Path(configured), KeyringSecretBackend())


def provider_from_env(credentials: CredentialStore | None = None) -> MarketDataProvider:
    choice = chosen_provider()
    if choice == "fake":
        return FakeMarketDataProvider.from_path()
    return pooled_provider(choice, credentials or default_credential_store())


def pooled_provider(
    choice: str,
    credentials: CredentialStore,
    *,
    only: Callable[[CredentialRecord], bool] | None = None,
) -> PooledProvider:
    """The provider served through its registered keys, optionally a subset of them."""

    def records() -> list[CredentialRecord]:
        return [item for item in credentials.list(choice) if only is None or only(item)]

    return PooledProvider(
        choice,
        records=records,
        secrets=credentials.secrets,
        build=lambda secrets: build_adapter(choice, adapter_settings(os.environ, choice, secrets)),
    )


def build_adapter(choice: str, settings: Mapping[str, str]) -> MarketDataProvider:
    """One adapter from settings that already carry a single key's secrets."""
    # Vendor modules are imported only when chosen, so the default path never loads them.
    from services.market_data.broker_common import BrokerConfigError

    values = dict(settings)
    try:
        if choice == "alpaca":
            from services.market_data.alpaca import (
                AlpacaConfig,
                AlpacaConfigError,
                AlpacaMarketDataProvider,
            )

            try:
                return AlpacaMarketDataProvider(AlpacaConfig.from_env(values))
            except AlpacaConfigError as exc:
                raise ProviderConfigError(str(exc)) from exc
        if choice == "kis":
            from services.market_data.kis import KisConfig, KisMarketDataProvider

            return KisMarketDataProvider(KisConfig.from_env(values))
        if choice == "toss":
            from services.market_data.toss import TossConfig, TossMarketDataProvider

            return TossMarketDataProvider(TossConfig.from_env(values))
        if choice == "meritz":
            from services.market_data.meritz import MeritzConfig, MeritzMarketDataProvider

            return MeritzMarketDataProvider(MeritzConfig.from_env(values))
        if choice == "kiwoom":
            from services.market_data.kiwoom import KiwoomConfig, KiwoomMarketDataProvider

            return KiwoomMarketDataProvider(KiwoomConfig.from_env(values))
    except (BrokerConfigError, ValueError) as exc:
        if isinstance(exc, ProviderConfigError):
            raise
        raise ProviderConfigError(str(exc)) from exc
    raise ProviderConfigError(f"{choice} does not use registered keys")
