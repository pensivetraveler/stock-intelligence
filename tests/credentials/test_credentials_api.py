"""Keys screen API: write-only secrets, local-only access, contract-shaped responses."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from packages.contracts.validator import validate_document
from services.api.main import create_app
from services.credentials.pool import PooledProvider
from services.credentials.store import CredentialStore, MemorySecretBackend
from services.market_data.provider import BarsQuery, ProviderRateLimitError

SECRET = "test-secret-not-real"
NOW = datetime(2026, 10, 19, 1, 0, tzinfo=timezone.utc)
LOCAL = "http://127.0.0.1:8000"


@pytest.fixture
def store(tmp_path: Path) -> CredentialStore:
    return CredentialStore(tmp_path, MemorySecretBackend(), now=lambda: NOW)


@pytest.fixture
def client(store: CredentialStore, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.delenv("SIW_DATA_DIR", raising=False)
    monkeypatch.delenv("SIW_MARKET_DATA_PROVIDER", raising=False)
    with TestClient(create_app(credential_store=store), base_url=LOCAL) as test_client:
        yield test_client


def kis_body(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "provider": "kis",
        "label": "해외주식",
        "account_suffix": "1234",
        "environment": "real",
        "secrets": {"app_key": "test-key-not-real", "app_secret": SECRET},
    }
    body.update(changes)
    return body


def test_register_list_and_never_echo_secrets(client: TestClient) -> None:
    created = client.post("/v1/credentials", json=kis_body())
    assert created.status_code == 201
    assert created.headers["cache-control"] == "no-store"
    validate_document(created.json())
    assert created.json()["active"] is False  # the running provider is fake
    assert created.json()["status"] is None

    listing = client.get("/v1/credentials")
    assert listing.status_code == 200
    validate_document(listing.json())
    assert listing.json()["active_provider"] == "fake"
    assert [p["provider"] for p in listing.json()["providers"]] == [
        "alpaca",
        "kis",
        "toss",
        "kiwoom",
        "meritz",
        "anthropic",  # the review Agent's commentary key, not a market-data source
    ]
    for response in (created, listing):
        assert SECRET not in response.text
        assert "test-key-not-real" not in response.text


def test_update_move_replace_and_delete(client: TestClient, store: CredentialStore) -> None:
    first = client.post("/v1/credentials", json=kis_body(label="a")).json()
    second = client.post("/v1/credentials", json=kis_body(label="b")).json()

    patched = client.patch(
        f"/v1/credentials/{first['credential_id']}",
        json={"account_suffix": None, "enabled": False},
    )
    assert patched.status_code == 200
    assert (patched.json()["account_suffix"], patched.json()["enabled"]) == (None, False)
    assert patched.json()["label"] == "a"

    moved = client.post(f"/v1/credentials/{second['credential_id']}/move", json={"direction": "up"})
    assert moved.json()["priority"] == 1

    replaced = client.put(
        f"/v1/credentials/{first['credential_id']}/secrets",
        json={"secrets": {"app_key": "k2", "app_secret": "s2"}},
    )
    assert replaced.status_code == 200
    assert "s2" not in replaced.text
    assert store.secrets(store.get(first["credential_id"])) == {"app_key": "k2", "app_secret": "s2"}

    assert client.delete(f"/v1/credentials/{first['credential_id']}").status_code == 204
    missing = client.delete(f"/v1/credentials/{first['credential_id']}")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "credential_not_found"


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        (kis_body(account_suffix="12345678"), 422, "validation_error"),
        (kis_body(secrets={"app_key": "only"}), 422, "invalid_credential"),
        (kis_body(provider="alpaca"), 422, "invalid_credential"),
        (kis_body(extra="x"), 422, "validation_error"),
    ],
)
def test_invalid_registrations_store_nothing_and_echo_nothing(
    client: TestClient, store: CredentialStore, body: dict[str, Any], status: int, code: str
) -> None:
    response = client.post("/v1/credentials", json=body)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert SECRET not in response.text
    assert store.list() == []


@pytest.mark.parametrize(
    "headers",
    [
        {"host": "attacker.example:8000"},
        {"origin": "https://attacker.example"},
    ],
)
def test_requests_from_outside_the_app_are_refused(
    client: TestClient, store: CredentialStore, headers: dict[str, str]
) -> None:
    for response in (
        client.post("/v1/credentials", json=kis_body(), headers=headers),
        client.get("/v1/credentials", headers=headers),
    ):
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "credential_request_refused"
    assert store.list() == []


def test_app_origin_is_accepted(client: TestClient) -> None:
    response = client.get("/v1/credentials", headers={"origin": "http://127.0.0.1:5173"})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"


def test_storage_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SIW_DATA_DIR", raising=False)
    monkeypatch.delenv("SIW_MARKET_DATA_PROVIDER", raising=False)
    with TestClient(create_app(), base_url=LOCAL) as test_client:
        response = test_client.get("/v1/credentials")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "credential_storage_unconfigured"


class LimitedAdapter:
    source_id = "kis-us"

    def capabilities(self) -> dict[str, Any]:
        return {}

    def instruments(self) -> list[dict[str, Any]]:
        return []

    def get_bars(self, query: BarsQuery) -> Any:
        raise ProviderRateLimitError("KIS rate limit reached; retry later")


def test_active_provider_keys_show_runtime_status(
    store: CredentialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SIW_DATA_DIR", raising=False)
    monkeypatch.setenv("SIW_MARKET_DATA_PROVIDER", "kis")
    record = store.add("kis", "main", None, {"app_key": "k", "app_secret": SECRET}, "real")
    pool = PooledProvider(
        "kis",
        records=lambda: store.list("kis"),
        secrets=store.secrets,
        build=lambda _secrets, _environment: LimitedAdapter(),
        now=lambda: NOW,
    )
    with TestClient(
        create_app(market_data_provider=pool, credential_store=store), base_url=LOCAL
    ) as test_client:
        with pytest.raises(ProviderRateLimitError):
            pool.get_bars(
                BarsQuery(
                    "us:spy:arcx:2026",
                    "1m",
                    datetime(2026, 10, 16, 13, 30, tzinfo=timezone.utc),
                    datetime(2026, 10, 16, 14, 0, tzinfo=timezone.utc),
                )
            )
        listing = test_client.get("/v1/credentials").json()
    validate_document(listing)
    (item,) = listing["credentials"]
    assert item["credential_id"] == record.credential_id
    assert item["active"] is True
    assert item["status"]["state"] == "cooling"
    assert item["status"]["last_error"] == "KIS rate limit reached; retry later"
