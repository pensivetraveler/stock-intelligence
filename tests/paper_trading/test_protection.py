"""A paper position's protection decides what its backtest decides, bar by bar (ADR 0044 G2)."""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from services.backtest.data import EngineBar, InstrumentInput
from services.backtest.engine import Backtest, EngineConfig, RuleSignals
from services.backtest.protection import Levels, ProtectionRules
from services.backtest.strategy import validate_definition
from services.paper_trading.limits import LimitResult
from services.paper_trading.protection import ProtectionState, advance, open_state
from services.paper_trading.store import PaperOrderStore, PaperStoreError
from tests.paper_trading.test_store import _store

NY = "America/New_York"
START = datetime(2026, 7, 1, 13, 30, tzinfo=timezone.utc)
ENTRY = {"op": "gt", "left": {"price": "close"}, "right": {"price": "close", "offset": 1}}
ACCOUNT = "kiwoom-test-demo"
PASSED = LimitResult(True, (), None)
CASES: list[dict[str, Any]] = [
    {"stop_loss_pct": 1},
    {"take_profit_pct": 1},
    {"stop_loss_pct": 0.5, "take_profit_pct": 0.5},
    {"stop_loss_atr": {"length": 5, "mult": 2}},
    {"trailing_stop": {"mode": "pct", "value": 1}},
    {"trailing_stop": {"mode": "atr", "length": 5, "mult": 2}},
    {
        "stop_loss_pct": 2,
        "take_profit_pct": 3,
        "trailing_stop": {"mode": "atr", "length": 14, "mult": 3},
    },
    {"stop_loss_atr": {"length": 14, "mult": 3}, "trailing_stop": {"mode": "pct", "value": 1.5}},
]


def walk(seed: int, count: int = 400) -> list[EngineBar]:
    """A random walk with gaps, so stops are hit at the open as well as inside bars."""
    rng = random.Random(seed)
    price = Decimal("100.00")
    bars = []
    for i in range(count):
        at = int((START + timedelta(minutes=5 * i)).timestamp())
        gap = Decimal(rng.choice([0, 0, 0, 0, 0, 0, 0, 0, 40, -60])) / 100
        open_ = (price + gap).quantize(Decimal("0.01"))
        close = (open_ + Decimal(rng.randint(-40, 40)) / 100).quantize(Decimal("0.01"))
        high = max(open_, close) + Decimal(rng.randint(0, 30)) / 100
        low = min(open_, close) - Decimal(rng.randint(0, 30)) / 100
        bars.append(EngineBar(at, at + 300, open_, high, low, close, Decimal(1000), True))
        price = close
    return bars


def backtest(extra: dict[str, Any], bars: list[EngineBar]) -> tuple[dict[str, Any], dict[str, Any]]:
    clean = validate_definition(
        {
            "type": "rules",
            "entry": ENTRY,
            "exit": None,
            "sizing": {"mode": "fixed_shares", "value": 1},
            **extra,
        }
    )
    inputs = [InstrumentInput("a", NY, bars, {"instrument_id": "a"})]
    config = EngineConfig(Decimal("1000000"), "USD")
    return clean, Backtest("r", clean, inputs, config, RuleSignals(clean, inputs)).run()


def _epoch(text: str) -> int:
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())


