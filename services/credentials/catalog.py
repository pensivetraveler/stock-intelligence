"""Which secret fields each market-data provider needs, and how adapters receive them.

Adapters still read their settings through `from_env` mappings. Keys never come from the
process environment: `adapter_settings` drops every key variable from the environment and
adds the values of one registered credential under the names the adapter expects.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class SecretField:
    name: str
    label: str
    env_name: str


@dataclass(frozen=True)
class ProviderKeySpec:
    provider: str
    display_name: str
    fields: tuple[SecretField, ...]
    # Broker APIs issue keys per brokerage account; a vendor key belongs to the login.
    per_account: bool

    @property
    def environment_env(self) -> str | None:
        """Setting that tells a broker adapter whether its key is for a real or demo account."""
        return f"SIW_{self.provider.upper()}_ENVIRONMENT" if self.per_account else None


CATALOG: dict[str, ProviderKeySpec] = {
    spec.provider: spec
    for spec in (
        ProviderKeySpec(
            "alpaca",
            "Alpaca",
            (
                SecretField("api_key_id", "API key ID", "APCA_API_KEY_ID"),
                SecretField("api_secret_key", "API secret key", "APCA_API_SECRET_KEY"),
            ),
            per_account=False,
        ),
        ProviderKeySpec(
            "kis",
            "한국투자증권",
            (
                SecretField("app_key", "App key", "SIW_KIS_APP_KEY"),
                SecretField("app_secret", "App secret", "SIW_KIS_APP_SECRET"),
            ),
            per_account=True,
        ),
        ProviderKeySpec(
            "toss",
            "토스증권",
            (
                SecretField("client_id", "Client ID", "SIW_TOSS_CLIENT_ID"),
                SecretField("client_secret", "Client secret", "SIW_TOSS_CLIENT_SECRET"),
            ),
            per_account=True,
        ),
        ProviderKeySpec(
            "kiwoom",
            "키움증권",
            (
                SecretField("app_key", "App key", "SIW_KIWOOM_APP_KEY"),
                SecretField("secret_key", "Secret key", "SIW_KIWOOM_SECRET_KEY"),
            ),
            per_account=True,
        ),
        ProviderKeySpec(
            "meritz",
            "메리츠증권",
            (
                SecretField("app_key", "App key", "SIW_MERITZ_APP_KEY"),
                SecretField("app_secret", "App secret", "SIW_MERITZ_APP_SECRET"),
                # Not secret, but every Meritz call must carry the calling device's MAC
                # address, so it is kept with the key it is used with.
                SecretField("mac_address", "MAC address (12 hex digits)", "SIW_MERITZ_MAC_ADDRESS"),
            ),
            per_account=True,
        ),
        # Not a market-data source: FINRA weekly ATS data for the dark pool Agent (ADR 0024).
        ProviderKeySpec(
            "finra",
            "FINRA API",
            (
                SecretField("client_id", "Client ID", "SIW_FINRA_CLIENT_ID"),
                SecretField("client_secret", "Client secret", "SIW_FINRA_CLIENT_SECRET"),
            ),
            per_account=False,
        ),
        # Not a market-data source: the Bank of Korea's USD/KRW reference rate, used to
        # check paper orders against a KRW amount limit (ADR 0037).
        ProviderKeySpec(
            "ecos",
            "한국은행 ECOS",
            (SecretField("api_key", "API key", "SIW_ECOS_API_KEY"),),
            per_account=False,
        ),
        # Not a market-data source: the review Agent's commentary (ADR 0022).
        ProviderKeySpec(
            "anthropic",
            "Claude (Anthropic)",
            (SecretField("api_key", "API key", "ANTHROPIC_API_KEY"),),
            per_account=False,
        ),
    )
}

SECRET_ENV_NAMES = frozenset(field.env_name for spec in CATALOG.values() for field in spec.fields)
# The account environment comes from the registered key, never from the process.
ENVIRONMENT_ENV_NAMES = frozenset(
    name for spec in CATALOG.values() if (name := spec.environment_env) is not None
)

# Stands in for a key when an adapter is built only to list instruments; such an adapter
# never sends a request.
LISTING_ONLY = "unused-listing-only"
_UNUSED = LISTING_ONLY


def adapter_settings(
    environ: Mapping[str, str],
    provider: str,
    secrets: Mapping[str, str] | None,
    environment: str | None = None,
) -> dict[str, str]:
    """Environment settings without any key variable, plus one credential's secrets.

    `environment` ("real" or "demo") is the registered account's; adapters default to real.
    """
    spec = CATALOG[provider]
    dropped = SECRET_ENV_NAMES | ENVIRONMENT_ENV_NAMES
    values = {name: value for name, value in environ.items() if name not in dropped}
    for field in spec.fields:
        values[field.env_name] = _UNUSED if secrets is None else secrets[field.name]
    if spec.environment_env is not None and environment is not None:
        values[spec.environment_env] = environment
    return values
