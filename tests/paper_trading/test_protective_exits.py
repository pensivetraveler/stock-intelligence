"""The runner keeps a position's protection and sells what reaches a level (ADR 0044 G3)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest

from packages.contracts.validator import validate_document
from services.backtest.data import EngineBar
from services.backtest.store import BacktestStore
from services.paper_trading.brokers.base import PaperBroker
from services.paper_trading.brokers.kiwoom_paper import KiwoomPaperBroker
from services.paper_trading.limits import LimitResult
from services.paper_trading.paper_client import LiveDomainBlockedError
from services.paper_trading.runner import PaperRunner, plan_from_approval
from services.paper_trading.service import RunnerService
from services.paper_trading.store import PaperOrderStore
from tests.paper_trading.fake_kiwoom import FakeKiwoomPaper
from tests.paper_trading.test_runner import AAPL, DEFINITION, START, Feed, _setup

MSFT = "us:msft:xnas:2026"
STOP = {**DEFINITION, "stop_loss_pct": "5"}


def _bar(
    minute: int, close: float, low: float | None = None, high: float | None = None
) -> EngineBar:
    at = int((START + timedelta(minutes=minute)).timestamp())
    price = Decimal(str(close))
    return EngineBar(
        at,
        at + 60,
        price,
        price if high is None else Decimal(str(high)),
        price if low is None else Decimal(str(low)),
        price,
        Decimal(100),
        True,
    )


def _after(feed: Feed, seconds: int = 5) -> datetime:
    return datetime.fromtimestamp(feed.bars[-1].end, timezone.utc) + timedelta(seconds=seconds)


def _held(tmp_path: Path, definition: dict[str, Any] = STOP, **changes: Any) -> Any:
    """A runner holding 3 AAPL bought at 101: the fill is 30 s into the bar after the signal."""
    runner, fake, feed, store = _setup(
        tmp_path, definition=definition, unfilled_cancel_seconds=60, **changes
    )
    feed.bars = [_bar(0, 99), _bar(1, 101)]
    assert runner.step(_after(feed)).submitted == 1
    fake.fill("000000001", "101", at="23:02:30")
    assert runner.step(_after(feed, 40)).fills == 1
    return runner, fake, feed, store


def _state(runner: PaperRunner, store: PaperOrderStore) -> Any:
    return store.active_protection(runner.plan.broker_account_ref, AAPL)


def _sell_intents(store: PaperOrderStore) -> list[dict[str, Any]]:
    return [row for row in store.intents() if row["intent"]["side"] == "sell"]


def test_a_stop_sells_the_position_and_the_fill_closes_its_protection(tmp_path: Path) -> None:
    runner, fake, feed, store = _held(tmp_path)
    state = _state(runner, store)
    assert state.entry_price == Decimal(101) and state.levels.stop == Decimal("95.95")

    feed.bars = [*feed.bars, _bar(2, 102, low=95)]
    assert runner.step(_after(feed)).submitted == 1
    assert (fake.orders[1].side, fake.orders[1].qty) == ("1", Decimal(3))
    # Priced from the bar's close with the plan's buffer: 102 × (1 - 0.005).
    assert fake.orders[1].price == "101.49"
    row = _sell_intents(store)[0]
    assert row["intent"]["signal"]["summary"] == "protective exit: stop_loss"
    assert row["intent"]["signal"]["operands"][0]["value"] == "95.95"
    assert row["intent"]["data"]["signal_bar_start_utc"] == "2026-10-23T14:02:00Z"

    fake.fill("000000002", "101.5", at="23:03:30")
    runner.step(_after(feed, 40))
    assert store.position_quantity(runner.plan.broker_account_ref, AAPL) == 0
    assert _state(runner, store) is None


def test_take_profit_and_trailing_stop_give_their_reasons(tmp_path: Path) -> None:
    definition = {**DEFINITION, "take_profit_pct": "2"}
    runner, fake, feed, store = _held(tmp_path / "target", definition)
    feed.bars = [*feed.bars, _bar(2, 102, high=104)]
    runner.step(_after(feed))
    assert _sell_intents(store)[0]["intent"]["signal"]["summary"].endswith("take_profit")

    trailing = {**DEFINITION, "trailing_stop": {"mode": "pct", "value": "1"}}
    runner, fake, feed, store = _held(tmp_path / "trail", trailing)
    # The fill's own bar moves nothing; the next one raises the stop for the one after it.
    feed.bars = [*feed.bars, _bar(2, 102), _bar(3, 110, low=109.5), _bar(4, 110, low=108.5)]
    runner.step(_after(feed))
    assert _sell_intents(store)[0]["intent"]["signal"]["summary"].endswith("trailing_stop")


def test_nothing_is_judged_before_a_fill_arrives_in_a_bar_not_yet_known(tmp_path: Path) -> None:
    fake = FakeKiwoomPaper()
    runner, fake, feed, store = _setup(tmp_path, fake, definition=STOP, unfilled_cancel_seconds=60)
    feed.bars = [_bar(0, 99), _bar(1, 101, low=80)]
    runner.step(_after(feed))
    fake.fill("000000001", "101", at="23:02:30")  # inside the 14:02 bar, which has not come
    runner.step(_after(feed, 40))
    state = _state(runner, store)
    assert state.entry_bar_time == int((START + timedelta(minutes=2)).timestamp())
    assert _sell_intents(store) == []


def test_a_fill_time_before_the_order_is_not_believed(tmp_path: Path) -> None:
    fake = FakeKiwoomPaper()
    runner, fake, feed, store = _setup(tmp_path, fake, definition=STOP, unfilled_cancel_seconds=60)
    feed.bars = [_bar(0, 99), _bar(1, 101, low=80)]
    runner.step(_after(feed))
    fake.fill("000000001", "101", at="23:00:10")  # earlier than the order was sent
    runner.step(_after(feed, 40))
    assert _sell_intents(store) == []
    assert _state(runner, store).entry_time >= feed.bars[-1].end


def test_kill_switch_lets_the_protective_exit_out_and_nothing_else(tmp_path: Path) -> None:
    runner, fake, feed, store = _held(tmp_path)
    store.halt("pressed", source="test")
    feed.bars = [*feed.bars, _bar(2, 102)]
    report = runner.step(_after(feed))
    assert report.halted and report.submitted == 0  # no level reached, no signal acted on

    feed.bars = [*feed.bars, _bar(3, 95)]
    report = runner.step(_after(feed))
    assert report.halted and report.submitted == 1
    assert fake.orders[1].side == "1"
    assert store.outcome(_sell_intents(store)[0]["intent"]["intent_id"])[0] == "submitted"

    fake.fill("000000002", "95", at="23:04:30")
    runner.step(_after(feed, 40))
    feed.bars = [*feed.bars, _bar(4, 105)]
    assert runner.step(_after(feed)).submitted == 0  # the entry rule holds, but no new orders
    assert len(fake.orders) == 2


def test_kill_switch_reads_no_bars_without_a_position(tmp_path: Path) -> None:
    runner, _, feed, store = _setup(tmp_path, definition=STOP, unfilled_cancel_seconds=60)
    feed.bars = [_bar(0, 99)]
    store.halt("pressed", source="test")
    runner.step(_after(feed))
    assert feed.reads == 0


def test_disarming_stops_the_protective_exit_too(tmp_path: Path) -> None:
    runner, fake, feed, store = _held(tmp_path)
    store.disarm(source="test")
    feed.bars = [*feed.bars, _bar(2, 95)]
    runner.step(_after(feed))
    assert len(fake.orders) == 1
    assert store.outcome(_sell_intents(store)[0]["intent"]["intent_id"]) == ("blocked", "not_armed")


def test_limits_do_not_stop_a_protective_exit_but_are_recorded(tmp_path: Path) -> None:
    runner, fake, feed, store = _held(tmp_path)
    runner.plan = _with_limits(runner.plan, max_daily_orders=1, max_order_amount_krw=Decimal(1))
    feed.bars = [*feed.bars, _bar(2, 95)]
    assert runner.step(_after(feed)).submitted == 1
    row = _sell_intents(store)[0]
    assert row["intent"]["limit_check"]["passed"] is False
    assert set(row["intent"]["limit_check"]["reasons"]) >= {
        "daily_order_count_exceeded",
        "order_amount_exceeded",
    }
    assert row["outcome"]["status"] == "submitted"
    with store._connect() as db:
        assert db.execute("SELECT count(*) FROM limit_events").fetchone()[0] >= 1


def test_an_order_the_broker_cannot_take_is_still_stopped(tmp_path: Path) -> None:
    runner, fake, feed, store = _held(tmp_path)
    runner.plan = _with_limits(runner.plan, allowed_instrument_ids=(MSFT,))
    feed.bars = [*feed.bars, _bar(2, 95)]
    report = runner.step(_after(feed))
    assert report.submitted == 0 and report.blocked == 1
    assert len(fake.orders) == 1


def _with_limits(plan: Any, **changes: Any) -> Any:
    return replace(plan, limits=replace(plan.limits, **changes))


def test_a_refused_exit_is_recorded_and_the_next_bar_judges_again(tmp_path: Path) -> None:
    runner, fake, feed, store = _held(tmp_path)
    fake.reject_orders = True
    feed.bars = [*feed.bars, _bar(2, 95)]
    runner.step(_after(feed))
    assert _sell_intents(store)[0]["submission"]["status"] == "rejected"
    assert _state(runner, store) is not None

    # Back above the stop: nothing is sold on that bar.
    feed.bars = [*feed.bars, _bar(3, 101)]
    runner.step(_after(feed))
    assert len(_sell_intents(store)) == 1

    fake.reject_orders = False
    feed.bars = [*feed.bars, _bar(4, 94)]
    assert runner.step(_after(feed)).submitted == 1
    assert fake.orders[-1].side == "1"


def test_an_open_exit_is_not_joined_by_another(tmp_path: Path) -> None:
    runner, fake, feed, store = _held(tmp_path)
    feed.bars = [*feed.bars, _bar(2, 95)]
    runner.step(_after(feed))
    feed.bars = [*feed.bars, _bar(3, 94)]
    report = runner.step(_after(feed))
    assert report.submitted == 0 and f"{AAPL}:exit_waits_for_open_order" in report.skipped
    assert len(fake.orders) == 2


def test_only_an_unsettled_order_of_the_same_instrument_holds_the_exit(tmp_path: Path) -> None:
    runner, _, feed, store = _held(tmp_path)
    bought = store.intents()[0]["intent"]
    limits = {**bought["limit_check"]["limits"], "allowed_instrument_ids": [AAPL, MSFT]}
    other = {
        **bought,
        "intent_id": "f" * 64,
        "instrument_id": MSFT,
        "symbol": "MSFT",
        "limit_check": {**bought["limit_check"], "limits": limits},
    }
    store.record_intent(other, LimitResult(True, (), None))
    store.record_submission(runner._submission(other, _after(feed), "unknown", "buy", "00"))
    assert store.may_submit(runner.approval_id) == (False, "unknown_submission_pending")
    assert runner._exit_is_clear(AAPL, lambda: [])
    assert not runner._exit_is_clear(MSFT, lambda: [])


def test_a_restart_goes_on_from_the_stored_state_and_never_sells_twice(tmp_path: Path) -> None:
    runner, fake, feed, store = _held(tmp_path)
    feed.bars = [*feed.bars, _bar(2, 95)]
    # A crash after the order went out, before the state was saved.
    stored = store.save_protection
    store.save_protection = lambda state: (_ for _ in ()).throw(RuntimeError("crash"))
    with pytest.raises(RuntimeError):
        runner.step(_after(feed))
    store.save_protection = stored
    assert len(fake.orders) == 2

    again = PaperRunner(store, runner.broker, feed, runner.plan)
    report = again.step(_after(feed, 10))
    assert report.submitted == 0
    assert len(fake.orders) == 2
    assert again.approval_id == runner.approval_id


def test_a_level_reached_while_the_runner_was_down_still_sells(tmp_path: Path) -> None:
    runner, fake, feed, store = _held(tmp_path)
    feed.bars = [*feed.bars, _bar(2, 95), _bar(3, 102)]
    later = _after(feed) + timedelta(hours=2)  # far past the signal age limit
    assert runner.step(later).submitted == 1
    row = _sell_intents(store)[0]
    assert row["intent"]["data"]["signal_bar_start_utc"] == "2026-10-23T14:02:00Z"
    # Priced from the latest close, not the old bar's.
    assert row["intent"]["sizing"]["reference_price"] == "102"


def test_a_bar_outside_the_session_can_reach_a_level_but_moves_nothing(tmp_path: Path) -> None:
    trailing = {**DEFINITION, "trailing_stop": {"mode": "pct", "value": "2"}}
    runner, fake, feed, store = _held(tmp_path, trailing)
    feed.bars = [*feed.bars, _bar(2, 102)]
    runner.step(_after(feed))
    before = _state(runner, store).levels
    # A high outside the session does not raise the trailing stop.
    feed.outside_bars = [_bar(3, 120, high=125, low=119)]
    feed.bars = [*feed.bars, _bar(4, 102)]
    runner.step(_after(feed))
    assert _state(runner, store).levels.trail_high == max(before.trail_high, Decimal(102))
    assert _sell_intents(store) == []

    # A low outside the session does reach it, and the intent says where it came from.
    feed.outside_bars = [_bar(5, 90)]
    runner.step(
        datetime.fromtimestamp(feed.outside_bars[0].end, timezone.utc) + timedelta(seconds=5)
    )
    row = _sell_intents(store)[0]
    assert row["intent"]["data"]["session"] == "extended"
    assert row["intent"]["signal"]["summary"].endswith("trailing_stop")
    assert fake.orders[-1].side == "1"


def test_no_entry_while_the_atr_of_its_stop_is_unknown(tmp_path: Path) -> None:
    definition = {**DEFINITION, "stop_loss_atr": {"length": 5, "mult": "2"}}
    runner, fake, feed, store = _setup(tmp_path, definition=definition, unfilled_cancel_seconds=60)
    feed.bars = [_bar(0, 99), _bar(1, 101)]
    report = runner.step(_after(feed))
    assert report.submitted == 0 and f"{AAPL}:protection_atr_unknown" in report.skipped
    assert store.intents() == []

    feed.bars = [_bar(i, 101 + i, high=102 + i, low=100 + i) for i in range(8)]
    assert runner.step(_after(feed)).submitted == 1


def test_a_holding_from_elsewhere_is_left_alone_and_shown_unprotected(tmp_path: Path) -> None:
    runner, fake, feed, store = _setup(tmp_path, unfilled_cancel_seconds=60)
    feed.bars = [_bar(0, 99), _bar(1, 101)]
    runner.step(_after(feed))
    fake.fill("000000001", "101", at="23:02:30")
    runner.step(_after(feed, 40))

    fake2 = FakeKiwoomPaper()
    broker = KiwoomPaperBroker(
        "app-key",
        "secret-key",
        "demo",
        markets=frozenset({"us"}),
        transport=fake2.transport(),
        sleep=lambda _seconds: None,
    )
    other = replace(
        runner.plan,
        definition=STOP,
        strategy=replace(runner.plan.strategy, definition_sha256="d" * 64),
    )
    protecting = PaperRunner(store, cast(PaperBroker, broker), feed, other)
    store.arm(protecting.approval_id, source="test")
    feed.bars = [*feed.bars, _bar(2, 102, low=50)]
    report = protecting.step(_after(feed))
    assert f"{AAPL}:holding_without_protection" in report.skipped
    assert fake2.orders == []  # neither a protective exit nor a state appeared
    assert _state(protecting, store) is None
    assert store.positions()[0]["protection"] is None
    assert store.positions()[0]["watched"] is False
    validate_document(
        {
            "kind": "paper_position_list_response",
            "schema_version": "0.1",
            "positions": store.positions(),
        }
    )


def test_positions_say_whether_the_running_plan_watches_the_protection(tmp_path: Path) -> None:
    runner, _, _, store = _held(tmp_path)
    position = store.positions()[0]
    assert position["protection"]["stop"] == "95.95" and position["watched"] is True
    validate_document(
        {
            "kind": "paper_position_list_response",
            "schema_version": "0.1",
            "positions": store.positions(),
        }
    )
    store.halt("pressed", source="test")
    assert store.positions()[0]["watched"] is True
    store.release(source="test")
    assert store.positions()[0]["watched"] is False


# -- the resident service --------------------------------------------------------------


@pytest.fixture
def protective_service(tmp_path: Path) -> Any:
    fake = FakeKiwoomPaper()
    store = PaperOrderStore(tmp_path)
    backtests = BacktestStore(tmp_path / "backtests.sqlite3", lambda: START)
    feed = Feed()
    feed.bars = [_bar(0, 99), _bar(1, 101)]
    now = [_after(feed)]
    broker = KiwoomPaperBroker(
        "app-key",
        "secret-key",
        "demo",
        markets=frozenset({"us"}),
        transport=fake.transport(),
        sleep=lambda _seconds: None,
    )
    strategy = backtests.save_strategy("with a stop", STOP, None)
    runner, _, _, setup_store = _setup(
        tmp_path / "plan", definition=STOP, unfilled_cancel_seconds=60
    )
    content = setup_store.approval(runner.approval_id)
    assert content is not None
    content["strategy"]["strategy_id"] = strategy["strategy_id"]
    content["strategy"]["strategy_version"] = strategy["version"]
    content["strategy"]["definition_sha256"] = strategy["definition_sha256"]
    content["broker_account_ref"] = broker.account_ref
    plan = plan_from_approval(content, strategy["definition"])
    approval = store.record_approval(plan.approval_content())
    store.arm(approval, source="test")
    service = RunnerService(
        store, backtests.strategy, lambda ref, markets: broker, lambda: feed, now=lambda: now[0]
    )
    yield service, store, fake, feed, now
    backtests.close()


def test_service_keeps_protecting_under_the_kill_switch(protective_service: Any) -> None:
    service, store, fake, feed, now = protective_service
    assert service.tick().startswith("step")
    fake.fill("000000001", "101", at="23:02:30")
    now[0] = _after(feed, 40)
    assert service.tick().startswith("step")

    store.halt("pressed", source="test")
    feed.bars = [*feed.bars, _bar(2, 95)]
    now[0] = _after(feed)
    assert service.tick().startswith("step")
    assert fake.orders[-1].side == "1"

    store.release(source="test")  # the way to stop every order
    assert service.tick() == "idle"


def test_service_stops_everything_after_a_blocked_request(protective_service: Any) -> None:
    service, store, fake, feed, now = protective_service
    assert service.tick().startswith("step")
    fake.fill("000000001", "101", at="23:02:30")
    now[0] = _after(feed, 40)
    service.tick()
    store.halt(f"blocked request: {LiveDomainBlockedError.__name__}", source="runner")
    feed.bars = [*feed.bars, _bar(2, 95)]
    now[0] = _after(feed)
    assert service.tick() == "idle"
    assert len(fake.orders) == 1
