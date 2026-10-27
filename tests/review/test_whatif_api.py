"""What-ifs, strategy suggestions and background principle checks (ADR 0042, step 3b)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from packages.contracts.validator import validate_document
from services.api.main import create_app
from services.backtest.jobs import BacktestRunner
from services.backtest.store import BacktestStore
from services.journal.store import JournalStore
from services.review.store import ReviewStore
from tests.agents.test_review_agent import broker
from tests.review.test_conversation_api import trade_documents
from tests.review.test_report_api import Asked, bars

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
TRADE = "flat:broker:brk-1"
# Always in the market: the entry rule holds on every bar, the exit rule never.
ALWAYS = {
    "type": "rules",
    "entry": {"op": "gt", "left": {"price": "close"}, "right": {"value": 0}},
    "exit": {"op": "lt", "left": {"price": "close"}, "right": {"value": 0}},
    "sizing": {"mode": "percent_equity", "value": 10},
}
NEVER = {
    "type": "rules",
    "entry": {"op": "lt", "left": {"price": "close"}, "right": {"value": 0}},
    "exit": None,
    "sizing": {"mode": "percent_equity", "value": 10},
}


@pytest.fixture
def setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, JournalStore, dict[str, str], Any]]:
    monkeypatch.delenv("SIW_DATA_DIR", raising=False)
    import services.api.main as main

    original_service = main.MarketBarsService

    class Synthetic:
        def __init__(self, _provider: object) -> None:
            pass

        def get_bars(self, query: Any) -> Any:
            return bars(query)

    main.MarketBarsService = Synthetic  # type: ignore[assignment,misc]
    journal = JournalStore(tmp_path / "journal")
    for document in trade_documents():
        journal.record_broker_execution(document)
    store = BacktestStore(tmp_path / "backtests.sqlite3", lambda: NOW)
    ids = {
        "always": store.save_strategy("Always", ALWAYS)["strategy_id"],
        "never": store.save_strategy("Never", NEVER)["strategy_id"],
    }
    runner = BacktestRunner(store, lambda: bars)
    review = ReviewStore(tmp_path / "review.sqlite3", lambda: NOW)
    app = create_app(
        journal_store=journal,
        backtest_runner=runner,
        review_store=review,
        review_ask=Asked(),
        clock=lambda: NOW,
    )
    try:
        with TestClient(app) as client:
            yield client, journal, ids, app
    finally:
        main.MarketBarsService = original_service  # type: ignore[misc]
        runner.stop()


def principles(client: TestClient, *items: tuple[str, str]) -> None:
    response = client.put(
        "/v1/review/principles",
        json={
            "items": [
                {"scope": "shared", "kind": kind, "value": value, "text": kind}
                for kind, value in items
            ]
        },
    )
    assert response.status_code == 201, response.text


def what_if(client: TestClient, **params: str) -> dict[str, Any]:
    response = client.get(f"/v1/review/trades/{TRADE}/what-if", params=params)
    assert response.status_code == 200, response.text
    document: dict[str, Any] = response.json()
    validate_document(document)
    return document


def test_principles_what_if_sells_at_the_first_touch(setup: Any) -> None:
    client, _journal, _ids, _app = setup
    assert what_if(client)["principles"] is None  # no principles yet
    principles(client, ("stop_loss_pct", "0.5"), ("take_profit_pct", "50"))
    result = what_if(client)["principles"]
    assert result["status"] == "computed"
    assert result["reason"] == "stop"
    # The average buy is 100.5; a 0.5% stop is 99.9975 (or the open, if it gapped below).
    assert Decimal(result["exit_price"]) <= Decimal("99.9975")
    assert result["exit_at_utc"] >= "2026-03-03T15:00:00Z"  # from the bar of the buy on
    assert result["quantity"] == "20"
    assert Decimal(result["difference"]) == Decimal(result["gross_pnl"]) - Decimal(
        result["actual_gross_pnl"]
    )
    assert result["hindsight"] is True


def crafted(minutes: list[tuple[str, str]]) -> Any:
    """5-minute bars that never move, except the 16:00 bar, which touches 99 and 102;
    its 1-minute bars are `minutes` (low, high) from 16:00."""
    from datetime import timedelta

    from services.market_data.provider import BarsResult
    from tests.review.test_report_api import TEMPLATE

    def bar(start: datetime, minutes_long: int, low: str, high: str) -> dict[str, Any]:
        end = start + timedelta(minutes=minutes_long)
        return {
            **TEMPLATE,
            "interval_start_utc": start.isoformat().replace("+00:00", "Z"),
            "interval_end_utc": end.isoformat().replace("+00:00", "Z"),
            "open": "100.5",
            "high": high,
            "low": low,
            "close": "100.5",
        }

    def get(query: Any) -> Any:
        spike = datetime(2026, 3, 3, 16, 0, tzinfo=timezone.utc)
        if query.timeframe == "1m":
            out = [
                bar(spike + timedelta(minutes=k), 1, low, high)
                for k, (low, high) in enumerate(minutes)
            ]
            return BarsResult(tuple(out), {})
        out = []
        moment = query.start_utc
        while moment < query.end_utc:
            touch = moment == spike
            out.append(bar(moment, 5, "99" if touch else "100.4", "102" if touch else "100.6"))
            moment += timedelta(minutes=5)
        return BarsResult(tuple(out), {})

    return get


def test_a_bar_touching_both_is_settled_on_one_minute_bars() -> None:
    from services.review.trades import build_trades, fills_of
    from services.review.whatif import principles_what_if

    fills, _ = fills_of("broker", trade_documents())
    (trade,) = build_trades(fills, {}, {}, {})
    items = [
        {"scope": "shared", "kind": "stop_loss_pct", "value": "1", "text": ""},
        {"scope": "shared", "kind": "take_profit_pct", "value": "1", "text": ""},
    ]
    # The target (101.505) is touched in the first minute, the stop (99.495) later.
    first = principles_what_if(crafted([("100.4", "101.6"), ("99.0", "100.6")]), trade, items, NOW)
    assert first["reason"] == "target"
    assert first["exit_at_utc"] == "2026-03-03T16:00:00Z"
    assert first["exit_price"] == "101.505"
    assert not any("assumed" in note for note in first["notes"])
    # One minute touching both: the stop is assumed first, and the result says so.
    both = principles_what_if(crafted([("99.0", "102")]), trade, items, NOW)
    assert both["reason"] == "stop"
    assert any("assumed first" in note for note in both["notes"])


def test_principles_without_a_stop_or_target_do_not_replay(setup: Any) -> None:
    client, _journal, _ids, _app = setup
    principles(client, ("max_trades_per_day", "3"))
    assert what_if(client)["principles"]["status"] == "not_applicable"


def test_strategy_what_if_trades_the_same_window(setup: Any) -> None:
    client, _journal, ids, _app = setup
    assert what_if(client)["strategy"] is None  # no strategy linked or chosen
    always = what_if(client, strategy_id=ids["always"])["strategy"]
    assert always["status"] == "computed"
    assert always["timeframe"] == "5m"
    # The first signal is the window's first bar; the buy is the next bar's open.
    assert always["entry_at_utc"] > always["window_start_utc"]
    assert any("no exit signal" in note for note in always["notes"])
    never = what_if(client, strategy_id=ids["never"])["strategy"]
    assert never["status"] == "no_signal"


def test_suggestions_list_strategies_whose_entry_held(setup: Any) -> None:
    client, _journal, ids, _app = setup
    response = client.get(f"/v1/review/trades/{TRADE}/strategy-suggestions")
    document = response.json()
    validate_document(document)
    assert document["suggestions"] == [
        {"strategy_id": ids["always"], "name": "Always", "timeframes": ["5m", "1d"]}
    ]


def test_checks_run_in_the_background_and_fill_the_list(setup: Any) -> None:
    client, journal, _ids, _app = setup
    # A recent trade (within 90 days of NOW) is checked automatically.
    journal.record_broker_execution(broker("7", "buy", "100", "2026-10-20T15:00:00Z", "kiwoom-b"))
    principles(client, ("max_trades_per_day", "5"), ("stop_loss_pct", "50"))
    listed = client.get("/v1/review/trades").json()
    assert {t["trade_id"]: t["principles_broken"] for t in listed["trades"]}[TRADE] is None
    _wait(client)
    recent = client.get("/v1/review/trades", params={"principles": "kept"}).json()
    assert [t["trade_id"] for t in recent["trades"]] == ["flat:broker:brk-7"]
    assert recent["trades"][0]["principles_broken"] == 0
    # Older trades wait for a run over their dates.
    status = client.post(
        "/v1/review/principle-checks/run",
        json={"from_date": "2026-03-01", "to_date": "2026-03-31"},
    )
    assert status.status_code == 202
    validate_document(status.json())
    _wait(client)
    unchecked = client.get("/v1/review/trades", params={"principles": "unchecked"}).json()
    assert unchecked["total"] == 0
    # A new principles version makes stored checks stale until they run again.
    principles(client, ("max_trades_per_day", "5"), ("stop_loss_pct", "40"))
    assert client.get("/v1/review/trades", params={"principles": "unchecked"}).json()["total"] in (
        1,
        2,
    )


def test_report_draft_carries_the_what_ifs(setup: Any) -> None:
    client, _journal, _ids, _app = setup
    principles(client, ("stop_loss_pct", "0.5"))
    draft = client.post(f"/v1/review/trades/{TRADE}/report-drafts", json={}).json()
    assert draft["what_if"]["principles"]["reason"] == "stop"
    assert draft["what_if"]["strategy"] is None


def _wait(client: TestClient) -> None:
    import time

    for _ in range(200):
        status = client.get("/v1/review/principle-checks/status").json()
        if status["queued"] == 0 and not status["running"]:
            assert status["failed"] == 0, status["last_error"]
            return
        time.sleep(0.05)
    raise AssertionError("background checks did not finish")
