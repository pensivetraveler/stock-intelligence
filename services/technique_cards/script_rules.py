"""Card condition slots -> rules, by code (ADR 0056 L3, script first; user decision 2026-11-15).

A condition whose slots say exactly what to compare (the close against the 20-day Donchian
band, RSI against 30, a 2 % stop) becomes a rule here, without a model. The draft then fixes
that rule: a model, when one is asked for the other items, must use it unchanged. Anything the
slots cannot settle is left to the model or marked unconvertible: no slot means no rule.

Some conversions rest on a reading the source did not state, and saving needs the user's tick
for them: a length the card leaves blank takes the engine's default (sma 9, not the 20 a
lesson usually means), "ma" is read as a simple average, and a "both sides" item keeps only
the long side (the engine is long only).

Measured on 2026-11-15 over 18 cards (132 conditions): 23 converted exactly, 20 on an
assumption, 2 "not used", 87 left (77 written only as text).
"""

from __future__ import annotations

import re
from typing import Any

from services.backtest.indicators import INDICATORS
from services.backtest.strategy import StrategyError, validate_definition

SCRIPT_MODEL = "script"

PRICES = {"close", "open", "high", "low", "volume"}
PREVIOUS = {"previous_close": "close", "previous_high": "high", "previous_low": "low"}
# Card indicator name -> (engine indicator, output, the length parameter or None).
INDICATOR_SLOTS: dict[str, tuple[str, str, str | None]] = {
    "sma": ("sma", "value", "length"),
    "ema": ("ema", "value", "length"),
    "wma": ("wma", "value", "length"),
    "rsi": ("rsi", "value", "length"),
    "atr": ("atr", "value", "length"),
    "macd": ("macd", "macd", None),
    "macd_signal": ("macd", "signal", None),
    "macd_histogram": ("macd", "histogram", None),
    "bollinger_upper": ("bb", "upper", "length"),
    "bollinger_middle": ("bb", "basis", "length"),
    "bollinger_lower": ("bb", "lower", "length"),
    "bollinger_width": ("bbw", "value", "length"),
    "adx": ("dmi", "adx", "length"),
    "plus_di": ("dmi", "plus", "length"),
    "minus_di": ("dmi", "minus", "length"),
    "stochastic_k": ("stoch", "k", "k"),
    "stochastic_d": ("stoch", "d", "k"),
    "vwap": ("vwap", "value", None),
    "obv": ("obv", "value", None),
    "volume_average": ("volma", "average", "length"),
}
OPS = {
    "cross_above": "cross_above",
    "cross_below": "cross_below",
    "above": "gt",
    "below": "lt",
    "at_or_above": "gte",
    "at_or_below": "lte",
}
_ANY_ENTRY = {"op": "gt", "left": {"price": "close"}, "right": {"value": "0"}}


# The text says when NOT to act ("상단에서 떨어져도 중심선 위면 팔지 않는다"): the slots then
# read as the very act it forbids (close crosses below the upper band = an exit), so the script
# leaves it (card 1b7a80be v2, 2026-11-28).
_NEGATED = re.compile(
    r"지\s*(않|말)|안\s*(사|산|팔|판)|\bdo(?:es)?\s+not\b|\bdon'?t\b|\bnever\b", re.I
)


class NotScriptable(Exception):
    """The slots do not settle a rule; the message says why (English, shown as the reason)."""


def _operand(slot: dict[str, Any], assumptions: list[dict[str, Any]]) -> dict[str, Any]:
    operand = _unscaled(slot, assumptions)
    number = slot.get("value")
    if slot.get("kind") not in ("indicator", "price") or number is None:
        return operand
    if operand.get("indicator") in ("bb", "bbw"):
        # On a band the number is its width in standard deviations (1b7a80be: upper, 2).
        return {**operand, "params": {**operand["params"], "mult": number}}
    # "1.3 times the 20-day average volume", "0.65 of the 52-week high" (841c304b): the
    # number scales the operand. Dropping it read "1.3배 이상" as "at or above the average".
    return {**operand, "mult": str(number)}


