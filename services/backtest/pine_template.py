"""A rules strategy written out as a Pine v6 strategy, to compare on TradingView (ADR 0056 ⑤).

Code, not a model: each rule is turned into the Pine expression the engine's formula matches
(`services.backtest.indicators`; sma/ema/wma on the close, RSI and ATR with Wilder's
average, Donchian on highs and lows, CCI and MFI on the typical price ...). What has no
Pine equivalent written here is listed in `unsupported` and becomes `false` in the source,
so the template never trades on a guess.

The template is for a side-by-side check, not a copy of the engine:
- Orders fill at the next bar's open in both. A protective exit placed by Pine applies from
  the bar after the fill; the engine also checks the fill bar. Pine decides by its own rule
  which of a stop and a target in one bar came first; the engine takes the stop.
- `max_positions` limits the engine's whole portfolio; one Pine chart holds one symbol.
- Costs (fee rate, slippage) belong to a run, not to the strategy: set them in the
  strategy's Properties to match the run being compared.
"""

from __future__ import annotations

import re
from typing import Any

TEMPLATE_VERSION = 1

_PRICES = {"open", "high", "low", "close", "volume"}
_OPS = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


def _num(value: Any) -> str:
    text = format(float(value), "g") if not isinstance(value, str) else value
    return text if re.fullmatch(r"-?\d+(\.\d+)?(e-?\d+)?", text) else str(float(text))


def _int(params: dict[str, Any], name: str, default: int) -> int:
    return int(params.get(name, default))


# indicator -> function(params) -> {output: pine expression}. The defaults are the engine's.
def _single(fn: str, param: str, default: int, source: str = "close") -> Any:
    return lambda p: {"value": f"{fn}({source}, {_int(p, param, default)})"}


def _macd(p: dict[str, Any]) -> dict[str, str]:
    fast, slow, signal = _int(p, "fast", 12), _int(p, "slow", 26), _int(p, "signal", 9)
    macd = f"(ta.ema(close, {fast}) - ta.ema(close, {slow}))"
    sig = f"ta.ema({macd}, {signal})"
    return {"macd": macd, "signal": sig, "histogram": f"({macd} - {sig})"}


def _bb(p: dict[str, Any]) -> dict[str, str]:
    length, mult = _int(p, "length", 20), _num(p.get("mult", 2))
    basis = f"ta.sma(close, {length})"
    dev = f"{mult} * ta.stdev(close, {length})"  # population, as the engine's
    return {"basis": basis, "upper": f"({basis} + {dev})", "lower": f"({basis} - {dev})"}


def _stoch(p: dict[str, Any]) -> dict[str, str]:
    k = f"ta.sma(ta.stoch(close, high, low, {_int(p, 'k', 14)}), {_int(p, 'smoothK', 1)})"
    return {"k": k, "d": f"ta.sma({k}, {_int(p, 'd', 3)})"}


def _kc(p: dict[str, Any]) -> dict[str, str]:
    basis = f"ta.ema(close, {_int(p, 'length', 20)})"
    band = f"{_num(p.get('mult', 2))} * ta.atr({_int(p, 'atr', 10)})"
    return {"basis": basis, "upper": f"({basis} + {band})", "lower": f"({basis} - {band})"}


def _dc(p: dict[str, Any]) -> dict[str, str]:
    length = _int(p, "length", 20)
    upper, lower = f"ta.highest(high, {length})", f"ta.lowest(low, {length})"
    return {"upper": upper, "lower": lower, "basis": f"(({upper} + {lower}) / 2)"}


def _env(p: dict[str, Any]) -> dict[str, str]:
    basis = f"ta.sma(close, {_int(p, 'length', 20)})"
    pct = _num(p.get("percent", 10))
    return {
        "basis": basis,
        "upper": f"({basis} * (1 + {pct} / 100))",
        "lower": f"({basis} * (1 - {pct} / 100))",
    }


def _dmi(p: dict[str, Any]) -> dict[str, str]:
    # ta.dmi returns a tuple: the writer declares it once and names its parts.
    length, smoothing = _int(p, "length", 14), _int(p, "smoothing", 14)
    tag = f"{length}_{smoothing}"
    return {
        "__tuple__": f"[dmiPlus{tag}, dmiMinus{tag}, adx{tag}] = ta.dmi({length}, {smoothing})",
        "plus": f"dmiPlus{tag}",
        "minus": f"dmiMinus{tag}",
        "adx": f"adx{tag}",
    }


