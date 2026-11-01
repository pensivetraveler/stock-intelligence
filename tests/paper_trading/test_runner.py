from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from services.backtest.data import EngineBar
from services.backtest.strategy import StrategyError, fingerprint
from services.paper_trading.brokers.kiwoom_paper import KiwoomPaperBroker
from services.paper_trading.intent import FxRate, StrategyRef
from services.paper_trading.limits import PaperLimits
from services.paper_trading.runner import (
    FeedSnapshot,
    InstrumentPlan,
    PaperRunner,
    RunPlan,
    plan_from_approval,
)
from services.paper_trading.store import PaperOrderStore
from tests.paper_trading.fake_kiwoom import FakeKiwoomPaper

AAPL = "us:aapl:xnas:2026"
START = datetime(2026, 10, 23, 14, 0, tzinfo=timezone.utc)  # 23:00 Seoul
# Buy while the close is above 100, sell once it is below.
DEFINITION = {
    "type": "rules",
    "entry": {"op": "gt", "left": {"price": "close"}, "right": {"value": 100}},
    "exit": {"op": "lt", "left": {"price": "close"}, "right": {"value": 100}},
    "sizing": {"mode": "fixed_shares", "value": 3},
}


def _bars(*closes: float, final: bool = True) -> list[EngineBar]:
    bars = []
    for i, close in enumerate(closes):
        t = int((START + timedelta(minutes=i)).timestamp())
        price = Decimal(str(close))
        last = i == len(closes) - 1
        bars.append(
            EngineBar(t, t + 60, price, price, price, price, Decimal(100), final or not last)
        )
    return bars


class Feed:
    def __init__(self) -> None:
        self.bars: list[EngineBar] = []

    def snapshot(self, instrument_id: str, now: datetime) -> FeedSnapshot | None:
        if not self.bars:
            return None
        return FeedSnapshot(
            bars=self.bars,
            data_as_of_utc=max(now, datetime.fromtimestamp(self.bars[-1].end, timezone.utc)),
            timeframe="1m",
            source_id="alpaca",
            feed_scope="alpaca-sip",
            dataset_revision="alpaca-sip-r1",
            adjustment="raw",
            session="regular",
            calendar_id="XNYS",
            calendar_version="exchange-calendars-4.13.2",
        )


def _end(feed: Feed, seconds: int = 5) -> datetime:
    return datetime.fromtimestamp(feed.bars[-1].end, timezone.utc) + timedelta(seconds=seconds)


def _setup(
    tmp_path: Path,
    fake: FakeKiwoomPaper | None = None,
    markets: frozenset[str] = frozenset({"us"}),
    arm: bool = True,
    **plan_changes: Any,
) -> tuple[PaperRunner, FakeKiwoomPaper, Feed, PaperOrderStore]:
    fake = fake or FakeKiwoomPaper()
    broker = KiwoomPaperBroker(
        "app-key",
        "secret-key",
        "demo",
        markets=markets,
        transport=fake.transport(),
        sleep=lambda _seconds: None,
    )
    plan = RunPlan(
        strategy=StrategyRef("strat-above-100", 1, "c" * 64, "rules"),
        definition=DEFINITION,
        instruments=(InstrumentPlan(AAPL, "AAPL", "America/New_York"),),
        limits=PaperLimits((AAPL,), max_order_amount_krw=Decimal("1000000")),
        broker_id="kiwoom",
        broker_account_ref=broker.account_ref,
        fx=FxRate(Decimal("1400"), "fixed rate confirmed at approval", START),
    )
    plan = replace(plan, **plan_changes)
    store = PaperOrderStore(tmp_path)
    runner = PaperRunner(store, broker, Feed(), plan)
    if arm:
        store.arm(runner.approval_id, source="test")
    return runner, fake, runner.feed, store  # type: ignore[return-value]


def _api_ids(fake: FakeKiwoomPaper) -> list[str]:
    return [str(r.headers.get("api-id")) for r in fake.requests]


