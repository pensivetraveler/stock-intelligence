"""The backtest indicators match the chart's and never look ahead."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.backtest.indicators import INDICATORS, Bar, compute, ema, sma

FIXTURE = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "indicators" / "parity.json").read_text()
)
BARS = [Bar(**bar) for bar in FIXTURE["bars"]]


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["id"])
def test_reproduces_the_shared_parity_fixture(case: dict) -> None:
    outputs = compute(case["id"], BARS, case["params"], FIXTURE["time_zone"])
    assert set(outputs) == set(case["outputs"])
    for key, expected in case["outputs"].items():
        for actual, value in zip(outputs[key], expected, strict=True):
            assert (actual is None) == (value is None)
            if value is not None:
                assert actual == pytest.approx(value, abs=1e-9)


def test_the_fixture_covers_every_indicator() -> None:
    assert {case["id"] for case in FIXTURE["cases"]} == set(INDICATORS)


@pytest.mark.parametrize("indicator_id", sorted(INDICATORS))
def test_appending_future_bars_never_changes_earlier_values(indicator_id: str) -> None:
    cut = 90
    early = compute(indicator_id, BARS[:cut])
    full = compute(indicator_id, BARS)
    for key, values in early.items():
        assert full[key][:cut] == values


def test_textbook_values_and_warm_up() -> None:
    assert sma([1, 2, 3, 4, 5], 3) == [None, None, 2, 3, 4]
    assert ema([1, 2, 3, 4, 5], 3) == [None, None, 2, 3, 4]


def test_rejects_unknown_indicators_and_parameters() -> None:
    with pytest.raises(ValueError, match="unknown indicator"):
        compute("nope", BARS)
    with pytest.raises(ValueError, match="does not take"):
        compute("sma", BARS, {"window": 3})
    with pytest.raises(ValueError, match="whole number"):
        compute("sma", BARS, {"length": 2.5})


def test_ichimoku_offers_no_look_ahead_output() -> None:
    # The chart's lagging span is a later close drawn in the past: never a backtest input.
    assert "lagging" not in INDICATORS["ichimoku"].outputs
    assert set(compute("ichimoku", BARS)) == {"conversion", "base", "spanA", "spanB"}


def test_fractional_settings_are_range_checked() -> None:
    with pytest.raises(ValueError, match="between"):
        compute("psar", BARS, {"maximum": 5})
    with pytest.raises(ValueError, match="between"):
        compute("supertrend", BARS, {"factor": 0})