PINE: dict[str, Any] = {
    "sma": _single("ta.sma", "length", 9),
    "ema": _single("ta.ema", "length", 9),
    "wma": _single("ta.wma", "length", 9),
    "rsi": _single("ta.rsi", "length", 14),
    "atr": lambda p: {"value": f"ta.atr({_int(p, 'length', 14)})"},
    "macd": _macd,
    "bb": _bb,
    "stoch": _stoch,
    "vwap": lambda p: {"value": "ta.vwap(hlc3)"},
    "obv": lambda p: {"value": "ta.obv"},
    "vwma": _single("ta.vwma", "length", 20),
    "hma": _single("ta.hma", "length", 9),
    "kc": _kc,
    "dc": _dc,
    "env": _env,
    "psar": lambda p: {
        "value": f"ta.sar({_num(p.get('start', 0.02))}, {_num(p.get('increment', 0.02))}, "
        f"{_num(p.get('maximum', 0.2))})"
    },
    "cci": _single("ta.cci", "length", 20, "hlc3"),
    "willr": lambda p: {"value": f"ta.wpr({_int(p, 'length', 14)})"},
    "mom": _single("ta.mom", "length", 10),
    "roc": _single("ta.roc", "length", 9),
    "ao": lambda p: {"value": "(ta.sma(hl2, 5) - ta.sma(hl2, 34))"},
    "dmi": _dmi,
    "mfi": _single("ta.mfi", "length", 14, "hlc3"),
    "volma": lambda p: {"volume": "volume", "average": f"ta.sma(volume, {_int(p, 'length', 20)})"},
}


class _Writer:
    def __init__(self) -> None:
        self.unsupported: list[str] = []
        self.lines: list[str] = []  # indicator definitions, each once
        self._names: dict[str, str] = {}

    def series(self, expression: str) -> str:
        """A named series for an indicator expression (Pine computes it on every bar)."""
        if expression not in self._names:
            name = f"s{len(self._names) + 1}"
            self._names[expression] = name
            self.lines.append(f"{name} = {expression}")
        return self._names[expression]

    def operand(self, operand: dict[str, Any], where: str) -> str | None:
        if "value" in operand:
            return _num(operand["value"])
        if "price" in operand:
            expression = str(operand["price"]) if operand["price"] in _PRICES else None
        else:
            ident = str(operand.get("indicator"))
            builder = PINE.get(ident)
            if builder is None:
                self.unsupported.append(f"{where}: indicator {ident} has no Pine form here")
                return None
            outputs = dict(builder(operand.get("params") or {}))
            declared = outputs.pop("__tuple__", None)
            if declared is not None and declared not in self.lines:
                self.lines.append(declared)
            output = operand.get("output") or next(iter(outputs))
            if output not in outputs:
                expression = None
            elif declared is not None:
                expression = outputs[output]
            else:
                expression = self.series(outputs[output])
        if expression is None:
            self.unsupported.append(f"{where}: operand {operand} has no Pine form")
            return None
        offset = int(operand.get("offset") or 0)
        return f"{expression}[{offset}]" if offset else expression

    def condition(self, condition: dict[str, Any] | None, where: str) -> str:
        if condition is None:
            return "false"
        for group, joiner in (("all", " and "), ("any", " or ")):
            if group in condition:
                parts = [
                    self.condition(item, f"{where}/{group}/{n}")
                    for n, item in enumerate(condition[group])
                ]
                return "(" + joiner.join(parts) + ")"
        if "time" in condition:
            start = condition["time"]["from"].replace(":", "")
            end = condition["time"]["to"].replace(":", "")
            return f'not na(time(timeframe.period, "{start}-{end}", syminfo.timezone))'
        left = self.operand(condition["left"], f"{where}/left")
        right = self.operand(condition["right"], f"{where}/right")
        if left is None or right is None:
            return "false"
        op = condition["op"]
        if op == "cross_above":
            return f"ta.crossover({left}, {right})"
        if op == "cross_below":
            return f"ta.crossunder({left}, {right})"
        return f"({left} {_OPS[op]} {right})"


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "").replace('"', "'").replace("\n", " ")[:80] + '"'


def _order_lines(order: dict[str, Any] | None, kind: str) -> tuple[str, list[str]]:
    """(the price argument, extra lines) for a limit or stop order at an offset from the
    signal close; ("", []) for a market order."""
    if not order or order.get("type", "market") == "market":
        return "", []
    pct = _num(order["offset_pct"])
    return f"{order['type']}=close * (1 + {pct} / 100)", [str(order.get("expire_bars", 1))]