def _pairs(result: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    fills = result["fills"]
    return [(fills[i], fills[i + 1]) for i in range(0, len(fills) - 1, 2)]


@pytest.mark.parametrize("extra", CASES)
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_every_exit_matches_the_backtest(extra: dict[str, Any], seed: int) -> None:
    bars = walk(seed)
    clean, result = backtest(extra, bars)
    rules = ProtectionRules.of(clean)
    pairs = _pairs(result)
    assert pairs
    for entry, exit_ in pairs:
        fill_time = _epoch(entry["filled_at_utc"])
        state = open_state(rules, "i", "a", Decimal(entry["price"]), fill_time, bars, NY)
        assert state is not None
        # One bar at a time, with the state written out and read back between bars: a restart
        # at any point must change nothing, and later bars must never be needed.
        decided = None
        first = next(i for i, bar in enumerate(bars) if bar.time == state.entry_bar_time)
        for count in range(first + 1, len(bars) + 1):
            state, decided = advance(rules, state, bars[:count], NY)
            state = ProtectionState.from_json(state.to_json())
            if decided is not None:
                break
        assert decided is not None
        assert decided.bar_time == _epoch(exit_["filled_at_utc"])
        # The engine records fills to six places; the levels themselves are not rounded.
        assert abs(decided.hit.reference - Decimal(exit_["price"])) <= Decimal("0.000001")
        assert decided.hit.reason == exit_["fill_reason"]


def test_advancing_all_at_once_gives_the_same_exit() -> None:
    bars = walk(4)
    clean, result = backtest({"trailing_stop": {"mode": "pct", "value": 1}}, bars)
    rules = ProtectionRules.of(clean)
    entry, exit_ = _pairs(result)[0]
    state = open_state(
        rules, "i", "a", Decimal(entry["price"]), _epoch(entry["filled_at_utc"]), bars, NY
    )
    assert state is not None
    _, decided = advance(rules, state, bars, NY)
    assert decided is not None
    assert decided.bar_time == _epoch(exit_["filled_at_utc"])


def _bar(minute: int, o: str, h: str, l: str, c: str, final: bool = True) -> EngineBar:  # noqa: E741
    at = int((START + timedelta(minutes=minute)).timestamp())
    return EngineBar(
        at, at + 60, Decimal(o), Decimal(h), Decimal(l), Decimal(c), Decimal(100), final
    )


TRAIL = ProtectionRules(trailing={"mode": "pct", "value": "10"})


def test_a_fill_inside_a_bar_may_exit_there_but_its_high_does_not_lift_the_stop() -> None:
    bars = [_bar(0, "100", "200", "99", "100")]
    fill = bars[0].time + 30
    state = open_state(TRAIL, "i", "a", Decimal(100), fill, bars, NY)
    assert state is not None
    after, decided = advance(TRAIL, state, bars, NY)
    assert decided is None
    assert after.levels.trail_high == Decimal(100)
    assert after.levels.trail_stop == Decimal(90)
    low = [_bar(0, "100", "100", "80", "85")]
    _, decided = advance(TRAIL, state, low, NY)
    assert decided is not None
    assert decided.same_bar_as_entry


def test_a_bar_not_final_is_not_judged() -> None:
    state = open_state(TRAIL, "i", "a", Decimal(100), START.timestamp().__int__(), [], NY)
    assert state is not None
    after, decided = advance(TRAIL, state, [_bar(0, "100", "100", "50", "60", final=False)], NY)
    assert decided is None
    assert after.last_bar_time is None


def test_no_state_while_the_atr_is_unknown() -> None:
    rules = ProtectionRules(stop_atr={"length": 50, "mult": "2"})
    bars = [_bar(0, "100", "101", "99", "100")]
    assert open_state(rules, "i", "a", Decimal(100), bars[0].time, bars, NY) is None


def _opened(tmp_path: Path) -> tuple[PaperOrderStore, ProtectionState]:
    store, _, intent = _store(tmp_path)
    store.record_intent(intent, PASSED)
    state = ProtectionState(
        intent["intent_id"],
        intent["instrument_id"],
        Decimal(100),
        1000,
        1000,
        Levels(Decimal(95), Decimal(110), Decimal(90), Decimal(100)),
    )
    store.open_protection(ACCOUNT, state)
    return store, state


def test_the_state_survives_a_restart(tmp_path: Path) -> None:
    store, state = _opened(tmp_path)
    moved = replace(
        state,
        levels=replace(state.levels, trail_stop=Decimal(99), trail_high=Decimal(110)),
        last_bar_time=1060,
    )
    store.save_protection(moved)
    reopened = PaperOrderStore(tmp_path)
    assert reopened.active_protection(ACCOUNT, state.instrument_id) == moved
    reopened.close_protection(state.intent_id, "stop_loss")
    assert reopened.active_protection(ACCOUNT, state.instrument_id) is None


@pytest.mark.parametrize(
    "change",
    [
        {"levels": Levels(Decimal(95), Decimal(110), Decimal(80), Decimal(100))},
        {"levels": Levels(Decimal(90), Decimal(110), Decimal(90), Decimal(100))},
        {"levels": Levels(Decimal(95), Decimal(120), Decimal(90), Decimal(100))},
    ],
)
def test_the_store_refuses_levels_a_position_cannot_have(
    tmp_path: Path, change: dict[str, Any]
) -> None:
    store, state = _opened(tmp_path)
    with pytest.raises(PaperStoreError):
        store.save_protection(replace(state, **change))


def test_the_store_refuses_going_back_and_a_second_open_state(tmp_path: Path) -> None:
    store, state = _opened(tmp_path)
    store.save_protection(replace(state, last_bar_time=1060))
    with pytest.raises(PaperStoreError):
        store.save_protection(replace(state, last_bar_time=1000))
    with pytest.raises(PaperStoreError):
        store.open_protection(ACCOUNT, state)
