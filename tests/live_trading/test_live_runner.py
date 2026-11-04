"""Live orders wait for the user's confirmation (ADR 0047). Only a fake Kiwoom server is used."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from services.live_trading.client import LiveOnlyClient, NotLiveCredentialError
from services.live_trading.kiwoom_live import KiwoomLiveBroker
from services.live_trading.runner import SEND_GRACE_SECONDS, LiveRunner
from services.live_trading.store import LiveOrderStore
from services.paper_trading.brokers.kiwoom_paper import ALLOWED, KiwoomPaperBroker
from services.paper_trading.intent import StrategyRef
from services.paper_trading.limits import PaperLimits
from services.paper_trading.paper_client import (
    LiveDomainBlockedError,
    NotPaperCredentialError,
    PaperOperationBlockedError,
)
from services.paper_trading.runner import InstrumentPlan, RunPlan
from services.paper_trading.store import PaperOrderStore, PaperStoreError
from tests.paper_trading.fake_kiwoom import FakeKiwoomPaper
from tests.paper_trading.test_runner import AAPL, DEFINITION, START, Feed, _bars, _end

ONE_SHARE = {**DEFINITION, "sizing": {"mode": "fixed_shares", "value": 1}}


def _setup(
    tmp_path: Path, arm: bool = True, **plan_changes: Any
) -> tuple[LiveRunner, FakeKiwoomPaper, Feed, LiveOrderStore]:
    fake = FakeKiwoomPaper()
    broker = KiwoomLiveBroker(
        "live-app-key",
        "secret-key",
        "real",
        markets=frozenset({"us"}),
        transport=fake.transport(),
        sleep=lambda _seconds: None,
    )
    plan = RunPlan(
        strategy=StrategyRef("strat-above-100", 1, "c" * 64, "rules"),
        definition=ONE_SHARE,
        instruments=(InstrumentPlan(AAPL, "AAPL", "America/New_York"),),
        limits=PaperLimits((AAPL,), max_daily_orders=2, max_order_amount_usd=Decimal("150")),
        broker_id="kiwoom",
        broker_account_ref=broker.account_ref,
        confirm_seconds=60,
    )
    plan = replace(plan, **plan_changes)
    # The store's clock follows the last step, so the user answers "just after" it.
    clock = [START]
    store = LiveOrderStore(tmp_path, now=lambda: clock[0])
    runner = LiveRunner(store, broker, Feed(), plan)
    step = runner.step

    def stepped(now: datetime) -> Any:
        clock[0] = now
        return step(now)

    runner.step = stepped  # type: ignore[method-assign]
    if arm:
        store.arm(runner.approval_id, source="test")
    return runner, fake, runner.feed, store  # type: ignore[return-value]


def _orders_sent(fake: FakeKiwoomPaper) -> list[httpx.Request]:
    return [r for r in fake.requests if r.headers.get("api-id") in ("ust20000", "ust20001")]


def _waiting(store: LiveOrderStore) -> dict[str, Any]:
    [item] = store.open_confirmations()
    return item


def test_an_order_waits_until_the_user_confirms_it(tmp_path: Path) -> None:
    runner, fake, feed, store = _setup(tmp_path)
    feed.bars = _bars(99, 101)
    now = _end(feed)
    report = runner.step(now)
    assert (report.intents, report.awaiting, report.submitted) == (1, 1, 0)
    assert _orders_sent(fake) == []
    item = _waiting(store)
    assert item["intent"]["environment"] == "real"
    assert item["expires_at_utc"] == (now + timedelta(seconds=60)).isoformat().replace(
        "+00:00", "Z"
    )

    assert runner.step(now + timedelta(seconds=5)).submitted == 0  # still waiting
    store.decide_confirmation(item["intent"]["intent_id"], "confirmed", source="test")
    report = runner.step(now + timedelta(seconds=10))
    assert report.submitted == 1
    [sent] = _orders_sent(fake)
    assert sent.url.host == "api.kiwoom.com"
    assert json.loads(sent.content)["ord_qty"] == "1"
    assert store.outcome(item["intent"]["intent_id"]) == ("submitted", None)
    assert store.open_confirmations() == []


def test_live_fills_go_to_the_live_ledger(tmp_path: Path) -> None:
    runner, fake, feed, store = _setup(tmp_path)
    feed.bars = _bars(99, 101)
    now = _end(feed)
    runner.step(now)
    store.decide_confirmation(_waiting(store)["intent"]["intent_id"], "confirmed", "test")
    runner.step(now + timedelta(seconds=5))
    fake.fill("000000001", "101.05", at="23:01:30")
    assert runner.step(now + timedelta(seconds=30)).fills == 1
    [execution] = store.executions()
    assert (execution["environment"], execution["ledger"], execution["fill_origin"]) == (
        "real",
        "broker_live_orders",
        "broker_live",
    )
    assert (tmp_path / "live_orders.sqlite3").is_file()
    assert not (tmp_path / "paper_orders.sqlite3").exists()


def test_an_order_not_confirmed_in_time_expires_and_a_late_answer_is_refused(
    tmp_path: Path,
) -> None:
    runner, fake, feed, store = _setup(tmp_path)
    feed.bars = _bars(99, 101)
    now = _end(feed)
    runner.step(now)
    identifier = _waiting(store)["intent"]["intent_id"]
    store._now = lambda: now + timedelta(seconds=61)  # type: ignore[method-assign]  # noqa: SLF001
    with pytest.raises(PaperStoreError, match="passed"):
        store.decide_confirmation(identifier, "confirmed", "test")
    report = runner.step(now + timedelta(seconds=61))
    assert report.expired == 1
    assert store.outcome(identifier) == ("expired", "not_confirmed")
    assert _orders_sent(fake) == []


def test_a_declined_order_is_closed_and_never_sent(tmp_path: Path) -> None:
    runner, fake, feed, store = _setup(tmp_path)
    feed.bars = _bars(99, 101)
    now = _end(feed)
    runner.step(now)
    identifier = _waiting(store)["intent"]["intent_id"]
    store.decide_confirmation(identifier, "declined", "test")
    runner.step(now + timedelta(seconds=5))
    assert store.outcome(identifier) == ("blocked", "declined_by_user")
    assert _orders_sent(fake) == []
    with pytest.raises(PaperStoreError, match="already decided"):
        store.decide_confirmation(identifier, "confirmed", "test")


def test_no_new_signal_while_one_waits(tmp_path: Path) -> None:
    runner, fake, feed, store = _setup(tmp_path, confirm_seconds=120)
    feed.bars = _bars(99, 101)
    now = _end(feed)
    runner.step(now)
    feed.bars = _bars(99, 101, 102)
    report = runner.step(_end(feed))
    assert report.intents == 0
    assert len(store.open_confirmations()) == 1


def test_the_kill_switch_stops_a_confirmed_entry(tmp_path: Path) -> None:
    runner, fake, feed, store = _setup(tmp_path)
    feed.bars = _bars(99, 101)
    now = _end(feed)
    runner.step(now)
    identifier = _waiting(store)["intent"]["intent_id"]
    store.decide_confirmation(identifier, "confirmed", "test")
    store.halt("pressed", source="test")
    runner.step(now + timedelta(seconds=5))
    assert store.outcome(identifier) == ("blocked", "halted")
    assert _orders_sent(fake) == []


def test_a_confirmation_too_old_to_send_expires(tmp_path: Path) -> None:
    runner, fake, feed, store = _setup(tmp_path)
    feed.bars = _bars(99, 101)
    now = _end(feed)
    runner.step(now)
    identifier = _waiting(store)["intent"]["intent_id"]
    store.decide_confirmation(identifier, "confirmed", "test")
    runner.step(now + timedelta(seconds=60 + SEND_GRACE_SECONDS + 1))
    assert store.outcome(identifier) == ("expired", "confirmed_but_not_sent_in_time")
    assert _orders_sent(fake) == []


def test_the_dollar_cap_blocks_an_order_before_it_waits(tmp_path: Path) -> None:
    runner, fake, feed, store = _setup(
        tmp_path, limits=PaperLimits((AAPL,), max_daily_orders=2, max_order_amount_usd=Decimal(100))
    )
    feed.bars = _bars(99, 101)  # 1 share at about 101.51 with the 50 bps buffer
    report = runner.step(_end(feed))
    assert (report.blocked, report.awaiting) == (1, 0)
    assert store.open_confirmations() == []
    [row] = store.intents()
    assert row["intent"]["limit_check"]["reasons"] == ["order_amount_exceeded"]
    assert row["intent"]["limit_check"]["limits"]["max_order_amount_usd"] == "100"


def test_the_daily_count_blocks_a_third_order(tmp_path: Path) -> None:
    runner, fake, feed, store = _setup(tmp_path)

    def confirm_and_fill(closes: tuple[float, ...], ord_no: str) -> datetime:
        feed.bars = _bars(*closes)
        now = _end(feed)
        runner.step(now)
        store.decide_confirmation(_waiting(store)["intent"]["intent_id"], "confirmed", "test")
        runner.step(now + timedelta(seconds=5))
        fake.fill(ord_no, str(closes[-1]))
        runner.step(now + timedelta(seconds=20))
        return now

    confirm_and_fill((99, 101), "000000001")  # buy
    confirm_and_fill((99, 101, 98), "000000002")  # sell
    feed.bars = _bars(99, 101, 98, 103)
    report = runner.step(_end(feed))
    assert (report.blocked, report.awaiting) == (1, 0)
    assert store.intents()[0]["outcome"] == {
        "status": "blocked",
        "reason": "daily_order_count_exceeded",
    }


def test_a_protective_exit_also_waits_for_the_user(tmp_path: Path) -> None:
    stops = {**ONE_SHARE, "exit": None, "stop_loss_pct": 1}
    runner, fake, feed, store = _setup(tmp_path, definition=stops, unfilled_cancel_seconds=60)
    feed.bars = _bars(99, 101)
    now = _end(feed)
    runner.step(now)
    store.decide_confirmation(_waiting(store)["intent"]["intent_id"], "confirmed", "test")
    runner.step(now + timedelta(seconds=5))
    fake.fill("000000001", "101")
    runner.step(now + timedelta(seconds=20))
    feed.bars = _bars(99, 101, 101, 95)
    report = runner.step(_end(feed))
    assert report.awaiting == 1
    item = _waiting(store)
    assert item["intent"]["side"] == "sell"
    assert item["intent"]["signal"]["summary"].startswith("protective exit:")
    assert len(_orders_sent(fake)) == 1  # only the entry
    store.decide_confirmation(item["intent"]["intent_id"], "confirmed", "test")
    assert runner.step(_end(feed, 10)).submitted == 1
    assert len(_orders_sent(fake)) == 2


def test_live_client_reaches_only_the_live_domain() -> None:
    sent: list[httpx.Request] = []
    client = LiveOnlyClient(
        ALLOWED, transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200))
    )
    with pytest.raises(LiveDomainBlockedError):
        client.request(
            "POST", "https://mockapi.kiwoom.com/api/us/ordr", headers={"api-id": "ust20000"}
        )
    with pytest.raises(PaperOperationBlockedError):
        client.request("POST", "/api/us/acnt", headers={"api-id": "ust21070"})
    assert sent == []
    client.request("POST", "/api/us/ordr", headers={"api-id": "ust20000"})
    assert sent[0].url.host == "api.kiwoom.com"


def test_keys_cannot_cross_between_paper_and_live() -> None:
    with pytest.raises(NotLiveCredentialError):
        KiwoomLiveBroker("k", "s", "demo")
    with pytest.raises(NotPaperCredentialError):
        KiwoomPaperBroker("k", "s", "real")
    assert KiwoomLiveBroker("k", "s", "real").account_ref.endswith("-real")


def test_ledgers_refuse_documents_of_the_other_environment(tmp_path: Path) -> None:
    runner, fake, feed, live = _setup(tmp_path / "live")
    feed.bars = _bars(99, 101)
    runner.step(_end(feed))
    real_intent = _waiting(live)["intent"]
    paper = PaperOrderStore(tmp_path / "paper")
    from services.paper_trading.limits import LimitResult

    with pytest.raises(PaperStoreError, match="does not belong"):
        paper.record_intent(real_intent, LimitResult(True, (), None))
    demo_intent = {
        **real_intent,
        "environment": "demo",
        "broker_account_ref": real_intent["broker_account_ref"].replace("-real", "-demo"),
    }
    with pytest.raises(PaperStoreError, match="does not belong"):
        live.record_intent(demo_intent, LimitResult(True, (), None))


def test_a_live_plan_needs_a_confirmation_window(tmp_path: Path) -> None:
    runner, fake, feed, store = _setup(tmp_path, confirm_seconds=None)
    feed.bars = _bars(99, 101)
    with pytest.raises(ValueError, match="confirm_seconds"):
        runner.step(_end(feed))
    assert _orders_sent(fake) == []