def test_signal_becomes_a_paper_order_then_a_fill_then_an_exit(tmp_path: Path) -> None:
    runner, fake, feed, store = _setup(tmp_path)
    feed.bars = _bars(99, 101)
    report = runner.step(_end(feed))
    assert (report.intents, report.submitted) == (1, 1)
    assert fake.orders[0].stk_cd == "AAPL" and fake.orders[0].trde_tp == "00"

    fake.fill("000000001", "101.05", at="23:01:30")
    report = runner.step(_end(feed, 30))
    assert report.fills == 1
    assert store.position_quantity(runner.plan.broker_account_ref, AAPL) == Decimal(3)

    feed.bars = _bars(99, 101, 98)
    report = runner.step(_end(feed))
    assert report.submitted == 1
    assert fake.orders[1].side == "1"  # sell
    assert fake.orders[1].qty == Decimal(3)


def test_execution_record_is_a_simulated_paper_fill(tmp_path: Path) -> None:
    runner, fake, feed, store = _setup(tmp_path)
    feed.bars = _bars(99, 101)
    runner.step(_end(feed))
    fake.fill("000000001", "101.05", at="23:01:30")
    runner.step(_end(feed, 30))
    with store._connect() as db:  # noqa: SLF001
        document = json.loads(
            db.execute("SELECT document_json FROM paper_executions").fetchone()[0]
        )
    assert document["fill_origin"] == "broker_demo_simulated"
    assert document["ledger"] == "broker_paper"
    assert document["executed_at_utc"] == "2026-10-23T14:01:30Z"  # 23:01:30 Seoul
    assert document["granularity"] == "order_aggregate"


def test_no_market_offered_blocks_every_order(tmp_path: Path) -> None:
    runner, fake, feed, store = _setup(tmp_path, markets=frozenset())
    feed.bars = _bars(99, 101)
    report = runner.step(_end(feed))
    assert (report.intents, report.blocked, report.submitted) == (1, 1, 0)
    assert "ust20000" not in _api_ids(fake)


def test_not_armed_and_halted_send_nothing(tmp_path: Path) -> None:
    runner, fake, feed, store = _setup(tmp_path, arm=False)
    feed.bars = _bars(99, 101)
    report = runner.step(_end(feed))
    assert report.blocked == 1
    intent_id = next(iter(store.submissions("accepted", "rejected", "unknown")), None)
    assert intent_id is None

    store.halt("kill switch", source="test")
    feed.bars = _bars(99, 101, 102)
    report = runner.step(_end(feed))
    assert report.halted and report.intents == 0
    assert not any(api in _api_ids(fake) for api in ("ust20000", "ust20001"))


def test_amount_over_the_limit_is_blocked_before_sending(tmp_path: Path) -> None:
    definition = {**DEFINITION, "sizing": {"mode": "fixed_shares", "value": 10}}
    runner, fake, feed, _ = _setup(tmp_path, definition=definition)
    feed.bars = _bars(99, 101)  # 10 × 101 × 1.005 × 1400 > 1,000,000 KRW
    report = runner.step(_end(feed))
    assert report.blocked == 1
    assert "ust20000" not in _api_ids(fake)


def test_old_signal_expires(tmp_path: Path) -> None:
    runner, fake, feed, store = _setup(tmp_path)
    feed.bars = _bars(99, 101)
    report = runner.step(_end(feed, 3600))
    assert report.expired == 1
    assert "ust20000" not in _api_ids(fake)


def test_same_bar_is_acted_on_once_even_after_a_restart(tmp_path: Path) -> None:
    runner, fake, feed, store = _setup(tmp_path)
    feed.bars = _bars(99, 101)
    runner.step(_end(feed))
    runner.step(_end(feed, 10))
    again = PaperRunner(store, runner.broker, feed, runner.plan)  # a restarted runner
    report = again.step(_end(feed, 20))
    assert report.intents == 0
    assert len(fake.orders) == 1


def test_non_final_bar_gives_no_signal(tmp_path: Path) -> None:
    runner, fake, feed, _ = _setup(tmp_path)
    feed.bars = _bars(99, 101, final=False)
    assert runner.step(_end(feed)).intents == 0


