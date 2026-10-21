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


# --- The rest of the chart's indicators (parity with indicators.ts) --------------------


def _num(params: dict[str, float], name: str, low: float, high: float) -> float:
    value = params[name]
    if isinstance(value, bool) or not low <= value <= high:
        raise ValueError(f"{name} must be between {low:g} and {high:g}")
    return float(value)


def _change_n(values: Series, length: int) -> Series:
    out: Series = []
    for i, value in enumerate(values):
        before = values[i - length] if i >= length else None
        out.append(None if i < length or value is None or before is None else value - before)
    return out


def _shift(values: Series, by: int) -> Series:
    """Positive `by` moves values later in time (value at i comes from i - by)."""
    count = len(values)
    return [values[i - by] if 0 <= i - by < count else None for i in range(count)]


def _ratio(a: Series, b: Series, scale: float = 1.0) -> Series:
    return _combine(a, b, lambda x, y: None if y == 0 else scale * x / y)


def _hl2(bars: Sequence[Bar]) -> Series:
    return [(bar.high + bar.low) / 2 for bar in bars]


def _hlc3(bars: Sequence[Bar]) -> Series:
    return [(bar.high + bar.low + bar.close) / 3 for bar in bars]


def _highs(bars: Sequence[Bar]) -> Series:
    return [bar.high for bar in bars]


def _lows(bars: Sequence[Bar]) -> Series:
    return [bar.low for bar in bars]


def _volumes(bars: Sequence[Bar]) -> Series:
    return [bar.volume for bar in bars]


def _vwma(bars: Sequence[Bar], p: dict[str, float], _z: str) -> dict[str, Series]:
    length = _int(p, "length")
    price_volume: Series = [bar.close * bar.volume for bar in bars]
    return {"value": _ratio(sma(price_volume, length), sma(_volumes(bars), length))}


