"""Close path features of the off-exchange price structure (ADR 0053)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from services.screening.structure.model import _path_features

ZONE = {"low": Decimal("10"), "high": Decimal("11")}


def _bars(*closes: str) -> list[dict[str, Any]]:
    return [{"date": f"2026-10-{day:02d}", "close": Decimal(c)} for day, c in enumerate(closes, 1)]


def test_a_crossing_holds_only_when_the_next_close_stays_on_the_new_side() -> None:
    # 9 -> 12 crosses up and 12 holds; 12 -> 9 crosses down and 10.5 (back inside) does not hold.
    path = _path_features([ZONE], _bars("9", "12", "12", "9", "10.5"))
    assert path["cross_count_20"] == 2
    assert path["close_hold_rate"] == "0.5"


def test_a_crossing_on_the_newest_close_cannot_be_judged_yet() -> None:
    path = _path_features([ZONE], _bars("9", "9", "12"))
    assert path["cross_count_20"] == 1
    assert path["close_hold_rate"] is None


def test_reentries_count_closes_coming_back_into_a_zone() -> None:
    path = _path_features([ZONE], _bars("9", "10.5", "12", "10.2"))
    assert path["reentry_count_20"] == 2
    assert path["cross_count_20"] == 0
    assert path["close_hold_rate"] is None


def test_no_bars_means_no_path_values() -> None:
    assert _path_features([ZONE], []) == {
        "reentry_count_20": None,
        "cross_count_20": None,
        "close_hold_rate": None,
    }
