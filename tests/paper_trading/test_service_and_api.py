from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from packages.contracts.validator import validate_document
from services.api.main import create_app
from services.backtest.data import EngineBar
from services.backtest.jobs import BacktestRunner
from services.backtest.store import BacktestStore
from services.credentials.store import CredentialStore, MemorySecretBackend
from services.market_data.provider import BarsQuery, BarsResult
from services.paper_trading.brokers.kiwoom_paper import KiwoomPaperBroker
from services.paper_trading.feed import ServiceBarFeed
from services.paper_trading.paper_client import LiveDomainBlockedError
from services.paper_trading.runner import FeedSnapshot
from services.paper_trading.service import RunnerService, kiwoom_account_ref
from services.paper_trading.store import PaperOrderStore
from tests.paper_trading.fake_kiwoom import FakeKiwoomPaper

AAPL = "us:aapl:xnas:2026"
NOW = datetime(2026, 10, 23, 14, 2, 5, tzinfo=timezone.utc)
AGENT = {"origin": "tauri://localhost"}
RESEARCH = {"origin": "siw://app"}
LOCAL = "http://127.0.0.1"
RULES = {
    "type": "rules",
    "entry": {"op": "gt", "left": {"price": "close"}, "right": {"value": 100}},
    "exit": {"op": "lt", "left": {"price": "close"}, "right": {"value": 100}},
    "sizing": {"mode": "fixed_shares", "value": 3},
}


