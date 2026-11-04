"""The live-order API (ADR 0047): who may start, approve and confirm, and the Kiwoom key guard."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from packages.contracts.validator import validate_document
from services.api.main import create_app
from services.backtest.jobs import BacktestRunner
from services.backtest.store import BacktestStore
from services.credentials.store import CredentialStore, MemorySecretBackend
from services.live_trading.kiwoom_live import live_account_ref
from services.live_trading.store import LiveOrderStore
from services.market_data.kiwoom_account import KiwoomAccountCredentials, KiwoomAccountSession
from tests.paper_trading.test_service_and_api import (
    AAPL,
    AGENT,
    LOCAL,
    NOW,
    RESEARCH,
    RULES,
    minute_bars,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, dict[str, str]]]:
    monkeypatch.setenv("SIW_DATA_DIR", str(tmp_path))
    keys = CredentialStore(tmp_path, MemorySecretBackend())
    demo = keys.add(
        "kiwoom", "paper", None, {"app_key": "app-key", "secret_key": "s"}, environment="demo"
    )
    real = keys.add(
        "kiwoom", "live", None, {"app_key": "live-key", "secret_key": "s"}, environment="real"
    )
    runner = BacktestRunner(
        BacktestStore(tmp_path / "backtests.sqlite3", lambda: NOW), lambda: minute_bars
    )
    session = KiwoomAccountSession(KiwoomAccountCredentials("live-key", "s", "real"))
    with TestClient(
        create_app(
            credential_store=keys,
            backtest_runner=runner,
            clock=lambda: NOW,
            kiwoom_session=lambda: session,
        ),
        base_url=LOCAL,
    ) as test_client:
        strategy = test_client.post(
            "/v1/backtests/strategies", json={"name": "above 100", "definition": RULES}
        ).json()
        test_client.strategy = strategy  # type: ignore[attr-defined]
        yield test_client, {"demo": demo.credential_id, "real": real.credential_id}
    runner.stop()


def _body(client: TestClient, credential_id: str, **changes: Any) -> dict[str, Any]:
    return {
        "strategy_id": client.strategy["strategy_id"],  # type: ignore[attr-defined]
        "strategy_version": client.strategy["version"],  # type: ignore[attr-defined]
        "instruments": [{"instrument_id": AAPL, "symbol": "aapl"}],
        "credential_id": credential_id,
        "max_order_amount_usd": "100",
        "max_daily_orders": 2,
        **changes,
    }


def test_lists_validate(client: Any) -> None:
    test_client, _ = client
    for path in (
        "/v1/live/status",
        "/v1/live/approvals",
        "/v1/live/intents",
        "/v1/live/executions",
        "/v1/live/confirmations",
    ):
        response = test_client.get(path)
        assert response.status_code == 200, path
        validate_document(response.json())


def test_only_the_agent_app_can_approve_arm_release_and_confirm(client: Any) -> None:
    test_client, ids = client
    for headers in ({}, RESEARCH):
        assert (
            test_client.post(
                "/v1/live/approvals", json=_body(test_client, ids["real"]), headers=headers
            ).status_code
            == 403
        )
        assert (
            test_client.post(
                "/v1/live/arm", json={"approval_id": "a" * 64}, headers=headers
            ).status_code
            == 403
        )
        assert test_client.post("/v1/live/release", headers=headers).status_code == 403
        assert (
            test_client.post(f"/v1/live/intents/{'a' * 64}/confirm", headers=headers).status_code
            == 403
        )
    # Declining stops an order, so anyone may; there is just nothing to decline.
    assert test_client.post(f"/v1/live/intents/{'a' * 64}/decline").status_code == 409


def test_approval_needs_a_real_key_a_dollar_cap_and_a_daily_count(client: Any) -> None:
    test_client, ids = client
    demo = test_client.post(
        "/v1/live/approvals", json=_body(test_client, ids["demo"]), headers=AGENT
    )
    assert (demo.status_code, demo.json()["error"]["code"]) == (422, "not_a_real_key")
    body = _body(test_client, ids["real"])
    for missing in ("max_order_amount_usd", "max_daily_orders"):
        partial = {k: v for k, v in body.items() if k != missing}
        assert (
            test_client.post("/v1/live/approvals", json=partial, headers=AGENT).status_code == 422
        )
    zero = test_client.post(
        "/v1/live/approvals", json={**body, "max_order_amount_usd": "0"}, headers=AGENT
    )
    assert zero.status_code == 422

    approval = test_client.post("/v1/live/approvals", json=body, headers=AGENT)
    assert approval.status_code == 201
    document = approval.json()
    validate_document(document)
    assert document["broker_account_ref"] == live_account_ref("live-key")
    assert document["limits"]["max_order_amount_usd"] == "100"
    assert document["limits"]["max_order_amount_krw"] is None
    assert document["limits"]["max_daily_orders"] == 2
    assert document["confirm_seconds"] == 60
    assert "live-key" not in approval.text
    # The approval is in the live ledger only.
    assert test_client.get("/v1/paper/approvals").json()["approvals"] == []


def test_arming_live_blocks_syncing_the_same_kiwoom_key(client: Any) -> None:
    test_client, ids = client
    approval = test_client.post(
        "/v1/live/approvals", json=_body(test_client, ids["real"]), headers=AGENT
    ).json()
    test_client.post("/v1/live/arm", json={"approval_id": approval["approval_id"]}, headers=AGENT)
    for path in (
        "/v1/brokers/kiwoom/sync",
        "/v1/brokers/kiwoom/sync-fees",
        "/v1/brokers/kiwoom/sync-domestic",
    ):
        response = test_client.post(path)
        assert (response.status_code, response.json()["error"]["code"]) == (
            409,
            "kiwoom_key_in_live_use",
        ), path
    disarmed = test_client.post("/v1/live/disarm", headers=RESEARCH)
    assert disarmed.json()["armed"] is False


def test_confirm_and_decline_reach_the_live_ledger(client: Any, tmp_path: Path) -> None:
    test_client, _ = client
    store = LiveOrderStore(tmp_path)
    assert store.open_confirmations() == []
    response = test_client.post(f"/v1/live/intents/{'b' * 64}/confirm", headers=AGENT)
    assert (response.status_code, response.json()["error"]["code"]) == (409, "cannot_decide")


def test_only_the_live_module_names_the_live_host() -> None:
    host = re.compile(r"(?<![A-Za-z0-9.-])api\.kiwoom\.com(?![A-Za-z0-9-])")
    hits = [
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "services").rglob("*.py")
        if "live_trading" in path.parts and host.search(path.read_text(encoding="utf-8"))
    ]
    assert hits == ["services/live_trading/client.py"]
