"""ADR 0057 stage 7 paper limits (2026-11-25) and the day summary."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from packages.contracts.validator import validate_document
from services.backtest.strategy import fingerprint
from services.paper_trading import daily
from services.paper_trading.__main__ import main
from services.paper_trading.intent import StrategyRef
from services.paper_trading.limits import OrderAmount, PaperLimits, RunnerFacts, check_limits
from services.paper_trading.runner import InstrumentPlan, plan_from_approval
from tests.paper_trading.test_runner import AAPL, DEFINITION, _api_ids, _bars, _end, _setup

MSFT = "us:msft:xnas:2026"
STAGE7 = {
    "max_daily_new_instruments": 3,
    "max_daily_loss_usd": Decimal("300"),
    "max_position_amount_usd": Decimal("2000"),
}


def _buy(instrument: str = AAPL, quantity: str = "5", price: str = "100") -> OrderAmount:
    return OrderAmount(
        instrument, "buy", "us", "USD", "limit", Decimal(quantity), Decimal(price), None
    )


def _facts(**changes: Any) -> RunnerFacts:
    base = RunnerFacts(frozenset({"us"}), 0, 0, None, None)
    return replace(base, **changes)


def _fill(
    side: str, quantity: str, price: str, at: str, instrument: str = AAPL, intent: str = "i1"
) -> dict[str, Any]:
    return {
        "execution_id": f"x-{side}-{at}",
        "instrument_id": instrument,
        "symbol": "AAPL" if instrument == AAPL else "MSFT",
        "side": side,
        "quantity": quantity,
        "price": price,
        "currency": "USD",
        "intent_id": intent,
        "executed_at_utc": at,
        "synced_at_utc": at,
    }


# -- limits document and approval ----------------------------------------------------


def test_stage7_limits_enter_the_approval_only_when_set() -> None:
    plain = PaperLimits((AAPL,)).document()
    assert not {"max_daily_new_instruments", "max_daily_loss_usd"} & plain.keys()
    document = PaperLimits((AAPL,), max_daily_orders=20, **STAGE7).document()
    assert document["max_daily_new_instruments"] == 3
    assert document["max_daily_loss_usd"] == "300"
    assert document["max_position_amount_usd"] == "2000"


def test_plan_with_stage7_limits_round_trips(tmp_path: Path) -> None:
    limits = PaperLimits((AAPL,), max_order_amount_krw=Decimal("2000000"), **STAGE7)
    strategy = StrategyRef("strat-above-100", 1, fingerprint(DEFINITION), "rules")
    runner, *_ = _setup(tmp_path, limits=limits, strategy=strategy)
    content = runner.plan.approval_content()
    rebuilt = plan_from_approval(content, runner.plan.definition)
    assert rebuilt.limits == limits
    assert rebuilt.approval_content() == content


def test_a_plan_without_them_keeps_its_approval_id(tmp_path: Path) -> None:
    runner, *_ = _setup(tmp_path)
    assert "max_daily_loss_usd" not in runner.plan.approval_content()["limits"]


# -- check_limits ----------------------------------------------------------------------


def test_a_fourth_new_instrument_is_blocked_but_not_a_known_one() -> None:
    limits = PaperLimits((AAPL, MSFT), max_daily_new_instruments=1)
    bought = _facts(new_instruments_today=frozenset({AAPL}))
    assert check_limits(_buy(AAPL), limits, bought, None).passed
    blocked = check_limits(_buy(MSFT), limits, bought, None)
    assert blocked.reasons == ("daily_new_instrument_limit_exceeded",)
    sell = replace(_buy(MSFT), side="sell")
    assert (
        "daily_new_instrument_limit_exceeded"
        not in check_limits(sell, limits, bought, None).reasons
    )


def test_position_in_dollars_counts_what_is_held_and_bid() -> None:
    limits = PaperLimits((AAPL,), max_position_amount_usd=Decimal("2000"))
    held = _facts(position_amount_usd=Decimal("1500"))
    assert check_limits(_buy(quantity="5"), limits, held, None).passed  # 1500 + 500
    over = check_limits(_buy(quantity="6"), limits, held, None)
    assert over.reasons == ("position_amount_exceeded",)
    assert check_limits(_buy(), limits, _facts(), None).reasons == ("position_unknown",)


def test_daily_dollar_loss_blocks_and_unknown_blocks() -> None:
    limits = PaperLimits((AAPL,), max_daily_loss_usd=Decimal("300"))
    assert check_limits(_buy(), limits, _facts(daily_loss_usd=Decimal("299")), None).passed
    hit = check_limits(_buy(), limits, _facts(daily_loss_usd=Decimal("300")), None)
    assert hit.reasons == ("daily_loss_exceeded",)
    assert check_limits(_buy(), limits, _facts(), None).reasons == ("daily_loss_unknown",)


# -- daily loss and summary ----------------------------------------------------------


def test_daily_loss_is_realized_plus_unrealized_at_average_cost() -> None:
    fills = [
        _fill("buy", "10", "100", "2026-11-24T15:00:00Z"),  # yesterday
        _fill("buy", "10", "110", "2026-11-25T15:00:00Z"),  # average 105
        _fill("sell", "10", "95", "2026-11-25T16:00:00Z", intent="i2"),  # −100 realized
    ]
    day = date(2026, 11, 25)
    # 10 held at 105, priced 100: −50 unrealized.
    assert daily.daily_loss(fills, day, "America/New_York", {AAPL: Decimal(100)}) == 150
    assert daily.daily_loss(fills, day, "America/New_York", {AAPL: Decimal(130)}) == 0
    assert daily.daily_loss(fills, day, "America/New_York", {}) is None


def test_day_summary_lists_entries_exits_and_why() -> None:
    fills = [
        _fill("buy", "10", "100", "2026-11-25T15:00:00Z", intent="buy1"),
        _fill("sell", "8", "125", "2026-11-25T16:00:00Z", intent="tp1"),
        _fill("buy", "4", "50", "2026-11-26T15:00:00Z", MSFT, "buy2"),  # another day
    ]
    intents = {"tp1": {"signal": {"summary": "protective exit: target"}}}
    summary = daily.day_summary(fills, intents, date(2026, 11, 25), "America/New_York")
    validate_document(summary)
    (row,) = summary["instruments"]
    assert row["symbol"] == "AAPL" and row["frame"] is None
    assert (row["bought_quantity"], row["average_buy_price"]) == ("10", "100")
    assert (row["sold_quantity"], row["realized"], row["held_quantity"]) == ("8", "200", "2")
    assert row["exits"][0]["reason"] == "target"
    assert summary["realized_total"] == "200"


def test_summary_command_reads_the_ledger(tmp_path: Path, capsys: Any) -> None:
    assert main(["summary", "--data-dir", str(tmp_path)]) == 2
    assert main(["summary", "--day", "2026-11-25", "--data-dir", str(tmp_path)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["kind"] == "paper_day_summary" and printed["instruments"] == []


# -- the runner ------------------------------------------------------------------------


def test_reaching_the_daily_loss_turns_the_kill_switch_on(tmp_path: Path) -> None:
    limits = PaperLimits((AAPL,), max_daily_loss_usd=Decimal("100"))
    runner, fake, feed, store = _setup(tmp_path, limits=limits)
    feed.bars = _bars(99, 101)
    assert runner.step(_end(feed)).submitted == 1
    fake.fill("000000001", "101", at="23:01:30")
    assert runner.step(_end(feed, 30)).fills == 1
    assert not store.status()["halted"]
    feed.bars = _bars(99, 101, 102, 60)  # 3 × (60 − 101) = −123
    report = runner.step(_end(feed))
    assert report.halted
    status = store.status()
    assert status["halted"] and status["halted_reason"].startswith("daily_loss_limit")


def test_a_buy_over_the_dollar_position_is_blocked(tmp_path: Path) -> None:
    limits = PaperLimits((AAPL,), max_position_amount_usd=Decimal("200"))
    runner, fake, feed, _ = _setup(tmp_path, limits=limits)
    feed.bars = _bars(99, 101)  # 3 × 101 > 200
    report = runner.step(_end(feed))
    assert report.blocked == 1
    assert "ust20000" not in _api_ids(fake)


def test_only_one_new_instrument_a_day_when_the_limit_is_one(tmp_path: Path) -> None:
    limits = PaperLimits((AAPL, MSFT), max_daily_new_instruments=1)
    instruments = (
        InstrumentPlan(AAPL, "AAPL", "America/New_York"),
        InstrumentPlan(MSFT, "MSFT", "America/New_York"),
    )
    runner, fake, feed, _ = _setup(tmp_path, limits=limits, instruments=instruments)
    feed.bars = _bars(99, 101)
    report = runner.step(_end(feed))
    assert (report.submitted, report.blocked) == (1, 1)
    assert [order.stk_cd for order in fake.orders] == ["AAPL"]