def _utc(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def minute_bars(query: BarsQuery, revision: str = "r1") -> BarsResult:
    start = datetime(2026, 10, 23, 14, 0, tzinfo=timezone.utc)
    bars = []
    for i, close in enumerate(("99", "101")):
        begin = start + timedelta(minutes=i)
        if not (query.start_utc <= begin < query.end_utc):
            continue
        bars.append(
            {
                "interval_start_utc": _utc(begin),
                "interval_end_utc": _utc(begin + timedelta(minutes=1)),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": "100",
                "is_final": True,
            }
        )
    coverage = {
        "missing_intervals": [],
        "adjustment": "raw",
        "source_id": "synthetic",
        "source_feed_scope": "synthetic-1m",
        "dataset_revision": revision,
        "expected_interval_count": len(bars),
    }
    return BarsResult(tuple(bars), coverage)


# -- bar feed --------------------------------------------------------------------------


def test_feed_pins_one_source_and_revision() -> None:
    snapshot = ServiceBarFeed(minute_bars).snapshot(AAPL, NOW)
    assert snapshot is not None
    assert [bar.close for bar in snapshot.bars] == [Decimal(99), Decimal(101)]
    assert (snapshot.source_id, snapshot.dataset_revision, snapshot.calendar_id) == (
        "synthetic",
        "r1",
        "XNAS",
    )
    assert snapshot.data_as_of_utc == NOW


def test_feed_refuses_mixed_revisions() -> None:
    calls = [0]

    def mixed(query: BarsQuery) -> BarsResult:
        calls[0] += 1
        return minute_bars(query, revision=f"r{calls[0]}")

    feed = ServiceBarFeed(mixed)
    assert feed.snapshot(AAPL, NOW) is None
    assert feed.last_error is not None and "more than one" in feed.last_error


# -- runner service --------------------------------------------------------------------


class StaticFeed:
    def snapshot(self, instrument_id: str, now: datetime) -> FeedSnapshot | None:
        start = int(datetime(2026, 10, 23, 14, 0, tzinfo=timezone.utc).timestamp())
        bars = [
            EngineBar(start + 60 * i, start + 60 * (i + 1), p, p, p, p, Decimal(1), True)
            for i, p in enumerate((Decimal(99), Decimal(101)))
        ]
        return FeedSnapshot(bars, now, "1m", "synthetic", "s", "r1", "raw", "regular", "XNYS", "v1")


def _approval(store: PaperOrderStore, backtests: BacktestStore, account_ref: str) -> str:
    strategy = backtests.save_strategy("above 100", RULES, None)
    content = {
        "strategy": {
            "strategy_id": strategy["strategy_id"],
            "strategy_version": strategy["version"],
            "definition_sha256": strategy["definition_sha256"],
        },
        "instruments": [{"instrument_id": AAPL, "symbol": "AAPL", "time_zone": "America/New_York"}],
        "limits": {
            "max_order_amount_krw": "1000000",
            "max_daily_orders": None,
            "max_daily_loss_rate": None,
            "max_position_amount_krw": None,
            "allowed_instrument_ids": [AAPL],
        },
        "broker_id": "kiwoom",
        "broker_account_ref": account_ref,
        "market_buffer_bps": "50",
        "fee_rate": "0",
        "fx": {"krw_per_usd": "1400", "source": "test", "as_of_utc": "2026-10-23T13:00:00Z"},
        "equity_basis": None,
        "max_signal_age_seconds": 300,
    }
    return store.record_approval(content)


@pytest.fixture
def service(
    tmp_path: Path,
) -> Iterator[tuple[RunnerService, PaperOrderStore, FakeKiwoomPaper, list[Any]]]:
    fake = FakeKiwoomPaper()
    store = PaperOrderStore(tmp_path)
    backtests = BacktestStore(tmp_path / "backtests.sqlite3", lambda: NOW)
    calls: list[Any] = []

    def broker_for(ref: str, markets: frozenset[str]) -> KiwoomPaperBroker:
        calls.append((ref, markets))
        return KiwoomPaperBroker(
            "app-key", "secret-key", "demo", markets=markets, transport=fake.transport()
        )

    def feed() -> StaticFeed:
        calls.append("feed")
        return StaticFeed()

    approval = _approval(store, backtests, kiwoom_account_ref("app-key"))
    runner_service = RunnerService(store, backtests.strategy, broker_for, feed, now=lambda: NOW)
    runner_service.approval = approval  # type: ignore[attr-defined]
    yield runner_service, store, fake, calls
    backtests.close()


def test_disarmed_runner_touches_nothing(service: Any) -> None:
    runner_service, store, fake, calls = service
    assert runner_service.tick() == "idle"
    assert calls == [] and fake.requests == []
    assert store.status()["last_heartbeat_utc"] is not None


def test_armed_runner_without_verified_market_blocks_orders(service: Any) -> None:
    runner_service, store, fake, calls = service
    store.arm(runner_service.approval, source="test")
    outcome = runner_service.tick()
    assert outcome.startswith("step intents=1 submitted=0 blocked=1")
    assert calls[0][1] == frozenset()
    assert fake.requests == []  # blocked before any request


def test_verified_market_lets_the_order_through(service: Any) -> None:
    runner_service, store, fake, calls = service
    store.verify_market("kiwoom", "us", "test fixture")
    store.arm(runner_service.approval, source="test")
    assert "submitted=1" in runner_service.tick()
    assert [o.stk_cd for o in fake.orders] == ["AAPL"]


def test_changed_strategy_definition_is_refused(service: Any, tmp_path: Path) -> None:
    runner_service, store, fake, _ = service
    approval = runner_service.approval
    content = store.approval(approval)
    content["strategy"]["definition_sha256"] = "0" * 64
    tampered = store.record_approval(content)
    store.arm(tampered, source="test")
    assert runner_service.tick() == "error"
    assert "SHA-256" in (runner_service.last_error or "")
    assert fake.requests == []


def test_blocked_request_turns_the_kill_switch_on(service: Any, monkeypatch: Any) -> None:
    runner_service, store, _, _ = service
    store.arm(runner_service.approval, source="test")

    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise LiveDomainBlockedError("left the paper domain")

    monkeypatch.setattr("services.paper_trading.runner.PaperRunner.step", boom)
    assert runner_service.tick() == "halted"
    assert store.status()["halted"] is True


# -- API -------------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, str]]:
    monkeypatch.setenv("SIW_DATA_DIR", str(tmp_path))
    keys = CredentialStore(tmp_path, MemorySecretBackend())
    demo = keys.add(
        "kiwoom", "paper", None, {"app_key": "app-key", "secret_key": "s"}, environment="demo"
    )
    keys.add("kiwoom", "live", None, {"app_key": "live-key", "secret_key": "s"}, environment="real")
    runner = BacktestRunner(
        BacktestStore(tmp_path / "backtests.sqlite3", lambda: NOW), lambda: minute_bars
    )
    with TestClient(
        create_app(credential_store=keys, backtest_runner=runner, clock=lambda: NOW),
        base_url=LOCAL,
    ) as test_client:
        strategy = test_client.post(
            "/v1/backtests/strategies", json={"name": "above 100", "definition": RULES}
        ).json()
        test_client.strategy = strategy  # type: ignore[attr-defined]
        yield test_client, demo.credential_id
    runner.stop()


def _approval_body(client: TestClient, credential_id: str, **changes: Any) -> dict[str, Any]:
    return {
        "strategy_id": client.strategy["strategy_id"],  # type: ignore[attr-defined]
        "strategy_version": client.strategy["version"],  # type: ignore[attr-defined]
        "instruments": [{"instrument_id": AAPL, "symbol": "aapl"}],
        "credential_id": credential_id,
        "krw_per_usd": "1400",
        **changes,
    }


def test_status_and_lists_validate(client: Any) -> None:
    test_client, _ = client
    for path in (
        "/v1/paper/status",
        "/v1/paper/approvals",
        "/v1/paper/intents",
        "/v1/paper/executions",
    ):
        response = test_client.get(path)
        assert response.status_code == 200
        validate_document(response.json())


def test_only_the_agent_app_can_approve_arm_and_release(client: Any) -> None:
    test_client, credential_id = client
    body = _approval_body(test_client, credential_id)
    for headers in ({}, RESEARCH):
        assert (
            test_client.post("/v1/paper/approvals", json=body, headers=headers).status_code == 403
        )
        assert (
            test_client.post(
                "/v1/paper/arm", json={"approval_id": "a" * 64}, headers=headers
            ).status_code
            == 403
        )
        assert test_client.post("/v1/paper/release", headers=headers).status_code == 403


def test_approve_then_arm_then_kill(client: Any) -> None:
    test_client, credential_id = client
    approval = test_client.post(
        "/v1/paper/approvals", json=_approval_body(test_client, credential_id), headers=AGENT
    )
    assert approval.status_code == 201
    document = approval.json()
    validate_document(document)
    assert document["broker_account_ref"] == kiwoom_account_ref("app-key")
    assert document["instruments"][0]["symbol"] == "AAPL"
    assert document["limits"]["max_order_amount_krw"] == "1000000"
    assert "app-key" not in approval.text

    armed = test_client.post(
        "/v1/paper/arm", json={"approval_id": document["approval_id"]}, headers=AGENT
    )
    assert armed.json()["armed"] is True
    killed = test_client.post("/v1/paper/kill", json={"reason": "stop"}, headers=RESEARCH)
    assert (killed.json()["halted"], killed.json()["armed"]) == (True, False)
    again = test_client.post(
        "/v1/paper/arm", json={"approval_id": document["approval_id"]}, headers=AGENT
    )
    assert again.status_code == 409


def test_live_keys_and_unsupported_listings_are_refused(client: Any) -> None:
    test_client, _ = client
    keys = test_client.get("/v1/credentials").json()
    live_id = next(k["credential_id"] for k in keys["credentials"] if k["label"] == "live")
    response = test_client.post(
        "/v1/paper/approvals", json=_approval_body(test_client, live_id), headers=AGENT
    )
    assert (response.status_code, response.json()["error"]["code"]) == (422, "not_a_demo_key")
    arca = _approval_body(
        test_client, live_id, instruments=[{"instrument_id": "us:spy:arcx:2026", "symbol": "SPY"}]
    )
    response = test_client.post("/v1/paper/approvals", json=arca, headers=AGENT)
    assert response.json()["error"]["code"] == "instrument_not_supported"


def test_env_file_fills_only_missing_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.paper_trading.service import load_env_file

    monkeypatch.setenv("SIW_KEEP", "mine")
    monkeypatch.delenv("SIW_NEW", raising=False)
    env = tmp_path / ".env"
    env.write_text(
        '# comment\nSIW_KEEP=theirs\nexport SIW_NEW="data"\nbad line\n', encoding="utf-8"
    )
    load_env_file(env)
    import os

    assert (os.environ["SIW_KEEP"], os.environ["SIW_NEW"]) == ("mine", "data")
