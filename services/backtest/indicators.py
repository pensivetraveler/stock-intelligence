"""Causal indicators for backtests, ported formula for formula from the chart's
`apps/desktop/src/indicators.ts`, so a strategy sees the same values a chart shows.

Every value at bar i uses bars 0..i only (a test appends future bars and checks earlier
values do not change). Until a formula has enough bars, the value is None. Values are
floats: indicators are observations; money is accounted in Decimal by the engine.

A shared fixture (tests/fixtures/indicators/parity.json) is reproduced by both this module
and the TypeScript one.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

Series = list[float | None]


@dataclass(frozen=True)
class Bar:
    time: int  # bar start, epoch seconds
    open: float
    high: float
    low: float
    close: float
    volume: float


def sma(values: Series, length: int) -> Series:
    out: Series = []
    total = 0.0
    count = 0
    window: list[float | None] = []
    for value in values:
        window.append(value)
        if value is not None:
            total += value
            count += 1
        if len(window) > length:
            dropped = window.pop(0)
            if dropped is not None:
                total -= dropped
                count -= 1
        out.append(total / length if len(window) == length and count == length else None)
    return out


def _smoothed(values: Series, length: int, alpha: float) -> Series:
    """Exponential average seeded with the simple average of the first `length` values."""
    out: Series = []
    previous: float | None = None
    seed: list[float] = []
    for value in values:
        if value is None:
            out.append(previous)
            continue
        if previous is None:
            seed.append(value)
            if len(seed) == length:
                total = 0.0
                for item in seed:
                    total += item
                previous = total / length
                out.append(previous)
            else:
                out.append(None)
            continue
        previous = alpha * value + (1 - alpha) * previous
        out.append(previous)
    return out


def ema(values: Series, length: int) -> Series:
    return _smoothed(values, length, 2 / (length + 1))


def rma(values: Series, length: int) -> Series:
    """Wilder's moving average, used by RSI and ATR."""
    return _smoothed(values, length, 1 / length)


def wma(values: Series, length: int) -> Series:
    out: Series = []
    denominator = length * (length + 1) / 2
    for i in range(len(values)):
        if i < length - 1:
            out.append(None)
            continue
        total = 0.0
        valid = True
        for k in range(length):
            value = values[i - k]
            if value is None:
                valid = False
                break
            total += value * (length - k)
        out.append(total / denominator if valid else None)
    return out


def stdev(values: Series, length: int) -> Series:
    """Population standard deviation over a window."""
    mean = sma(values, length)
    out: Series = []
    for i, average in enumerate(mean):
        if average is None:
            out.append(None)
            continue
        total = 0.0
        for k in range(length):
            value = values[i - k]
            assert value is not None
            total += (value - average) ** 2
        out.append(math.sqrt(total / length))
    return out


def _rolling(values: Series, length: int, pick: Callable[[list[float]], float]) -> Series:
    out: Series = []
    for i in range(len(values)):
        if i < length - 1:
            out.append(None)
            continue
        window = values[i - length + 1 : i + 1]
        out.append(pick([v for v in window if v is not None]) if None not in window else None)
    return out


def _combine(a: Series, b: Series, op: Callable[[float, float], float | None]) -> Series:
    return [None if x is None or y is None else op(x, y) for x, y in zip(a, b, strict=True)]


def _minus(a: Series, b: Series) -> Series:
    return _combine(a, b, lambda x, y: x - y)


def _plus(a: Series, b: Series) -> Series:
    return _combine(a, b, lambda x, y: x + y)


def _times(a: Series, k: float) -> Series:
    return [None if x is None else x * k for x in a]


def _change(values: Series) -> Series:
    out: Series = [None]
    for i in range(1, len(values)):
        value, before = values[i], values[i - 1]
        out.append(None if value is None or before is None else value - before)
    return out[: len(values)]


def _true_range(bars: Sequence[Bar]) -> Series:
    out: Series = []
    for i, bar in enumerate(bars):
        if i == 0:
            out.append(bar.high - bar.low)
        else:
            previous = bars[i - 1].close
            out.append(max(bar.high - bar.low, abs(bar.high - previous), abs(bar.low - previous)))
    return out


def closes(bars: Sequence[Bar]) -> Series:
    return [bar.close for bar in bars]


def rsi(source: Series, length: int) -> Series:
    delta = _change(source)
    gains: Series = [None if v is None else max(v, 0.0) for v in delta]
    losses: Series = [None if v is None else max(-v, 0.0) for v in delta]

    def value(gain: float, loss: float) -> float:
        if loss == 0:
            return 100.0
        if gain == 0:
            return 0.0
        return 100 - 100 / (1 + gain / loss)

    return _combine(rma(gains, length), rma(losses, length), value)