def test_lost_reply_blocks_until_reconciled(tmp_path: Path) -> None:
    fake = FakeKiwoomPaper(drop_next_order=True)
    runner, fake, feed, store = _setup(tmp_path, fake=fake)
    feed.bars = _bars(99, 101)
    runner.step(_end(feed))
    assert store.unknown_submission_count() == 1
    assert store.may_submit(runner.approval_id) == (False, "unknown_submission_pending")

    report = runner.step(_end(feed, 10))
    assert report.reconciled == 1
    accepted = store.submissions("accepted")
    assert [s["broker_order_ref"] for s in accepted] == ["000000001"]
    assert store.unknown_submission_count() == 0


def test_lost_order_that_never_arrived_is_marked_rejected(tmp_path: Path) -> None:
    fake = FakeKiwoomPaper(drop_next_order=True)
    runner, fake, feed, store = _setup(tmp_path, fake=fake)
    feed.bars = _bars(99, 101)
    runner.step(_end(feed))
    fake.orders.clear()  # the server never kept it
    runner.step(_end(feed, 10))
    assert [s["response_code"] for s in store.submissions("rejected")] == ["not_found_on_reconcile"]


def test_ambiguous_unknown_order_turns_the_kill_switch_on(tmp_path: Path) -> None:
    fake = FakeKiwoomPaper(drop_next_order=True)
    runner, fake, feed, store = _setup(tmp_path, fake=fake)
    feed.bars = _bars(99, 101)
    runner.step(_end(feed))
    fake.orders.append(replace(fake.orders[0], ord_no="000000077"))  # a look-alike order
    runner.step(_end(feed, 10))
    status = store.status()
    assert status["halted"] and status["halted_reason"] == "ambiguous unknown paper order"


def test_percent_of_equity_needs_an_approved_equity(tmp_path: Path) -> None:
    definition = {**DEFINITION, "sizing": {"mode": "percent_equity", "value": 10}}
    runner, fake, feed, _ = _setup(tmp_path, definition=definition)
    feed.bars = _bars(99, 101)
    report = runner.step(_end(feed))
    assert report.skipped == [f"{AAPL}:equity_unknown"]

    runner2, fake2, feed2, _ = _setup(
        tmp_path / "b", definition=definition, equity_basis=Decimal("5000")
    )
    feed2.bars = _bars(99, 101)
    runner2.step(_end(feed2))
    assert fake2.orders[0].qty == Decimal(4)  # 500 / (101) → 4 whole shares


def test_limit_entry_is_priced_from_the_close(tmp_path: Path) -> None:
    definition = {
        **DEFINITION,
        "entry_order": {"type": "limit", "offset_pct": -1, "expire_bars": 1},
    }
    runner, fake, feed, store = _setup(tmp_path, definition=definition)
    feed.bars = _bars(99, 101)
    runner.step(_end(feed))
    assert (fake.orders[0].trde_tp, fake.orders[0].price) == ("00", "99.99")


def test_stop_loss_strategies_are_refused(tmp_path: Path) -> None:
    runner, _, feed, _ = _setup(tmp_path, definition={**DEFINITION, "stop_loss_pct": 5})
    feed.bars = _bars(99, 101)
    with pytest.raises(StrategyError):
        runner.step(_end(feed))


def test_plan_for_another_account_is_refused(tmp_path: Path) -> None:
    runner, _, _, store = _setup(tmp_path)
    with pytest.raises(ValueError):
        PaperRunner(store, runner.broker, runner.feed, replace(runner.plan, broker_account_ref="x"))


def test_ecos_rate_is_used_and_missing_rate_blocks(tmp_path: Path) -> None:
    runner, fake, feed, store = _setup(tmp_path, fx=None, fx_source="bok_ecos")
    feed.bars = _bars(99, 101)
    report = runner.step(_end(feed))  # no ECOS reader: the amount limit cannot be checked
    assert report.blocked == 1 and fake.orders == []

    ecos = FxRate(Decimal("1385.2"), "BOK ECOS 731Y001/0000001 for 2026-10-22", START)
    runner2, fake2, feed2, store2 = _setup(tmp_path / "b", fx=None, fx_source="bok_ecos")
    runner2._fx_rate = lambda: ecos  # noqa: SLF001
    feed2.bars = _bars(99, 101)
    assert runner2.step(_end(feed2)).submitted == 1
    intent = store2.intents(1)[0]["intent"]
    assert intent["fx"]["krw_per_usd"] == "1385.2"
    assert intent["fx"]["source"].startswith("BOK ECOS")