def _hma(bars: Sequence[Bar], p: dict[str, float], _z: str) -> dict[str, Series]:
    length = _int(p, "length")
    source = closes(bars)
    half = max(1, length // 2)
    raw = _minus(_times(wma(source, half), 2), wma(source, length))
    # JavaScript's Math.round; sqrt of a whole number is never exactly x.5.
    return {"value": wma(raw, max(1, int(math.floor(math.sqrt(length) + 0.5))))}


def _kc(bars: Sequence[Bar], p: dict[str, float], _z: str) -> dict[str, Series]:
    basis = ema(closes(bars), _int(p, "length"))
    band = _times(rma(_true_range(bars), _int(p, "atr")), _num(p, "mult", 0.1, 10))
    return {"basis": basis, "upper": _plus(basis, band), "lower": _minus(basis, band)}


def _dc(bars: Sequence[Bar], p: dict[str, float], _z: str) -> dict[str, Series]:
    length = _int(p, "length")
    upper = _rolling(_highs(bars), length, max)
    lower = _rolling(_lows(bars), length, min)
    return {"upper": upper, "lower": lower, "basis": _times(_plus(upper, lower), 0.5)}


def _env(bars: Sequence[Bar], p: dict[str, float], _z: str) -> dict[str, Series]:
    basis = sma(closes(bars), _int(p, "length"))
    percent = _num(p, "percent", 0.1, 100)
    return {
        "basis": basis,
        "upper": _times(basis, 1 + percent / 100),
        "lower": _times(basis, 1 - percent / 100),
    }


def _psar(bars: Sequence[Bar], p: dict[str, float], _z: str) -> dict[str, Series]:
    start = _num(p, "start", 0.001, 1)
    increment = _num(p, "increment", 0.001, 1)
    maximum = _num(p, "maximum", 0.01, 1)
    out: Series = [None] * len(bars)
    if len(bars) < 2:
        return {"value": out}
    rising = bars[1].close >= bars[0].close
    sar = bars[0].low if rising else bars[0].high
    extreme = bars[0].high if rising else bars[0].low
    factor = start
    for i in range(1, len(bars)):
        bar = bars[i]
        sar = sar + factor * (extreme - sar)
        if rising:
            sar = min(sar, bars[i - 1].low, bars[max(0, i - 2)].low)
            if bar.low < sar:
                rising, sar, extreme, factor = False, extreme, bar.low, start
            elif bar.high > extreme:
                extreme = bar.high
                factor = min(factor + increment, maximum)
        else:
            sar = max(sar, bars[i - 1].high, bars[max(0, i - 2)].high)
            if bar.high > sar:
                rising, sar, extreme, factor = True, extreme, bar.high, start
            elif bar.low < extreme:
                extreme = bar.low
                factor = min(factor + increment, maximum)
        out[i] = sar
    return {"value": out}


def _supertrend(bars: Sequence[Bar], p: dict[str, float], _z: str) -> dict[str, Series]:
    atr = rma(_true_range(bars), _int(p, "atr"))
    factor = _num(p, "factor", 0.1, 20)
    mid = _hl2(bars)
    up: Series = [None] * len(bars)
    down: Series = [None] * len(bars)
    upper = lower = 0.0
    direction = 1
    started = False
    for i, bar in enumerate(bars):
        band = atr[i]
        middle = mid[i]
        if band is None or middle is None:
            continue
        next_upper = middle + factor * band
        next_lower = middle - factor * band
        if started:
            previous_close = bars[i - 1].close
            if not (next_lower > lower or previous_close < lower):
                next_lower = lower
            if not (next_upper < upper or previous_close > upper):
                next_upper = upper
            if direction == 1 and bar.close < next_lower:
                direction = -1
            elif direction == -1 and bar.close > next_upper:
                direction = 1
        upper, lower, started = next_upper, next_lower, True
        if direction == 1:
            up[i] = lower
        else:
            down[i] = upper
    return {"up": up, "down": down}


def _ichimoku(bars: Sequence[Bar], p: dict[str, float], _z: str) -> dict[str, Series]:
    """Without the chart's lagging span: it is a later close drawn in the past, which
    would be look-ahead in a backtest."""

    def middle(length: int) -> Series:
        top = _rolling(_highs(bars), length, max)
        bottom = _rolling(_lows(bars), length, min)
        return _times(_plus(top, bottom), 0.5)

    conversion = middle(_int(p, "conversion"))
    base = middle(_int(p, "base"))
    displacement = _int(p, "displacement")
    return {
        "conversion": conversion,
        "base": base,
        "spanA": _shift(_times(_plus(conversion, base), 0.5), displacement - 1),
        "spanB": _shift(middle(_int(p, "spanB")), displacement - 1),
    }


def _stochrsi(bars: Sequence[Bar], p: dict[str, float], _z: str) -> dict[str, Series]:
    strength = rsi(closes(bars), _int(p, "rsi"))
    length = _int(p, "stoch")
    top = _rolling(strength, length, max)
    bottom = _rolling(strength, length, min)
    raw: Series = [
        None
        if value is None or high is None or low is None or high == low
        else 100 * (value - low) / (high - low)
        for value, high, low in zip(strength, top, bottom, strict=True)
    ]
    k = sma(raw, _int(p, "k"))
    return {"k": k, "d": sma(k, _int(p, "d"))}


def _cci(bars: Sequence[Bar], p: dict[str, float], _z: str) -> dict[str, Series]:
    length = _int(p, "length")
    typical = _hlc3(bars)
    mean = sma(typical, length)
    out: Series = []
    for i, (value, average) in enumerate(zip(typical, mean, strict=True)):
        if value is None or average is None:
            out.append(None)
            continue
        deviation = 0.0
        for k in range(length):
            item = typical[i - k]
            assert item is not None
            deviation += abs(item - average)
        deviation /= length
        out.append(None if deviation == 0 else (value - average) / (0.015 * deviation))
    return {"value": out}


def _willr(bars: Sequence[Bar], p: dict[str, float], _z: str) -> dict[str, Series]:
    values = _stochastic(bars, closes(bars), _int(p, "length"))
    return {"value": [None if v is None else v - 100 for v in values]}


def _roc(bars: Sequence[Bar], p: dict[str, float], _z: str) -> dict[str, Series]:
    length = _int(p, "length")
    source = closes(bars)
    out: Series = []
    for i, value in enumerate(source):
        before = source[i - length] if i >= length else None
        out.append(
            None if i < length or value is None or not before else 100 * (value - before) / before
        )
    return {"value": out}


def _ao(bars: Sequence[Bar], _p: dict[str, float], _z: str) -> dict[str, Series]:
    mid = _hl2(bars)
    return {"value": _minus(sma(mid, 5), sma(mid, 34))}


def _trix(bars: Sequence[Bar], p: dict[str, float], _z: str) -> dict[str, Series]:
    length = _int(p, "length")
    log: Series = [math.log(bar.close) if bar.close > 0 else None for bar in bars]
    triple = ema(ema(ema(log, length), length), length)
    return {"value": _times(_change(triple), 10_000)}


def _uo(bars: Sequence[Bar], p: dict[str, float], _z: str) -> dict[str, Series]:
    buying: Series = [
        None if i == 0 else bar.close - min(bar.low, bars[i - 1].close)
        for i, bar in enumerate(bars)
    ]
    ranges: Series = [
        None if i == 0 else max(bar.high, bars[i - 1].close) - min(bar.low, bars[i - 1].close)
        for i, bar in enumerate(bars)
    ]

    def average(length: int) -> Series:
        return _ratio(sma(buying, length), sma(ranges, length))

    fast, middle, slow = (average(_int(p, name)) for name in ("fast", "middle", "slow"))
    return {
        "value": [
            None if f is None or m is None or s is None else 100 * (4 * f + 2 * m + s) / 7
            for f, m, s in zip(fast, middle, slow, strict=True)
        ]
    }


def _aroon(bars: Sequence[Bar], p: dict[str, float], _z: str) -> dict[str, Series]:
    length = _int(p, "length")
    up: Series = []
    down: Series = []
    for i in range(len(bars)):
        if i < length:
            up.append(None)
            down.append(None)
            continue
        high_index = low_index = i
        for k in range(i - length, i + 1):
            if bars[k].high >= bars[high_index].high:
                high_index = k
            if bars[k].low <= bars[low_index].low:
                low_index = k
        up.append(100 * (length - (i - high_index)) / length)
        down.append(100 * (length - (i - low_index)) / length)
    return {"up": up, "down": down}


def _dmi(bars: Sequence[Bar], p: dict[str, float], _z: str) -> dict[str, Series]:
    length = _int(p, "length")
    plus_move: Series = [None]
    minus_move: Series = [None]
    for i in range(1, len(bars)):
        up = bars[i].high - bars[i - 1].high
        down = bars[i - 1].low - bars[i].low
        plus_move.append(up if up > down and up > 0 else 0.0)
        minus_move.append(down if down > up and down > 0 else 0.0)
    plus_move, minus_move = plus_move[: len(bars)], minus_move[: len(bars)]
    band = rma(_true_range(bars), length)
    plus_di = _ratio(rma(plus_move, length), band, 100)
    minus_di = _ratio(rma(minus_move, length), band, 100)
    dx = _combine(plus_di, minus_di, lambda a, b: 0.0 if a + b == 0 else 100 * abs(a - b) / (a + b))
    return {"plus": plus_di, "minus": minus_di, "adx": rma(dx, _int(p, "smoothing"))}


def _bbw(bars: Sequence[Bar], p: dict[str, float], _z: str) -> dict[str, Series]:
    length = _int(p, "length")
    basis = sma(closes(bars), length)
    band = _times(stdev(closes(bars), length), 2 * _num(p, "mult", 0.1, 10))
    return {"value": _ratio(band, basis)}


def _bbpb(bars: Sequence[Bar], p: dict[str, float], _z: str) -> dict[str, Series]:
    length = _int(p, "length")
    basis = sma(closes(bars), length)
    deviation = _times(stdev(closes(bars), length), _num(p, "mult", 0.1, 10))
    lower = _minus(basis, deviation)
    return {"value": _ratio(_minus(closes(bars), lower), _times(deviation, 2))}


def _hv(bars: Sequence[Bar], p: dict[str, float], _z: str) -> dict[str, Series]:
    returns: Series = [
        None
        if i == 0 or bars[i - 1].close <= 0 or bar.close <= 0
        else math.log(bar.close / bars[i - 1].close)
        for i, bar in enumerate(bars)
    ]
    periods = _num(p, "periods", 1, 200_000)
    return {"value": _times(stdev(returns, _int(p, "length")), 100 * math.sqrt(periods))}


def _mfi(bars: Sequence[Bar], p: dict[str, float], _z: str) -> dict[str, Series]:
    length = _int(p, "length")
    typical = [(bar.high + bar.low + bar.close) / 3 for bar in bars]
    positive: Series = []
    negative: Series = []
    for i, bar in enumerate(bars):
        if i == 0:
            positive.append(None)
            negative.append(None)
            continue
        positive.append(typical[i] * bar.volume if typical[i] > typical[i - 1] else 0.0)
        negative.append(typical[i] * bar.volume if typical[i] < typical[i - 1] else 0.0)
    return {
        "value": _combine(
            sma(positive, length),
            sma(negative, length),
            lambda a, b: None if a + b == 0 else 100 * a / (a + b),
        )
    }


def _cmf(bars: Sequence[Bar], p: dict[str, float], _z: str) -> dict[str, Series]:
    length = _int(p, "length")
    flow: Series = [
        0.0
        if bar.high == bar.low
        else ((bar.close - bar.low - (bar.high - bar.close)) / (bar.high - bar.low)) * bar.volume
        for bar in bars
    ]
    return {"value": _ratio(sma(flow, length), sma(_volumes(bars), length))}


def _volma(bars: Sequence[Bar], p: dict[str, float], _z: str) -> dict[str, Series]:
    volume = _volumes(bars)
    return {"volume": volume, "average": sma(volume, _int(p, "length"))}


INDICATORS.update(
    {
        "vwma": IndicatorSpec({"length": 20}, ("value",), _vwma),
        "hma": IndicatorSpec({"length": 9}, ("value",), _hma),
        "kc": IndicatorSpec({"length": 20, "mult": 2, "atr": 10}, ("basis", "upper", "lower"), _kc),
        "dc": IndicatorSpec({"length": 20}, ("upper", "basis", "lower"), _dc),
        "env": IndicatorSpec({"length": 20, "percent": 10}, ("basis", "upper", "lower"), _env),
        "psar": IndicatorSpec(
            {"start": 0.02, "increment": 0.02, "maximum": 0.2}, ("value",), _psar
        ),
        "supertrend": IndicatorSpec({"atr": 10, "factor": 3}, ("up", "down"), _supertrend),
        "ichimoku": IndicatorSpec(
            {"conversion": 9, "base": 26, "spanB": 52, "displacement": 26},
            ("conversion", "base", "spanA", "spanB"),
            _ichimoku,
        ),
        "stochrsi": IndicatorSpec({"k": 3, "d": 3, "rsi": 14, "stoch": 14}, ("k", "d"), _stochrsi),
        "cci": IndicatorSpec({"length": 20}, ("value",), _cci),
        "willr": IndicatorSpec({"length": 14}, ("value",), _willr),
        "mom": IndicatorSpec(
            {"length": 10},
            ("value",),
            lambda b, p, z: {"value": _change_n(closes(b), _int(p, "length"))},
        ),
        "roc": IndicatorSpec({"length": 9}, ("value",), _roc),
        "ao": IndicatorSpec({}, ("value",), _ao),
        "trix": IndicatorSpec({"length": 18}, ("value",), _trix),
        "uo": IndicatorSpec({"fast": 7, "middle": 14, "slow": 28}, ("value",), _uo),
        "aroon": IndicatorSpec({"length": 14}, ("up", "down"), _aroon),
        "dmi": IndicatorSpec({"length": 14, "smoothing": 14}, ("plus", "minus", "adx"), _dmi),
        "bbw": IndicatorSpec({"length": 20, "mult": 2}, ("value",), _bbw),
        "bbpb": IndicatorSpec({"length": 20, "mult": 2}, ("value",), _bbpb),
        "hv": IndicatorSpec({"length": 10, "periods": 252}, ("value",), _hv),
        "mfi": IndicatorSpec({"length": 14}, ("value",), _mfi),
        "cmf": IndicatorSpec({"length": 20}, ("value",), _cmf),
        "volma": IndicatorSpec({"length": 20}, ("volume", "average"), _volma),
    }
)


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
