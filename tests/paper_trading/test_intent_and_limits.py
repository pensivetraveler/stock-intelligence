from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from services.paper_trading.intent import (
    FxRate,
    NonFinalBarError,
    OrderSpec,
    SignalData,
    Sizing,
    StrategyRef,
    build_intent,
)
from services.paper_trading.limits import PaperLimits, RunnerFacts

AAPL = "us:aapl:xnas:2026"
BAR_START = datetime(2026, 10, 23, 14, 30, tzinfo=timezone.utc)
BAR_END = BAR_START + timedelta(minutes=1)
STRATEGY = StrategyRef("strat-sma", 3, "c" * 64, "rules")
DATA = SignalData(
    data_as_of_utc=BAR_END,
    signal_bar_start_utc=BAR_START,
    signal_bar_end_utc=BAR_END,
    signal_bar_final=True,
    timeframe="1m",
    source_id="alpaca",
    feed_scope="alpaca-sip",
    dataset_revision="alpaca-sip-r1",
    adjustment="raw",
    session="regular",
    input_bar_count=120,
    input_bars_sha256="d" * 64,
    calendar_id="XNYS",
    calendar_version="exchange-calendars-4.13.2",
)
SIZING = Sizing("shares", Decimal("3"), Decimal("199.90"), "signal bar close", BAR_END)
FX = FxRate(Decimal("1400"), "fixed rate confirmed at approval", BAR_START)
LIMITS = PaperLimits((AAPL,), max_order_amount_krw=Decimal("1000000"))
FACTS = RunnerFacts(
    supported_markets=frozenset({"us"}),
    unknown_submissions=0,
    orders_today=0,
    daily_loss_rate=None,
    position_amount_krw=None,
)


def _build(**overrides: Any) -> tuple[dict[str, Any], Any]:
    arguments: dict[str, Any] = {
        "broker_id": "kiwoom",
        "broker_account_ref": "kiwoom-0123456789abcdef-demo",
        "approval_id": "b" * 64,
        "strategy": STRATEGY,
        "instrument_id": AAPL,
        "symbol": "AAPL",
        "side": "buy",
        "data": DATA,
        "signal_summary": "SMA(20) crossed above SMA(50)",
        "operands": [("sma(20)", Decimal("199.85")), ("sma(50)", None)],
        "sizing": SIZING,
        "order": OrderSpec("limit", Decimal("3"), limit_price=Decimal("200")),
        "fx": FX,
        "limits": LIMITS,
        "facts": FACTS,
        "created_at": BAR_END + timedelta(seconds=5),
    }
    arguments.update(overrides)
    return build_intent(**arguments)


def test_limit_intent_within_the_amount_limit() -> None:
    document, result = _build()
    assert result.passed
    assert document["limit_check"]["order_amount_krw"] == "840000"
    assert document["environment"] == "demo"
    assert document["limit_check"]["limits"]["max_daily_orders"] is None


def test_same_signal_gets_the_same_intent_id_and_bytes() -> None:
    first, _ = _build()
    second, _ = _build()
    assert first == second
    other, _ = _build(data=replace(DATA, signal_bar_start_utc=BAR_START - timedelta(minutes=1)))
    assert other["intent_id"] != first["intent_id"]


def test_non_final_bar_makes_no_intent() -> None:
    with pytest.raises(NonFinalBarError):
        _build(data=replace(DATA, signal_bar_final=False))


def test_market_order_is_checked_at_its_estimated_maximum() -> None:
    order = OrderSpec("market", Decimal("3"), market_buffer_bps=Decimal("50"))
    document, result = _build(order=order)
    assert result.passed
    assert document["order"]["estimated_max_price"] == "200.8995"
    assert document["order"]["limit_price"] is None
    # 3 × 199.90 × 1.005 × 1400
    assert result.order_amount_krw == Decimal("843777.9")


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        (
            {"order": OrderSpec("limit", Decimal("4"), limit_price=Decimal("200"))},
            "order_amount_exceeded",
        ),
        ({"fx": None}, "fx_unavailable"),
        ({"instrument_id": "us:msft:xnas:2026", "symbol": "MSFT"}, "symbol_not_allowed"),
        ({"facts": replace(FACTS, supported_markets=frozenset())}, "market_not_supported"),
        ({"facts": replace(FACTS, unknown_submissions=1)}, "unknown_submission_pending"),
    ],
)
def test_limit_failures_are_recorded_with_their_reason(
    overrides: dict[str, Any], reason: str
) -> None:
    document, result = _build(**overrides)
    assert not result.passed
    assert reason in result.reasons
    assert reason in document["limit_check"]["reasons"]


def test_amount_at_the_limit_passes() -> None:
    # 5 × 200 × 1000 = 1,000,000 KRW exactly
    fx = replace(FX, krw_per_usd=Decimal("1000"))
    order = OrderSpec("limit", Decimal("5"), limit_price=Decimal("200"))
    _, result = _build(order=order, fx=fx)
    assert result.passed


def test_unset_limits_do_not_block() -> None:
    limits = PaperLimits((AAPL,))
    facts = replace(FACTS, orders_today=10_000, daily_loss_rate=Decimal("0.9"))
    _, result = _build(limits=limits, facts=facts, fx=None)
    assert result.passed


@pytest.mark.parametrize(
    ("limits", "facts", "reason"),
    [
        (
            replace(LIMITS, max_daily_orders=3),
            replace(FACTS, orders_today=3),
            "daily_order_count_exceeded",
        ),
        (replace(LIMITS, max_daily_loss_rate=Decimal("0.02")), FACTS, "daily_loss_unknown"),
        (
            replace(LIMITS, max_daily_loss_rate=Decimal("0.02")),
            replace(FACTS, daily_loss_rate=Decimal("0.02")),
            "daily_loss_exceeded",
        ),
        (replace(LIMITS, max_position_amount_krw=Decimal("1000000")), FACTS, "position_unknown"),
    ],
)
def test_later_limits_apply_once_set(limits: PaperLimits, facts: RunnerFacts, reason: str) -> None:
    _, result = _build(limits=limits, facts=facts)
    assert reason in result.reasons


def test_korean_orders_need_no_exchange_rate() -> None:
    limits = PaperLimits(("kr:005930:xkrx:2026",), max_order_amount_krw=Decimal("1000000"))
    document, result = _build(
        instrument_id="kr:005930:xkrx:2026",
        symbol="005930",
        order=OrderSpec("limit", Decimal("10"), limit_price=Decimal("60000")),
        fx=None,
        limits=limits,
        facts=replace(FACTS, supported_markets=frozenset({"kr"})),
    )
    assert result.passed
    assert document["currency"] == "KRW"