def _unscaled(slot: dict[str, Any], assumptions: list[dict[str, Any]]) -> dict[str, Any]:
    kind, name, period = slot.get("kind"), slot.get("name"), slot.get("period")
    if name == "other":
        # The extractor found no slot for it (a Fibonacci ratio landed in a value slot as
        # "low >= 0.5", 원펀치매매 2권 2026-11-16): only the text says what it is.
        raise NotScriptable("a slot named 'other' needs its text read")
    if kind == "price":
        if name in PRICES:
            return {"price": name}
        if name in PREVIOUS:
            return {"price": PREVIOUS[name], "offset": 1}
        if name in ("highest_high", "lowest_low"):
            if not period:
                raise NotScriptable("the highest high or lowest low has no length")
            output = "upper" if name == "highest_high" else "lower"
            return {"indicator": "dc", "params": {"length": int(period)}, "output": output,
                    "offset": 1}  # fmt: skip
        raise NotScriptable(f"the price '{name}' has no rule form")
    if kind == "indicator":
        if name == "ma":
            assumptions.append({"kind": "ma_as_sma"})
            name = "sma"
        if name not in INDICATOR_SLOTS:
            raise NotScriptable(f"the indicator '{name}' is not in the engine")
        ident, output, param = INDICATOR_SLOTS[name]
        params: dict[str, Any] = {}
        if param:
            if period:
                params[param] = int(period)
            else:
                # Written out, so the saved rule shows the number the backtest used.
                default = INDICATORS[ident].params[param]
                params[param] = default
                assumptions.append(
                    {"kind": "default_length", "indicator": ident, "param": param,
                     "value": default}
                )  # fmt: skip
        return {"indicator": ident, "params": params, "output": output}
    if kind == "value":
        if slot.get("value") is None:
            raise NotScriptable("the value is blank")
        return {"value": str(slot["value"])}
    raise NotScriptable(f"a '{kind}' operand has no rule form")


def _normal_condition(rule: dict[str, Any]) -> dict[str, Any]:
    try:
        normal: dict[str, Any] = validate_definition({"type": "rules", "entry": rule})["entry"]
    except StrategyError as exc:
        raise NotScriptable(f"the rule is invalid: {exc}") from exc
    return normal


def _normal_field(name: str, value: Any) -> Any:
    try:
        return validate_definition({"type": "rules", "entry": _ANY_ENTRY, name: value})[name]
    except StrategyError as exc:
        raise NotScriptable(f"the {name} is invalid: {exc}") from exc


def _place(condition: dict[str, Any], assumptions: list[dict[str, Any]]) -> dict[str, Any]:
    field, comparator = condition.get("field"), condition.get("comparator")
    left, right = condition.get("left") or {}, condition.get("right") or {}
    if comparator == "none":
        # "No stop" / "no target": the field stays unset. An exit item saying "none" is read as
        # no profit target; if it meant no exit at all, the target is unset all the same.
        if field == "stop_loss":
            return {"kind": "absent", "pointer": "/stop_loss_pct"}
        if field == "exit":
            return {"kind": "absent", "pointer": "/take_profit_pct"}
        raise NotScriptable("an entry saying nothing is used has no rule form")
    if field in ("stop_loss", "exit") and right.get("kind") in ("percent", "atr_multiple"):
        value = right.get("value")
        if value is None:
            raise NotScriptable("the stop or target has no number")
        if right["kind"] == "percent":
            if field == "stop_loss":
                name = "stop_loss_pct"
            elif comparator in ("above", "at_or_above", "touch", "rises"):
                name = "take_profit_pct"
            else:
                raise NotScriptable("a percent exit that is not a target needs its text read")
            return {"kind": "field", "name": name, "value": _normal_field(name, str(value))}
        if field != "stop_loss":
            raise NotScriptable("the engine has an ATR stop, not an ATR target")
        length = right.get("period")
        if not length:
            length = INDICATORS["atr"].params["length"]
            assumptions.append(
                {"kind": "default_length", "indicator": "atr", "param": "length", "value": length}
            )
        stop = {"length": int(length), "mult": str(value)}
        return {"kind": "field", "name": "stop_loss_atr",
                "value": _normal_field("stop_loss_atr", stop)}  # fmt: skip
    if (
        right.get("kind") == "percent"
        and right.get("name") == "previous_close"
        and left.get("kind") == "price"
        and left.get("name") == "close"
        and comparator in ("above", "at_or_above")
    ):
        # "The close N % or more above the previous close" is the 1-bar rate of change.
        if right.get("value") is None:
            raise NotScriptable("the percent is blank")
        rule = {"op": OPS[comparator], "left": {"indicator": "roc", "params": {"length": 1},
                "output": "value"}, "right": {"value": str(right["value"])}}  # fmt: skip
        group = "entry" if field == "entry" else "exit"
        return {"kind": group, "rule": _normal_condition(rule)}
    if right.get("kind") in ("percent", "range_multiple", "atr_multiple", "bars"):
        raise NotScriptable(f"a '{right['kind']}' comparison is a distance, not a rule operand")
    if comparator in ("rises", "falls"):
        operand = _operand(left, assumptions)
        previous = {**operand, "offset": int(operand.get("offset", 0)) + 1}
        rule = {"op": "gt" if comparator == "rises" else "lt", "left": operand, "right": previous}
    elif comparator in OPS:
        rule = {"op": OPS[comparator], "left": _operand(left, assumptions),
                "right": _operand(right, assumptions)}  # fmt: skip
    else:
        raise NotScriptable(f"the comparison '{comparator}' has no rule form")
    group = "entry" if field == "entry" else "exit"
    return {"kind": group, "rule": _normal_condition(rule)}