def to_pine(definition: dict[str, Any], name: str) -> dict[str, Any]:
    """{"source", "unsupported", "version"} for a validated rules definition."""
    if definition.get("type") != "rules":
        return {"source": "", "unsupported": ["only rules strategies"], "version": TEMPLATE_VERSION}
    w = _Writer()
    entry = w.condition(definition.get("entry"), "/entry")
    exit_ = w.condition(definition.get("exit"), "/exit")
    sizing = definition.get("sizing") or {"mode": "percent_equity", "value": "10"}
    qty_type = {
        "percent_equity": "strategy.percent_of_equity",
        "fixed_cash": "strategy.cash",
        "fixed_shares": "strategy.fixed",
    }[sizing["mode"]]
    out = [
        "//@version=6",
        f"// SIW Research rules strategy, Pine template v{TEMPLATE_VERSION} (ADR 0056).",
        "// For a side-by-side check on TradingView; set fees and slippage to the run's.",
        f"// max_positions {definition.get('max_positions', 10)} is the engine's portfolio "
        "limit; this chart holds one symbol.",
        f"strategy({_quote(name)}, overlay=true, pyramiding=1, "
        f"default_qty_type={qty_type}, default_qty_value={_num(sizing['value'])}, "
        "process_orders_on_close=false)",
        "",
        *w.lines,
        "",
        f"entrySignal = {entry}",
        f"exitSignal = {exit_}",
        "flat = strategy.position_size == 0",
        "",
    ]
    price, expiry = _order_lines(definition.get("entry_order"), "entry")
    if not price:
        out.append('if entrySignal and flat\n    strategy.entry("L", strategy.long)')
    else:
        out += [
            "var int entryAt = na",
            "if entrySignal and flat and na(entryAt)",
            f'    strategy.entry("L", strategy.long, {price})',
            "    entryAt := bar_index",
            f"if not na(entryAt) and (not flat or bar_index - entryAt >= {expiry[0]})",
            '    if flat\n        strategy.cancel("L")',
            "    entryAt := na",
        ]
    price, expiry = _order_lines(definition.get("exit_order"), "exit")
    if not price:
        out.append('if exitSignal and not flat\n    strategy.close("L")')
    else:
        out += [
            "var int exitAt = na",
            "if exitSignal and not flat and na(exitAt)",
            f'    strategy.exit("XO", "L", {price})',
            "    exitAt := bar_index",
            f"if not na(exitAt) and (flat or bar_index - exitAt >= {expiry[0]})",
            '    strategy.cancel("XO")',
            "    exitAt := na",
        ]
    stop_pct, target_pct = definition.get("stop_loss_pct"), definition.get("take_profit_pct")
    atr_stop, trailing = definition.get("stop_loss_atr"), definition.get("trailing_stop")
    if any(v is not None for v in (stop_pct, target_pct, atr_stop, trailing)):
        out += ["", "// Protective exits, from the fill price (strategy.position_avg_price)."]
        out.append("justFilled = not flat and strategy.position_size[1] == 0")
        stops: list[str] = []
        if stop_pct is not None:
            stops.append(f"strategy.position_avg_price * (1 - {_num(stop_pct)} / 100)")
        if atr_stop is not None:
            # The engine reads the ATR of the bar before the fill.
            # ta.* calls stay at the top level: Pine needs them on every bar.
            out += [
                f"stopAtr = ta.atr({int(atr_stop['length'])})",
                "var float entryAtr = na",
                "if justFilled\n    entryAtr := stopAtr[1]",
            ]
            stops.append(f"strategy.position_avg_price - {_num(atr_stop['mult'])} * entryAtr")
        if trailing is not None:
            if trailing["mode"] == "pct":
                level = f"trailHigh * (1 - {_num(trailing['value'])} / 100)"
            else:
                out.append(f"trailAtr = ta.atr({int(trailing['length'])})")
                level = f"trailHigh - {_num(trailing['mult'])} * trailAtr"
            out += [
                "var float trailHigh = na",
                "var float trailStop = na",
                "if justFilled",
                "    trailHigh := strategy.position_avg_price",
                "    trailStop := na",
                "if not flat",
                "    trailHigh := math.max(trailHigh, high)",
                f"    trailStop := math.max(nz(trailStop, {level}), {level})",
            ]
            stops.append("trailStop")
        stop = (
            "na"
            if not stops
            else (stops[0] if len(stops) == 1 else f"math.max({', '.join(stops)})")
        )
        limit = (
            "na"
            if target_pct is None
            else f"strategy.position_avg_price * (1 + {_num(target_pct)} / 100)"
        )
        out += [
            "if not flat",
            f'    strategy.exit("P", "L", stop={stop}, limit={limit})',
        ]
    if w.unsupported:
        out = [
            *out[:2],
            "// NOT CONVERTED (written as false, the template does not trade on them):",
            *(f"//   {item}" for item in w.unsupported),
            *out[2:],
        ]
    return {"source": "\n".join(out) + "\n", "unsupported": w.unsupported,
            "version": TEMPLATE_VERSION}  # fmt: skip
