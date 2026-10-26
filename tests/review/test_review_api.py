"""Trade review API: trades from the journal, strategies, groups and notes (ADR 0042)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from services.api.main import create_app
from services.backtest.jobs import BacktestRunner
from services.backtest.store import BacktestStore
from services.journal.store import JournalStore
from services.review.store import ReviewStore
from tests.agents.test_review_agent import broker
from tests.backtest.test_backtest_api import RULES, synthetic_bars

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, JournalStore, str]]:
    monkeypatch.delenv("SIW_DATA_DIR", raising=False)
    journal = JournalStore(tmp_path / "journal")
    store = BacktestStore(tmp_path / "backtests.sqlite3", lambda: NOW)
    strategy_id = store.save_strategy("Cross", RULES)["strategy_id"]
    runner = BacktestRunner(store, lambda: synthetic_bars)
    app = create_app(
        journal_store=journal,
        backtest_runner=runner,
        review_store=ReviewStore(tmp_path / "review.sqlite3", lambda: NOW),
        clock=lambda: NOW,
    )
    for order_id, side, price, when in [
        ("1", "buy", "100", "2026-03-02T15:00:00Z"),
        ("2", "sell", "110", "2026-03-02T16:00:00Z"),
        ("3", "buy", "100", "2026-03-03T15:00:00Z"),
        ("4", "buy", "105", "2026-03-04T15:00:00Z"),
        ("5", "sell", "120", "2026-03-05T15:00:00Z"),
    ]:
        journal.record_broker_execution(broker(order_id, side, price, when, "kiwoom-a"))
    try:
        with TestClient(app) as client:
            yield client, journal, strategy_id
    finally:
        runner.stop()


def ref(order_id: str) -> dict[str, str]:
    return {"ledger": "broker", "execution_id": f"brk-{order_id}"}


def listed(client: TestClient, **params: Any) -> dict[str, Any]:
    response = client.get("/v1/review/trades", params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    assert body["kind"] == "review_trade_list"
    return body


def ids(trade: dict[str, Any]) -> list[str]:
    return [execution["execution_id"] for execution in trade["executions"]]


def test_lists_trades_newest_first_with_strategies(setup: Any) -> None:
    client, _journal, strategy_id = setup
    body = listed(client)
    assert body["total"] == 2
    assert [ids(t) for t in body["trades"]] == [["brk-3", "brk-4", "brk-5"], ["brk-1", "brk-2"]]
    assert body["trades"][1]["net_pnl"] == "99"  # 100 gross, two 0.50 fees
    assert body["strategies"] == [{"strategy_id": strategy_id, "name": "Cross", "rule": "flat"}]
    assert body["undated_execution_count"] == 0
    assert listed(client, ledger="manual")["total"] == 0


def test_assigning_a_strategy_regroups_by_its_rule(setup: Any) -> None:
    client, _journal, strategy_id = setup
    refs = [ref("3"), ref("4"), ref("5")]
    response = client.put(
        "/v1/review/assignments", json={"executions": refs, "strategy_id": strategy_id}
    )
    assert response.status_code == 200
    assert response.json()["updated"] == 3
    response = client.put(f"/v1/review/strategy-rules/{strategy_id}", json={"rule": "fifo"})
    assert response.json() == {
        "kind": "review_strategy_rule",
        "schema_version": "0.1",
        "strategy_id": strategy_id,
        "rule": "fifo",
    }
    mine = listed(client, strategy_id=strategy_id)
    # Each buy is a trade; the one sell closes the first buy (FIFO), the second stays open.
    assert [ids(t) for t in mine["trades"]] == [["brk-3", "brk-5"], ["brk-4"]]
    assert [t["status"] for t in mine["trades"]] == ["closed", "open"]
    assert {t["rule"] for t in mine["trades"]} == {"fifo"}
    assert [ids(t) for t in listed(client, strategy_id="none")["trades"]] == [["brk-1", "brk-2"]]
    # Taking the strategy off puts the fills back in the flat-rule stream.
    client.put("/v1/review/assignments", json={"executions": refs, "strategy_id": None})
    assert listed(client)["total"] == 2


def test_unknown_strategies_and_executions_are_refused(setup: Any) -> None:
    client, _journal, _strategy_id = setup
    response = client.put(
        "/v1/review/assignments", json={"executions": [ref("1")], "strategy_id": "strategy-nope"}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "strategy_not_found"
    response = client.put(
        "/v1/review/assignments", json={"executions": [ref("99")], "strategy_id": None}
    )
    assert response.json()["error"]["code"] == "execution_not_found"
    response = client.put("/v1/review/strategy-rules/strategy-nope", json={"rule": "day"})
    assert response.status_code == 404


def test_manual_groups(setup: Any) -> None:
    client, _journal, strategy_id = setup
    refs = [ref(n) for n in "12345"]
    client.put("/v1/review/assignments", json={"executions": refs, "strategy_id": strategy_id})
    client.put(f"/v1/review/strategy-rules/{strategy_id}", json={"rule": "manual"})
    assert listed(client)["total"] == 5  # no groups yet: one trade per fill
    response = client.post("/v1/review/groups", json={"executions": [ref("1"), ref("2")]})
    assert response.status_code == 201
    group_id = response.json()["group_id"]
    assert listed(client)["total"] == 4
    assert client.delete(f"/v1/review/groups/{group_id}").status_code == 204
    assert listed(client)["total"] == 5
    assert client.delete(f"/v1/review/groups/{group_id}").status_code == 404


def test_groups_never_mix_accounts(setup: Any) -> None:
    client, journal, _strategy_id = setup
    journal.record_broker_execution(broker("6", "buy", "100", "2026-03-06T15:00:00Z", "toss-a-1"))
    response = client.post("/v1/review/groups", json={"executions": [ref("5"), ref("6")]})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_review_group"


def test_notes_tags_and_reviewed_flag(setup: Any) -> None:
    client, _journal, _strategy_id = setup
    trade_id = listed(client)["trades"][1]["trade_id"]
    assert trade_id == "flat:broker:brk-1"
    response = client.put(
        f"/v1/review/trades/{trade_id}/annotation",
        json={"note": "급하게 따라 샀다", "tags": ["돌파", "충동", "돌파"], "reviewed": True},
    )
    assert response.status_code == 200
    assert response.json() == {
        "kind": "review_annotation",
        "schema_version": "0.1",
        "trade_id": trade_id,
        "note": "급하게 따라 샀다",
        "tags": ["돌파", "충동"],
        "reviewed": True,
        "recorded_at_utc": "2026-10-26T12:00:00Z",
        "revision": 1,
    }
    second = client.put(
        f"/v1/review/trades/{trade_id}/annotation", json={"note": "", "tags": ["돌파"]}
    ).json()
    assert second["revision"] == 2
    body = listed(client)
    assert body["tags"] == ["돌파"]
    assert body["trades"][1]["annotation"]["reviewed"] is False
    assert listed(client, tag="돌파")["total"] == 1
    assert listed(client, reviewed=False)["total"] == 2
    missing = client.put("/v1/review/trades/flat:broker:nope/annotation", json={"note": "x"})
    assert missing.status_code == 404


def test_new_journal_records_show_up(setup: Any) -> None:
    client, journal, _strategy_id = setup
    assert listed(client)["total"] == 2
    journal.record_broker_execution(broker("6", "buy", "100", "2026-03-06T15:00:00Z", "kiwoom-a"))
    # The second trade was still open (20 bought, 10 sold): the new buy joins it.
    body = listed(client, status="open")
    assert [ids(t) for t in body["trades"]] == [["brk-3", "brk-4", "brk-5", "brk-6"]]
    assert body["trades"][0]["open_quantity"] == "20"


def test_review_needs_a_data_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SIW_DATA_DIR", raising=False)
    with TestClient(create_app()) as client:
        response = client.get("/v1/review/trades")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "review_storage_unconfigured"