def base_timeframe(conditions: list[dict[str, Any]]) -> str:
    """The timeframe most conditions name; a condition on another one is not scripted."""
    counts: dict[str, int] = {}
    for condition in conditions:
        timeframe = str(condition.get("timeframe") or "").strip()
        if timeframe:
            counts[timeframe] = counts.get(timeframe, 0) + 1
    return max(counts, key=lambda key: counts[key]) if counts else ""


def convert(condition: dict[str, Any], base: str) -> dict[str, Any]:
    """{"place": ... or None, "assumptions": [...], "reason": why not}. The place is one of
    {"kind": "entry"|"exit", "rule"}, {"kind": "field", "name", "value"} or
    {"kind": "absent", "pointer"}."""
    assumptions: list[dict[str, Any]] = []
    side = condition.get("side")
    timeframe = str(condition.get("timeframe") or "").strip()
    try:
        if side == "short":
            raise NotScriptable("the engine is long only")
        if timeframe and base and timeframe != base:
            raise NotScriptable("another timeframe than the card's")
        place = _place(condition, assumptions)
        # "목표가는 두지 않는다" is still a plain absent field; only a rule turns the meaning over.
        if place["kind"] in ("entry", "exit") and _NEGATED.search(str(condition.get("text") or "")):
            raise NotScriptable("the text says when not to act; the slots read as when to act")
    except NotScriptable as why:
        return {"place": None, "assumptions": [], "reason": str(why)}
    if side == "both":
        assumptions.append({"kind": "long_only"})
    return {"place": place, "assumptions": assumptions, "reason": ""}


def assemble(places: dict[int, dict[str, Any]]) -> tuple[dict[str, Any], dict[int, list[str]]]:
    """A whole definition from scripted items alone, and each item's pointers.

    Entry conditions must all hold; exit conditions are alternatives (any one sells), as a
    card lists each way out separately. Raises NotScriptable when the items clash."""
    entry = [i for i, p in places.items() if p["kind"] == "entry"]
    exits = [i for i, p in places.items() if p["kind"] == "exit"]
    if not entry:
        raise NotScriptable("no entry condition was scripted")
    definition: dict[str, Any] = {
        "type": "rules",
        "entry": {"all": [places[i]["rule"] for i in entry]},
    }
    paths: dict[int, list[str]] = {i: [f"/entry/all/{n}"] for n, i in enumerate(entry)}
    if exits:
        definition["exit"] = {"any": [places[i]["rule"] for i in exits]}
        paths |= {i: [f"/exit/any/{n}"] for n, i in enumerate(exits)}
    for index, place in places.items():
        if place["kind"] == "field":
            if place["name"] in definition:
                raise NotScriptable(f"two items set {place['name']}")
            definition[place["name"]] = place["value"]
            paths[index] = [f"/{place['name']}"]
        elif place["kind"] == "absent":
            paths[index] = [place["pointer"]]
    try:
        return validate_definition(definition), paths
    except StrategyError as exc:
        raise NotScriptable(f"the scripted items do not fit together: {exc}") from exc


def _at(definition: dict[str, Any], pointer: str) -> Any:
    node: Any = definition
    for part in pointer[1:].split("/"):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return None
    return node


def kept(
    definition: dict[str, Any] | None, place: dict[str, Any], entry: dict[str, Any] | None
) -> bool:
    """Whether the model's answer used a scripted item as fixed."""
    if entry is None or definition is None:
        return False
    if place["kind"] == "absent":
        return entry["status"] == "absent" and place["pointer"] in entry["rule_paths"]
    if entry["status"] != "mapped":
        return False
    want = place["rule"] if place["kind"] in ("entry", "exit") else place["value"]
    roots = ["/" + (place["name"] if place["kind"] == "field" else place["kind"])]
    if place["kind"] == "entry" and "setup" in definition:
        roots = ["/setup/base", "/setup/breakout"]  # a setup's entry conditions (ADR 0061)
    return any(
        pointer.startswith(tuple(roots)) and _at(definition, pointer) == want
        for pointer in entry["rule_paths"]
    )