def test_cancel_rows_are_never_matched_to_an_unknown_submission(tmp_path: Path) -> None:
    fake = FakeKiwoomPaper(drop_next_order=True)
    runner, fake, feed, store = _setup(tmp_path, fake=fake)
    feed.bars = _bars(99, 101)
    runner.step(_end(feed))
    lost = fake.orders[0]
    fake.orders.clear()  # the order never stayed; only a cancel row of an older order is listed
    fake.orders.append(replace(lost, ord_no="000000448", original="000000436"))
    runner.step(_end(feed, 10))
    assert [s["response_code"] for s in store.submissions("rejected")] == ["not_found_on_reconcile"]


def test_one_order_list_read_per_step(tmp_path: Path) -> None:
    fake = FakeKiwoomPaper(drop_next_order=True)
    runner, fake, feed, store = _setup(tmp_path, fake=fake)
    feed.bars = _bars(99, 101)
    runner.step(_end(feed))  # the reply is lost: one unknown submission
    fake.fill("000000001", "101.05", at="23:01:30")
    before = sum(1 for r in fake.requests if r.headers.get("api-id") == "ust21510")
    report = runner.step(_end(feed, 30))  # reconcile and fill sync in one step
    after = sum(1 for r in fake.requests if r.headers.get("api-id") == "ust21510")
    assert (report.reconciled, report.fills, after - before) == (1, 1, 1)


def test_settle_time_lets_a_closed_bar_signal_before_the_source_finalizes_it(
    tmp_path: Path,
) -> None:
    runner, fake, feed, store = _setup(tmp_path, bar_settle_seconds=60)
    feed.bars = _bars(99, 101, final=False)  # the 101 bar is not final at the source yet
    assert runner.step(_end(feed, 5)).intents == 0  # 5 s after its end: not settled
    report = runner.step(_end(feed, 61))
    assert (report.intents, report.submitted) == (1, 1)
    data = store.intents(1)[0]["intent"]["data"]
    assert (data["final_by"], data["settle_seconds"]) == ("settle", 60)


def test_source_final_bars_are_recorded_as_such(tmp_path: Path) -> None:
    runner, fake, feed, store = _setup(tmp_path, bar_settle_seconds=60)
    feed.bars = _bars(99, 101)
    runner.step(_end(feed))
    data = store.intents(1)[0]["intent"]["data"]
    assert (data["final_by"], data["settle_seconds"]) == ("source", None)


def test_without_settle_time_the_latest_source_final_bar_is_used(tmp_path: Path) -> None:
    runner, fake, feed, store = _setup(tmp_path)
    feed.bars = _bars(101, 99, final=False)  # only the 101 bar is final at the source
    report = runner.step(_end(feed, 5))
    assert report.intents == 1  # decided on the 101 bar; the unfinished 99 bar is left out
    assert store.intents(1)[0]["intent"]["data"]["input_bar_count"] == 1


# --- orders left unfilled (ADR 0044, G1) ------------------------------------------------


def _open_order(tmp_path: Path, **changes: Any) -> tuple[PaperRunner, FakeKiwoomPaper, Feed, Any]:
    runner, fake, feed, store = _setup(tmp_path, unfilled_cancel_seconds=120, **changes)
    feed.bars = _bars(99, 101)
    assert runner.step(_end(feed)).submitted == 1
    return runner, fake, feed, store


def test_an_order_left_unfilled_is_cancelled_and_a_later_bar_decides_again(tmp_path: Path) -> None:
    runner, fake, feed, store = _open_order(tmp_path)
    first = _end(feed)
    assert runner.step(first + timedelta(seconds=119)).cancel_requests == 0
    report = runner.step(first + timedelta(seconds=120))
    assert report.cancel_requests == 1
    assert fake.orders[0].cancelled
    assert runner.step(first + timedelta(seconds=130)).cancelled == 1
    (submission,) = store.submissions("cancelled")
    assert submission["broker_order_ref"] == fake.orders[0].ord_no
    assert not store.accepted_without_execution()
    assert _api_ids(fake).count("ust20003") == 1
    # Nothing was filled, and the next bar's signal makes a new order.
    assert store.position_quantity(runner.plan.broker_account_ref, AAPL) == 0
    feed.bars = _bars(99, 101, 102)
    assert runner.step(_end(feed)).submitted == 1
    assert len(fake.orders) == 2


