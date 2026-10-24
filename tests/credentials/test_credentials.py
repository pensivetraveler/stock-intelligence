"""Registered provider keys: keychain-only secrets, ordering and rate-limit failover."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from services.credentials.catalog import SECRET_ENV_NAMES, adapter_settings
from services.credentials.pool import PooledProvider
from services.credentials.store import (
    CredentialError,
    CredentialNotFoundError,
    CredentialRecord,
    CredentialStore,
    MemorySecretBackend,
    SecretStorageError,
)
from services.market_data.provider import (
    BarsQuery,
    BarsResult,
    ProviderAuthError,
    ProviderRateLimitError,
)

SECRET = "test-secret-not-real"
NOW = datetime(2026, 10, 19, 1, 0, tzinfo=timezone.utc)
KIS_SECRETS = {"app_key": "test-key-not-real", "app_secret": SECRET}


def make_store(tmp_path: Path) -> tuple[CredentialStore, MemorySecretBackend]:
    backend = MemorySecretBackend()
    counter = iter(range(1, 100))
    store = CredentialStore(
        tmp_path, backend, now=lambda: NOW, new_id=lambda: f"{next(counter):032x}"
    )
    return store, backend


def test_secrets_live_only_in_the_keychain(tmp_path: Path) -> None:
    store, backend = make_store(tmp_path)
    record = store.add("kis", " 해외주식 ", "1234", KIS_SECRETS, "real")

    assert record.label == "해외주식"
    assert record.account_suffix == "1234"
    assert (record.priority, record.enabled, record.secret_revision) == (1, True, 1)
    assert json.loads(backend.items[record.keychain_name]) == KIS_SECRETS
    assert SECRET.encode() not in store.database_path.read_bytes()
    assert SECRET not in repr(record)
    assert store.secrets(record) == KIS_SECRETS


def test_registration_validates_before_storing_anything(tmp_path: Path) -> None:
    store, backend = make_store(tmp_path)
    with pytest.raises(CredentialError, match="last 4 digits"):
        store.add("kis", "a", "12345678", KIS_SECRETS, "real")
    with pytest.raises(CredentialError, match="not per account"):
        store.add("alpaca", "a", "1234", {"api_key_id": "x", "api_secret_key": "y"})
    with pytest.raises(CredentialError, match="app_key, app_secret"):
        store.add("kis", "a", None, {"app_key": "x"}, "real")
    with pytest.raises(CredentialError, match="app_secret must be"):
        store.add("kis", "a", None, {"app_key": "x", "app_secret": "  "}, "real")
    with pytest.raises(CredentialError, match="unknown provider"):
        store.add("massive", "a", None, {})
    assert backend.items == {}
    assert store.list() == []


def test_duplicate_label_keeps_the_keychain_clean(tmp_path: Path) -> None:
    store, backend = make_store(tmp_path)
    store.add("kis", "main", None, KIS_SECRETS, "real")
    with pytest.raises(CredentialError, match="already named"):
        store.add("kis", "main", None, KIS_SECRETS, "real")
    assert len(backend.items) == 1
    # The same label is fine for another provider.
    store.add("toss", "main", None, {"client_id": "c", "client_secret": SECRET}, "real")


def test_update_move_replace_and_delete(tmp_path: Path) -> None:
    store, backend = make_store(tmp_path)
    first = store.add("kis", "first", None, KIS_SECRETS, "real")
    second = store.add("kis", "second", "9876", KIS_SECRETS, "real")
    assert [item.label for item in store.list("kis")] == ["first", "second"]

    store.move(second.credential_id, "up")
    assert [item.label for item in store.list("kis")] == ["second", "first"]
    store.move(second.credential_id, "up")  # already first: unchanged
    assert [item.label for item in store.list("kis")] == ["second", "first"]

    updated = store.update(second.credential_id, account_suffix=None, enabled=False)
    assert (updated.account_suffix, updated.enabled, updated.label) == (None, False, "second")

    replaced = store.replace_secrets(first.credential_id, {"app_key": "k2", "app_secret": "s2"})
    assert replaced.secret_revision == 2
    assert store.secrets(replaced) == {"app_key": "k2", "app_secret": "s2"}

    store.delete(first.credential_id)
    assert first.keychain_name not in backend.items
    with pytest.raises(CredentialNotFoundError):
        store.get(first.credential_id)


def test_missing_keychain_entry_asks_to_reenter(tmp_path: Path) -> None:
    store, backend = make_store(tmp_path)
    record = store.add("kis", "main", None, KIS_SECRETS, "real")
    backend.items.clear()
    with pytest.raises(SecretStorageError, match="enter it again"):
        store.secrets(record)


def test_failed_row_insert_removes_the_keychain_entry(tmp_path: Path) -> None:
    store, backend = make_store(tmp_path)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("DROP TABLE credentials")
    with pytest.raises(sqlite3.Error):
        store.add("kis", "main", None, KIS_SECRETS, "real")
    assert backend.items == {}


def test_adapter_settings_drop_environment_keys() -> None:
    environ = {name: "from-env" for name in SECRET_ENV_NAMES} | {"SIW_KIS_SYMBOLS": "SPY:ARCX"}
    settings = adapter_settings(environ, "kis", KIS_SECRETS)
    assert settings["SIW_KIS_APP_KEY"] == "test-key-not-real"
    assert settings["SIW_KIS_APP_SECRET"] == SECRET
    assert settings["SIW_KIS_SYMBOLS"] == "SPY:ARCX"
    assert "from-env" not in settings.values()


class StubAdapter:
    source_id = "stub-us"

    def __init__(self, name: str, outcomes: list[Any]) -> None:
        self.name = name
        self.outcomes = outcomes
        self.calls = 0
        self.last_raw_sample = {"from": name}

    def capabilities(self) -> dict[str, Any]:
        return {}

    def instruments(self) -> list[dict[str, Any]]:
        return [{"listing": True}]

    def get_bars(self, query: BarsQuery) -> BarsResult:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def pool_for(
    store: CredentialStore, behaviour: dict[str, list[Any]], clock: list[float]
) -> tuple[PooledProvider, dict[str, StubAdapter]]:
    built: dict[str, StubAdapter] = {}

    def build(secrets: Any, environment: str | None = None) -> StubAdapter:
        name = "listing" if secrets is None else secrets["app_key"]
        built[name] = StubAdapter(name, behaviour.get(name, []))
        return built[name]

    pool = PooledProvider(
        "kis",
        records=lambda: store.list("kis"),
        secrets=store.secrets,
        build=build,
        cooldown_seconds=60,
        monotonic=lambda: clock[0],
        now=lambda: NOW,
    )
    return pool, built


QUERY = BarsQuery(
    "us:spy:arcx:2026",
    "1m",
    datetime(2026, 10, 16, 13, 30, tzinfo=timezone.utc),
    datetime(2026, 10, 16, 14, 0, tzinfo=timezone.utc),
)


def add(store: CredentialStore, key: str) -> CredentialRecord:
    return store.add("kis", key, None, {"app_key": key, "app_secret": SECRET}, "real")


def test_pool_fails_over_on_rate_limit_and_cools_the_key(tmp_path: Path) -> None:
    store, _backend = make_store(tmp_path)
    first, second = add(store, "a"), add(store, "b")
    result = BarsResult((), {})
    clock = [0.0]
    pool, built = pool_for(
        store,
        {"a": [ProviderRateLimitError("limit"), result], "b": [result, result]},
        clock,
    )

    assert pool.instruments() == [{"listing": True}]
    assert pool.get_bars(QUERY) is result
    assert (built["a"].calls, built["b"].calls) == (1, 1)
    assert pool.last_credential_id == second.credential_id
    assert pool.last_raw_sample == {"from": "b"}
    status = pool.status()
    assert status[first.credential_id]["state"] == "cooling"
    assert status[first.credential_id]["cooling_until_utc"] == "2026-10-19T01:01:00Z"
    assert status[second.credential_id]["state"] == "ok"

    pool.get_bars(QUERY)  # a still cools down: b again, a not called
    assert (built["a"].calls, built["b"].calls) == (1, 2)

    clock[0] = 61.0  # cooled down: the first key is preferred again
    pool.get_bars(QUERY)
    assert built["a"].calls == 2
    assert pool.status()[first.credential_id]["state"] == "ok"


def test_pool_reports_when_every_key_is_limited(tmp_path: Path) -> None:
    store, _backend = make_store(tmp_path)
    add(store, "a")
    add(store, "b")
    pool, _built = pool_for(
        store,
        {"a": [ProviderRateLimitError("x")], "b": [ProviderRateLimitError("y")]},
        [0.0],
    )
    with pytest.raises(ProviderRateLimitError, match="every key hit the rate limit"):
        pool.get_bars(QUERY)
    with pytest.raises(ProviderRateLimitError, match="every key is cooling down"):
        pool.get_bars(QUERY)


def test_pool_names_its_adapters_daily_revision(tmp_path: Path) -> None:
    store, _backend = make_store(tmp_path)
    pool, built = pool_for(store, {}, [0.0])
    assert pool.daily_dataset_revision is None  # the stub names none
    built["listing"].daily_dataset_revision = "stub-r2:1d-r2"  # type: ignore[attr-defined]
    assert pool.daily_dataset_revision == "stub-r2:1d-r2"


def test_pool_does_not_hide_auth_errors_behind_other_keys(tmp_path: Path) -> None:
    store, _backend = make_store(tmp_path)
    first = add(store, "a")
    add(store, "b")
    pool, built = pool_for(store, {"a": [ProviderAuthError("refused")]}, [0.0])
    with pytest.raises(ProviderAuthError, match="refused"):
        pool.get_bars(QUERY)
    assert built["b"].calls == 0
    assert pool.status()[first.credential_id]["last_error"] == "ProviderAuthError: refused"


def test_pool_follows_registry_changes_and_keeps_adapters(tmp_path: Path) -> None:
    store, _backend = make_store(tmp_path)
    pool, built = pool_for(store, {}, [0.0])
    with pytest.raises(ProviderAuthError, match="Keys screen"):
        pool.get_bars(QUERY)

    record = add(store, "a")
    built_before = dict(built)
    store.update(record.credential_id, enabled=False)
    with pytest.raises(ProviderAuthError, match="Keys screen"):
        pool.get_bars(QUERY)

    store.update(record.credential_id, enabled=True)
    result = BarsResult((), {})
    pool._candidates()
    adapter = built["a"]
    adapter.outcomes.extend([result, result])
    pool.get_bars(QUERY)
    pool.get_bars(QUERY)
    assert built["a"] is adapter  # reused, so its token cache survives
    assert set(built_before) == {"listing"}

    store.replace_secrets(record.credential_id, {"app_key": "a2", "app_secret": SECRET})
    pool._candidates()
    assert "a2" in built  # new secret revision: rebuilt with the new key
