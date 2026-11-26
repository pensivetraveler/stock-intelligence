"""Stage 5 price levels (ADR 0057 §5, rules as of 2026-11-23)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from services.screening.price_levels import (
    ceil_tick,
    floor_tick,
    price_levels,
    tick_size,
    ticks_above,
)

SESSION = date(2026, 11, 24)
OPEN = datetime(2026, 11, 24, 14, 30, tzinfo=timezone.utc)
AS_OF = OPEN + timedelta(minutes=30, seconds=20)


def _bars(closes: list[str], highs: list[str] | None = None) -> list[dict[str, Any]]:
    highs = highs or closes
    return [
        {
            "interval_start_utc": (OPEN + timedelta(minutes=i)).isoformat().replace("+00:00", "Z"),
            "close": close,
            "high": high,
            "is_final": False,
        }
        for i, (close, high) in enumerate(zip(closes, highs, strict=True))
    ]


def _value(amount: str | None, status: str = "confirmed") -> dict[str, Any]:
    return {"amount": amount, "unit": "usd_per_share", "status": status, "as_of": "2026-08-14"}


def _dilution(*prices: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "check_id": 1,
        "status": "ok",
        "cutoff_utc": "2026-11-24T14:00:00Z",
        "created_at_utc": "2026-11-24T14:01:00Z",
        "analysis_as_of": "2026-11-24",
        "securities": [
            {"id": f"s{i}", "kind": "warrant", "price": price, "conditions": []}
            for i, price in enumerate(prices)
        ],
        "incomplete_reasons": [],
        **extra,
    }


def _off(*bins: tuple[str, str], **extra: Any) -> dict[str, Any]:
    return {
        "check_id": 2,
        "status": "ok",
        "cutoff_utc": "2026-11-24T14:00:00Z",
        "created_at_utc": "2026-11-24T14:01:00Z",
        "data_until_utc": "2026-11-24T14:00:00Z",
        "levels": [
            {"price_low": low, "price_high": high, "volume": "1", "share_pct": "10", "side": "at"}
            for low, high in bins
        ],
        **extra,
    }


def _run(**kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("strategy", "normal")
    kwargs.setdefault("session_date", SESSION)
    kwargs.setdefault("as_of", AS_OF)
    kwargs.setdefault("bars", _bars(["5.00"] * 30, ["5.00"] * 29 + ["5.40"]))
    kwargs.setdefault("dilution", None)
    kwargs.setdefault("off_exchange", None)
    return price_levels(**kwargs)


def test_ticks_follow_the_one_dollar_boundary() -> None:
    assert tick_size(Decimal("1")) == Decimal("0.01")
    assert tick_size(Decimal("0.9999")) == Decimal("0.0001")
    assert ticks_above(Decimal("0.9999"), 2) == Decimal("1.01")
    assert ticks_above(Decimal("0.5"), 2) == Decimal("0.5002")
    assert ticks_above(Decimal("1.234"), 2) == Decimal("1.26")
    assert ceil_tick(Decimal("0.99991")) == Decimal("1.00")
    assert floor_tick(Decimal("1.0099")) == Decimal("1.00")


def test_normal_frames_from_peaks_and_dilution() -> None:
    made = _run(
        dilution=_dilution(_value("6.00"), _value("4.00")),
        off_exchange=_off(("4.50", "4.60"), ("4.80", "4.90"), ("5.50", "5.60")),
    )
    assert made["market"]["price"] == "5"
    pull = made["frames"]["pullback"]
    # Nearest peak below 5.00 is the 4.80-4.90 bin, at its middle 4.85.
    assert pull["buy"]["price"] == "4.87"
    assert pull["buy"]["basis"]["source"] == "off_exchange"
    assert pull["stop"] == {
        "price": "4.46",  # 4.85 x 0.92 = 4.462, down to the tick
        "rule": "basis_x_0.92",
        "basis_price": "4.85",
        "reason": None,
    }
    assert pull["first_target"]["price"] == "6.09"  # 4.87 x 1.25 = 6.0875, up
    assert pull["first_target"]["sell_pct"] == "80"
    assert pull["quantity"]["shares"] == 205  # $1,000 / 4.87
    brk = made["frames"]["breakout"]
    # Nearest pressure above 5.00: the 5.55 peak (before the 6.00 dilution price).
    assert brk["buy"]["price"] == "5.57"
    assert brk["stop"]["price"] == "5.1"  # 5.55 x 0.92 = 5.106
    assert brk["buy"]["rule"] == "nearest_pressure_above_plus_2_ticks"
    assert [level["price"] for level in made["pressure_above"]] == ["5.55", "6"]
    assert made["sources"]["dilution"]["state"] == "used"


def test_conservative_halves_size_lowers_target_and_keeps_breakout() -> None:
    made = _run(strategy="conservative", off_exchange=_off(("4.80", "4.90"), ("5.50", "5.60")))
    pull = made["frames"]["pullback"]
    assert pull["quantity"]["budget_usd"] == "500"
    assert pull["quantity"]["shares"] == 102  # $500 / 4.87
    assert pull["first_target"]["price"] == "5.48"  # 4.87 x 1.125 = 5.47875, up
    assert pull["first_target"]["sell_pct"] == "90"
    assert made["frames"]["breakout"]["state"] == "set"


def test_no_peaks_means_no_pullback_and_breakout_on_the_day_high() -> None:
    made = _run(off_exchange=_off())
    assert made["frames"]["pullback"]["state"] == "none"
    assert "봉우리" in made["frames"]["pullback"]["reason"]
    brk = made["frames"]["breakout"]
    assert brk["buy"]["rule"] == "day_high_plus_2_ticks"
    assert brk["buy"]["price"] == "5.42"
    assert brk["stop"]["basis_price"] == "5.4"


def test_dilution_with_no_prices_is_shown_as_the_reason() -> None:
    blank = _dilution(_value(None, "unknown"), _value("7.00", "estimated"))
    made = _run(dilution=blank, off_exchange=_off(("5.50", "5.60")))
    report = made["sources"]["dilution"]
    assert report["state"] == "no_prices"
    assert report["values_skipped"] == 1  # the estimated 7.00; the null is not a value
    assert any("장외 체결 봉우리뿐" in note for note in made["notes"])
    assert any("희석 공급 가격이 없어" in note for note in made["frames"]["breakout"]["notes"])


def test_below_one_dollar_uses_the_small_tick() -> None:
    made = _run(
        bars=_bars(["0.8000"] * 30),
        off_exchange=_off(("0.7500", "0.7540"), ("0.9990", "1.0010")),
    )
    pull = made["frames"]["pullback"]
    assert pull["buy"]["price"] == "0.7522"  # middle 0.7520 + 2 x $0.0001
    assert pull["stop"]["price"] == "0.6918"  # 0.752 x 0.92 = 0.69184
    brk = made["frames"]["breakout"]
    assert brk["buy"]["price"] == "1.02"  # 1.0000 crosses $1: then $0.01 steps
    assert pull["quantity"]["shares"] == 1329


def test_a_hand_set_buy_has_no_basis_so_the_stop_is_minus_seven_percent() -> None:
    made = _run(off_exchange=_off(("4.80", "4.90")), user_buy={"pullback": "4.70"})
    pull = made["frames"]["pullback"]
    assert pull["buy"]["rule"] == "user_set"
    assert pull["stop"]["rule"] == "buy_minus_7pct"
    assert pull["stop"]["price"] == "4.37"  # 4.70 x 0.93 = 4.371
    assert pull["stop"]["reason"]


def test_no_future_data_is_read() -> None:
    bars = _bars(["5.00"] * 30 + ["9.00"], ["5.00"] * 30 + ["9.50"])
    later_off = _off(("4.80", "4.90"), data_until_utc="2026-11-24T15:30:00Z")
    later_dilution = _dilution(_value("5.20"), analysis_as_of="2026-11-25")
    made = _run(bars=bars, off_exchange=later_off, dilution=later_dilution)
    assert made["market"]["price"] == "5"
    assert made["market"]["day_high"] == "5"
    assert made["market"]["bars_after_as_of_ignored"] == 1
    assert made["sources"]["off_exchange"]["state"] == "future"
    assert made["sources"]["dilution"]["state"] == "future"
    assert made["frames"]["pullback"]["state"] == "none"
    assert made["frames"]["breakout"]["buy"]["price"] == "5.02"


def test_no_bars_no_levels() -> None:
    made = _run(bars=[], off_exchange=_off(("4.80", "4.90")))
    for frame in ("pullback", "breakout"):
        assert made["frames"][frame]["state"] == "none"
        assert "현재가" in made["frames"][frame]["reason"]


def test_naive_time_is_refused() -> None:
    with pytest.raises(ValueError):
        _run(as_of=AS_OF.replace(tzinfo=None))