def test_a_partly_filled_order_keeps_its_fill_when_the_rest_is_cancelled(tmp_path: Path) -> None:
    runner, fake, feed, store = _open_order(tmp_path)
    order = fake.orders[0]
    order.filled, order.fill_price, order.cntr_time = Decimal(1), Decimal("101"), "23:31:30"
    later = _end(feed) + timedelta(seconds=120)
    assert runner.step(later).cancel_requests == 1
    assert runner.step(later + timedelta(seconds=5)).fills == 1
    assert store.position_quantity(runner.plan.broker_account_ref, AAPL) == 1
    assert not store.submissions("cancelled")


def test_the_kill_switch_leaves_unfilled_orders_alone(tmp_path: Path) -> None:
    runner, fake, feed, store = _open_order(tmp_path)
    store.halt("test", source="test")
    report = runner.step(_end(feed) + timedelta(minutes=10))
    assert report.halted
    assert report.cancel_requests == 0
    assert "ust20003" not in _api_ids(fake)


def test_without_the_setting_nothing_is_cancelled(tmp_path: Path) -> None:
    runner, fake, feed, _store = _setup(tmp_path)
    feed.bars = _bars(99, 101)
    runner.step(_end(feed))
    runner.step(_end(feed) + timedelta(hours=1))
    assert "ust20003" not in _api_ids(fake)


def test_only_this_plans_orders_are_cancelled(tmp_path: Path) -> None:
    runner, fake, feed, store = _open_order(tmp_path)
    other = PaperRunner(
        store,
        runner.broker,
        feed,
        replace(runner.plan, market_buffer_bps=Decimal("60")),
    )
    assert other.approval_id != runner.approval_id
    assert other.step(_end(feed) + timedelta(minutes=10)).cancel_requests == 0
    assert "ust20003" not in _api_ids(fake)


def test_a_second_order_waits_while_one_is_open(tmp_path: Path) -> None:
    runner, fake, feed, _store = _setup(tmp_path)
    feed.bars = _bars(99, 101)
    runner.step(_end(feed))
    feed.bars = _bars(99, 101, 102)
    report = runner.step(_end(feed))
    assert report.submitted == 0
    assert report.skipped == [f"{AAPL}:open_order"]
    assert len(fake.orders) == 1


def test_a_cancel_that_fails_stops_after_three_tries(tmp_path: Path) -> None:
    runner, fake, feed, store = _open_order(tmp_path)
    fake.reject_cancels = True
    moment = _end(feed)
    requests = 0
    for _ in range(4):
        moment += timedelta(seconds=120)
        requests += runner.step(moment).cancel_requests
    assert requests == 3
    assert _api_ids(fake).count("ust20003") == 3
    assert store.status()["halted"]
    assert not fake.orders[0].cancelled


def test_an_unknown_cancel_is_settled_from_the_order_list(tmp_path: Path) -> None:
    runner, fake, feed, store = _open_order(tmp_path)
    fake.drop_next_cancel = True
    later = _end(feed) + timedelta(seconds=120)
    assert runner.step(later).cancel_requests == 1
    assert fake.orders[0].cancelled
    assert runner.step(later + timedelta(seconds=5)).cancelled == 1
    assert store.submissions("cancelled")


def test_the_setting_is_part_of_the_approval_only_when_used(tmp_path: Path) -> None:
    runner, *_ = _setup(tmp_path)
    assert "unfilled_cancel_seconds" not in runner.plan.approval_content()
    used = replace(runner.plan, unfilled_cancel_seconds=120)
    content = used.approval_content()
    assert content["unfilled_cancel_seconds"] == 120
    content["strategy"]["definition_sha256"] = fingerprint(used.definition)
    assert plan_from_approval(content, used.definition).unfilled_cancel_seconds == 120