def _stochastic(bars: Sequence[Bar], source: Series, length: int) -> Series:
    high = _rolling([bar.high for bar in bars], length, max)
    low = _rolling([bar.low for bar in bars], length, min)
    out: Series = []
    for value, top, bottom in zip(source, high, low, strict=True):
        if value is None or top is None or bottom is None or top == bottom:
            out.append(None)
        else:
            out.append(100 * (value - bottom) / (top - bottom))
    return out


def _vwap(bars: Sequence[Bar], time_zone: str) -> Series:
    zone = ZoneInfo(time_zone)
    day = ""
    price_volume = 0.0
    volume = 0.0
    out: Series = []
    for bar in bars:
        today = datetime.fromtimestamp(bar.time, timezone.utc).astimezone(zone).date().isoformat()
        if today != day:
            day = today
            price_volume = 0.0
            volume = 0.0
        price_volume += ((bar.high + bar.low + bar.close) / 3) * bar.volume
        volume += bar.volume
        out.append(None if volume == 0 else price_volume / volume)
    return out


def _obv(bars: Sequence[Bar]) -> Series:
    total = 0.0
    out: Series = []
    for i, bar in enumerate(bars):
        if i > 0:
            previous = bars[i - 1].close
            if bar.close > previous:
                total += bar.volume
            elif bar.close < previous:
                total -= bar.volume
        out.append(total)
    return out


@dataclass(frozen=True)
class IndicatorSpec:
    params: dict[str, float]  # defaults, as on the chart
    outputs: tuple[str, ...]
    compute: Callable[[Sequence[Bar], dict[str, float], str], dict[str, Series]]
    version: str = "1"


def _int(params: dict[str, float], name: str) -> int:
    value = params[name]
    if value != int(value) or value < 1 or value > 500:
        raise ValueError(f"{name} must be a whole number from 1 to 500")
    return int(value)


def _macd(bars: Sequence[Bar], p: dict[str, float], _zone: str) -> dict[str, Series]:
    macd = _minus(ema(closes(bars), _int(p, "fast")), ema(closes(bars), _int(p, "slow")))
    signal = ema(macd, _int(p, "signal"))
    return {"macd": macd, "signal": signal, "histogram": _minus(macd, signal)}


def _bb(bars: Sequence[Bar], p: dict[str, float], _zone: str) -> dict[str, Series]:
    length = _int(p, "length")
    basis = sma(closes(bars), length)
    deviation = _times(stdev(closes(bars), length), p["mult"])
    return {"basis": basis, "upper": _plus(basis, deviation), "lower": _minus(basis, deviation)}


def _stoch(bars: Sequence[Bar], p: dict[str, float], _zone: str) -> dict[str, Series]:
    k = sma(_stochastic(bars, closes(bars), _int(p, "k")), _int(p, "smoothK"))
    return {"k": k, "d": sma(k, _int(p, "d"))}


INDICATORS: dict[str, IndicatorSpec] = {
    "sma": IndicatorSpec(
        {"length": 9}, ("value",), lambda b, p, z: {"value": sma(closes(b), _int(p, "length"))}
    ),
    "ema": IndicatorSpec(
        {"length": 9}, ("value",), lambda b, p, z: {"value": ema(closes(b), _int(p, "length"))}
    ),
    "wma": IndicatorSpec(
        {"length": 9}, ("value",), lambda b, p, z: {"value": wma(closes(b), _int(p, "length"))}
    ),
    "rsi": IndicatorSpec(
        {"length": 14}, ("value",), lambda b, p, z: {"value": rsi(closes(b), _int(p, "length"))}
    ),
    "macd": IndicatorSpec(
        {"fast": 12, "slow": 26, "signal": 9}, ("macd", "signal", "histogram"), _macd
    ),
    "bb": IndicatorSpec({"length": 20, "mult": 2}, ("basis", "upper", "lower"), _bb),
    "atr": IndicatorSpec(
        {"length": 14},
        ("value",),
        lambda b, p, z: {"value": rma(_true_range(b), _int(p, "length"))},
    ),
    "stoch": IndicatorSpec({"k": 14, "smoothK": 1, "d": 3}, ("k", "d"), _stoch),
    "vwap": IndicatorSpec({}, ("value",), lambda b, p, z: {"value": _vwap(b, z)}),
    "obv": IndicatorSpec({}, ("value",), lambda b, p, z: {"value": _obv(b)}),
}


def compute(
    indicator_id: str,
    bars: Sequence[Bar],
    params: dict[str, float] | None = None,
    time_zone: str = "America/New_York",
) -> dict[str, Series]:
    spec = INDICATORS.get(indicator_id)
    if spec is None:
        raise ValueError(f"unknown indicator {indicator_id!r}")
    merged = {**spec.params, **(params or {})}
    unknown = set(merged) - set(spec.params)
    if unknown:
        raise ValueError(f"{indicator_id} does not take {', '.join(sorted(unknown))}")
    return spec.compute(bars, merged, time_zone)
