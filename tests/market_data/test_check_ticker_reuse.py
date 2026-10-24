"""The ticker-reuse maintenance check's arithmetic (ADR 0033 rule 6); no network."""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from decimal import Decimal
from types import ModuleType


def _check() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_ticker_reuse", "scripts/check_ticker_reuse.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fills_are_compared_with_the_hours_around_them() -> None:
    check = _check()
    hourly = {
        "2023-05-05T13": (Decimal("0.74"), Decimal("0.95")),
        "2023-05-05T14": (Decimal("0.80"), Decimal("0.99")),
    }
    at = datetime(2023, 5, 5, 12, 31, tzinfo=timezone.utc)  # pre-market: 13h is next
    fills = [
        (at, Decimal("0.96")),  # within 0.95 + 3% (hour 13; 11 and 12 have no bar)
        (at, Decimal("22.10")),  # another company's price
        (datetime(2023, 5, 8, 15, 0, tzinfo=timezone.utc), Decimal("1")),  # no bar near
    ]
    assert check.count_outside(fills, hourly) == (1, 1)


def test_only_instruments_with_enough_fills_mostly_outside_are_flagged() -> None:
    check = _check()
    results = {
        "us:strr.d23:xnas:2026": {"fills": 8, "outside": 8, "no_bar": 0},
        "us:coco:xnas:2026": {"fills": 2, "outside": 1, "no_bar": 0},
        "us:aapl:xnas:2026": {"fills": 400, "outside": 3, "no_bar": 1},
    }
    assert [name for name, _ in check.flagged(results, 3, 0.5)] == ["us:strr.d23:xnas:2026"]
