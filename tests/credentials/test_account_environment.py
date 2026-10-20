"""Real (실전) and demo (모의투자) broker accounts: storage, adapter routing and the pool."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from services.credentials.catalog import adapter_settings
from services.credentials.pool import PooledProvider
from services.credentials.store import (
    CredentialError,
    CredentialStore,
    MemorySecretBackend,
)
from services.market_data.broker_common import BrokerConfigError
from services.market_data.kis import KisConfig, KisMarketDataProvider
from services.market_data.kiwoom import KiwoomConfig, KiwoomMarketDataProvider
from services.market_data.meritz import MeritzConfig
from services.market_data.provider import BarsQuery, BarsResult, ProviderAuthError
from services.market_data.selection import ProviderConfigError, build_adapter
from services.market_data.toss import TossConfig

SECRET = "test-secret-not-real"
NOW = datetime(2026, 10, 20, 1, 0, tzinfo=timezone.utc)
KIWOOM = {"app_key": "test-key-not-real", "secret_key": SECRET}


def make_store(tmp_path: Path) -> CredentialStore:
    counter = iter(range(1, 100))
    return CredentialStore(
        tmp_path, MemorySecretBackend(), now=lambda: NOW, new_id=lambda: f"{next(counter):032x}"
    )


def test_broker_keys_record_their_account_type(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    demo = store.add("kiwoom", "모의", None, KIWOOM, "demo")
    assert demo.environment == "demo"
    assert store.update(demo.credential_id, environment="real").environment == "real"

    with pytest.raises(CredentialError, match="real or demo"):
        store.add("kiwoom", "missing", None, KIWOOM)
    with pytest.raises(CredentialError, match="real or demo"):
        store.add("kiwoom", "wrong", None, KIWOOM, "paper")
    vendor = store.add("alpaca", "data", None, {"api_key_id": "x", "api_secret_key": "y"})
    assert vendor.environment is None
    with pytest.raises(CredentialError, match="no real or demo"):
        store.add("alpaca", "data2", None, {"api_key_id": "x", "api_secret_key": "y"}, "demo")


def test_keys_registered_before_the_column_are_marked_real(tmp_path: Path) -> None:
    database = tmp_path / "credentials.sqlite3"
    sql = (Path("services/credentials/migrations") / "0001_credentials.sql").read_text()
    with sqlite3.connect(database) as connection:
        connection.executescript(sql)
        connection.execute(
            "CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, name TEXT NOT NULL, "
            "checksum TEXT NOT NULL, applied_at_utc TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_migrations VALUES ('0001', '0001_credentials.sql', ?, 'x')",
            (hashlib.sha256(sql.encode()).hexdigest(),),
        )
        for provider in ("kiwoom", "alpaca"):
            connection.execute(
                "INSERT INTO credentials VALUES (?, ?, 'old', NULL, 1, 1, 1, 'x', 'x')",
                (provider.ljust(32, "0")[:32], provider),
            )
    connection.close()

    store = make_store(tmp_path)
    assert {record.provider: record.environment for record in store.list()} == {
        "alpaca": None,
        "kiwoom": "real",
    }


def test_settings_carry_the_registered_account_type_only() -> None:
    environ = {"SIW_KIWOOM_ENVIRONMENT": "demo", "SIW_KIWOOM_TIME_ZONE": "America/New_York"}
    settings = adapter_settings(environ, "kiwoom", KIWOOM, "real")
    assert settings["SIW_KIWOOM_ENVIRONMENT"] == "real"
    # The process environment never chooses the account type.
    assert "SIW_KIWOOM_ENVIRONMENT" not in adapter_settings(environ, "kiwoom", None)
    assert "SIW_ALPACA_ENVIRONMENT" not in adapter_settings(
        {}, "alpaca", {"api_key_id": "x", "api_secret_key": "y"}, None
    )


def test_demo_keys_go_to_documented_demo_servers_only() -> None:
    kiwoom = {"SIW_KIWOOM_APP_KEY": "k", "SIW_KIWOOM_SECRET_KEY": SECRET}
    real = KiwoomMarketDataProvider(KiwoomConfig.from_env(kiwoom))
    demo_config = KiwoomConfig.from_env({**kiwoom, "SIW_KIWOOM_ENVIRONMENT": "demo"})
    demo = KiwoomMarketDataProvider(demo_config)
    assert real.config.base_url == "https://api.kiwoom.com"
    assert demo_config.base_url == "https://mockapi.kiwoom.com"
    # A demo server's bars never share a revision with the real one's.
    assert demo.identity.feed_scope == "kiwoom-us-demo"
    assert demo.identity.dataset_revision != real.identity.dataset_revision

    kis = {"SIW_KIS_APP_KEY": "k", "SIW_KIS_APP_SECRET": SECRET}
    kis_demo = KisConfig.from_env({**kis, "SIW_KIS_ENVIRONMENT": "demo"})
    assert kis_demo.base_url == "https://openapivts.koreainvestment.com:29443"
    assert KisMarketDataProvider(kis_demo).identity.feed_scope == "kis-us-demo"
    # The real-domain override never redirects a demo key.
    assert (
        KisConfig.from_env(
            {**kis, "SIW_KIS_ENVIRONMENT": "demo", "SIW_KIS_BASE_URL": "https://example.test"}
        ).base_url
        == "https://openapivts.koreainvestment.com:29443"
    )

    with pytest.raises(BrokerConfigError, match="no demo"):
        TossConfig.from_env(
            {
                "SIW_TOSS_CLIENT_ID": "c",
                "SIW_TOSS_CLIENT_SECRET": SECRET,
                "SIW_TOSS_ENVIRONMENT": "demo",
            }
        )
    with pytest.raises(BrokerConfigError, match="no demo"):
        MeritzConfig.from_env(
            {
                "SIW_MERITZ_APP_KEY": "k",
                "SIW_MERITZ_APP_SECRET": SECRET,
                "SIW_MERITZ_MAC_ADDRESS": "A1B2C3D4E5F6",
                "SIW_MERITZ_ENVIRONMENT": "demo",
            }
        )
    with pytest.raises(ProviderConfigError, match="real or demo"):
        build_adapter("kiwoom", {**kiwoom, "SIW_KIWOOM_ENVIRONMENT": "paper"})


QUERY = BarsQuery(
    "us:aapl:xnas:2026",
    "1m",
    datetime(2026, 10, 16, 13, 30, tzinfo=timezone.utc),
    datetime(2026, 10, 16, 14, 0, tzinfo=timezone.utc),
)


class Stub:
    source_id = "stub"

    def __init__(self, environment: str | None) -> None:
        self.environment = environment

    def capabilities(self) -> dict[str, Any]:
        return {}

    def instruments(self) -> list[dict[str, Any]]:
        return []

    def get_bars(self, query: BarsQuery) -> BarsResult:
        return BarsResult((), {"environment": self.environment})


def test_pool_rebuilds_a_key_when_its_account_type_changes(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    record = store.add("kiwoom", "main", None, KIWOOM, "demo")
    built: list[str | None] = []

    def build(secrets: Any, environment: str | None) -> Stub:
        if environment == "demo" and secrets is not None and secrets["app_key"] == "refuse":
            raise ProviderConfigError("Toss Securities documents no demo (모의투자) domain")
        built.append(environment)
        return Stub(environment)

    pool = PooledProvider(
        "kiwoom", records=lambda: store.list("kiwoom"), secrets=store.secrets, build=build
    )
    assert pool.get_bars(QUERY).coverage == {"environment": "demo"}
    store.update(record.credential_id, environment="real")
    assert pool.get_bars(QUERY).coverage == {"environment": "real"}
    assert built == [None, "demo", "real"]

    store.replace_secrets(record.credential_id, {"app_key": "refuse", "secret_key": SECRET})
    store.update(record.credential_id, environment="demo")
    with pytest.raises(ProviderAuthError, match="no demo"):
        pool.get_bars(QUERY)
