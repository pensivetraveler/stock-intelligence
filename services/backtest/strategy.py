"""Strategy definitions: validation, fingerprints and the declarative rule evaluator.

A definition is plain JSON. `rules` definitions are evaluated here and never run code;
`python` definitions carry source code that only ever runs in the sandbox worker.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from services.backtest.indicators import INDICATORS, Bar, Series, compute
from services.custom_indicators.runner import IndicatorError, clean_params

COMPARISONS = ("cross_above", "cross_below", "gt", "gte", "lt", "lte")
PRICES = ("open", "high", "low", "close", "volume")
SIZING_MODES = ("percent_equity", "fixed_cash", "fixed_shares")
MAX_CODE_BYTES = 100_000
MAX_DEPTH = 6
MAX_CONDITIONS = 40
CUSTOM_TYPE = re.compile(r"^py:([A-Za-z0-9_-]{1,64})@([1-9][0-9]{0,8})$")

# A saved Python indicator version (ADR 0023) by ID and version; raises LookupError.
CustomLookup = Callable[[str, int], dict[str, Any]]


class StrategyError(ValueError):
    """A strategy definition is invalid."""


def canonical(definition: dict[str, Any]) -> str:
    return json.dumps(definition, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(definition: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(definition).encode()).hexdigest()


def _decimal(value: object, what: str, low: Decimal, high: Decimal) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise StrategyError(f"{what} must be a number")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise StrategyError(f"{what} must be a number") from exc
    if not number.is_finite() or not low <= number <= high:
        raise StrategyError(f"{what} must be between {low} and {high}")
    return format(number.normalize(), "f")


def parse_custom(indicator: str) -> tuple[str, int] | None:
    """`py:<id>@<version>` -> (id, version); None for a built-in indicator ID."""
    match = CUSTOM_TYPE.fullmatch(indicator)
    return None if match is None else (match.group(1), int(match.group(2)))


def _custom_operand(value: dict[str, Any], custom: CustomLookup | None) -> dict[str, Any]:
    indicator = str(value["indicator"])
    parsed = parse_custom(indicator)
    if parsed is None:
        raise StrategyError(f"{indicator!r} must look like py:<indicator_id>@<version>")
    if custom is None:
        raise StrategyError("Python indicators need the indicator store (set SIW_DATA_DIR)")
    try:
        saved = custom(*parsed)
    except LookupError as exc:
        raise StrategyError(f"{indicator} is not a saved Python indicator version") from exc
    params = value.get("params", {})
    if not isinstance(params, dict):
        raise StrategyError("indicator params must be an object")
    output = value.get("output", saved["outputs"][0])
    if output not in saved["outputs"]:
        raise StrategyError(f"{indicator} has no output {output!r}")
    extra = set(value) - {"indicator", "params", "output"}
    if extra:
        raise StrategyError(f"unexpected operand fields: {', '.join(sorted(extra))}")
    try:
        clean_params(saved, params)
    except IndicatorError as exc:
        raise StrategyError(f"{indicator}: {exc}") from exc
    return {"indicator": indicator, "params": dict(params), "output": output}


def _operand(value: object, custom: CustomLookup | None = None) -> dict[str, Any]:
    if not isinstance(value, dict) or len(value) == 0:
        raise StrategyError("an operand must be an indicator, a price or a value")
    if "indicator" in value and str(value.get("indicator")).startswith("py:"):
        return _custom_operand(value, custom)
    if "indicator" in value:
        indicator_id = value.get("indicator")
        spec = INDICATORS.get(str(indicator_id))
        if spec is None:
            raise StrategyError(f"unknown indicator {indicator_id!r}")
        params = value.get("params", {})
        if not isinstance(params, dict):
            raise StrategyError("indicator params must be an object")
        output = value.get("output", spec.outputs[0])
        if output not in spec.outputs:
            raise StrategyError(f"{indicator_id} has no output {output!r}")
        extra = set(value) - {"indicator", "params", "output"}
        if extra:
            raise StrategyError(f"unexpected operand fields: {', '.join(sorted(extra))}")
        clean: dict[str, float] = {}
        for name, number in params.items():
            if name not in spec.params:
                raise StrategyError(f"{indicator_id} does not take {name}")
            if isinstance(number, bool) or not isinstance(number, (int, float)):
                raise StrategyError(f"{indicator_id} {name} must be a number")
            clean[name] = number
        try:
            compute(str(indicator_id), [], clean)  # checks the parameter ranges
        except ValueError as exc:
            raise StrategyError(str(exc)) from exc
        return {"indicator": indicator_id, "params": clean, "output": output}
    if set(value) == {"price"}:
        if value["price"] not in PRICES:
            raise StrategyError(f"price must be one of {', '.join(PRICES)}")
        return {"price": value["price"]}
    if set(value) == {"value"}:
        return {"value": _decimal(value["value"], "value", Decimal("-1e12"), Decimal("1e12"))}
    raise StrategyError("an operand must be an indicator, a price or a value")


def _condition(
    value: object, depth: int, counter: list[int], custom: CustomLookup | None
) -> dict[str, Any]:
    counter[0] += 1
    if counter[0] > MAX_CONDITIONS:
        raise StrategyError(f"a rule may hold at most {MAX_CONDITIONS} conditions")
    if depth > MAX_DEPTH:
        raise StrategyError("conditions are nested too deeply")
    if not isinstance(value, dict):
        raise StrategyError("a condition must be an object")
    for group in ("all", "any"):
        if group in value:
            items = value[group]
            if set(value) != {group} or not isinstance(items, list) or not items:
                raise StrategyError(f"{group!r} must be the only field and a non-empty list")
            return {group: [_condition(item, depth + 1, counter, custom) for item in items]}
    if set(value) != {"op", "left", "right"} or value["op"] not in COMPARISONS:
        raise StrategyError(f"a comparison needs op ({', '.join(COMPARISONS)}), left and right")
    return {
        "op": value["op"],
        "left": _operand(value["left"], custom),
        "right": _operand(value["right"], custom),
    }


def _risk(definition: dict[str, Any], clean: dict[str, Any]) -> None:
    for name in ("stop_loss_pct", "take_profit_pct"):
        value = definition.get(name)
        clean[name] = (
            None if value is None else _decimal(value, name, Decimal("0.01"), Decimal("1000"))
        )
    limit = definition.get("max_positions", 10)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise StrategyError("max_positions must be a whole number from 1 to 100")
    clean["max_positions"] = limit


def validate_definition(definition: object, custom: CustomLookup | None = None) -> dict[str, Any]:
    """Return the normalized definition, or raise StrategyError.

    `custom` resolves `py:<id>@<version>` operands; without it they are refused.
    """
    if not isinstance(definition, dict):
        raise StrategyError("a strategy definition must be an object")
    kind = definition.get("type")
    clean: dict[str, Any] = {"type": kind}
    if kind == "rules":
        allowed = {"type", "entry", "exit", "stop_loss_pct", "take_profit_pct"}
        allowed |= {"sizing", "max_positions"}
        extra = set(definition) - allowed
        if extra:
            raise StrategyError(f"unexpected fields: {', '.join(sorted(extra))}")
        counter = [0]
        clean["entry"] = _condition(definition.get("entry"), 0, counter, custom)
        exit_rule = definition.get("exit")
        clean["exit"] = None if exit_rule is None else _condition(exit_rule, 0, counter, custom)
        sizing = definition.get("sizing", {"mode": "percent_equity", "value": 10})
        if not isinstance(sizing, dict) or sizing.get("mode") not in SIZING_MODES:
            raise StrategyError(f"sizing mode must be one of {', '.join(SIZING_MODES)}")
        if set(sizing) != {"mode", "value"}:
            raise StrategyError("sizing needs exactly mode and value")
        mode = sizing["mode"]
        high = Decimal("100") if mode == "percent_equity" else Decimal("1e12")
        amount = _decimal(sizing["value"], "sizing value", Decimal("0.0001"), high)
        if mode == "fixed_shares" and Decimal(amount) != Decimal(amount).to_integral_value():
            raise StrategyError("fixed_shares must be a whole number")
        clean["sizing"] = {"mode": mode, "value": amount}
        _risk(definition, clean)
    elif kind == "python":
        extra = set(definition) - {"type", "code", "params", "stop_loss_pct", "take_profit_pct"}
        extra -= {"max_positions"}
        if extra:
            raise StrategyError(f"unexpected fields: {', '.join(sorted(extra))}")
        code = definition.get("code")
        if not isinstance(code, str) or not code.strip():
            raise StrategyError("a Python strategy needs code")
        if len(code.encode()) > MAX_CODE_BYTES:
            raise StrategyError(f"code must be at most {MAX_CODE_BYTES} bytes")
        try:
            compile(code, "<strategy>", "exec")
        except SyntaxError as exc:
            raise StrategyError(f"code does not compile: line {exc.lineno}: {exc.msg}") from exc
        params = definition.get("params", {})
        if not isinstance(params, dict) or len(canonical(params)) > 10_000:
            raise StrategyError("params must be a small JSON object")
        clean["code"] = code
        clean["params"] = params
        _risk(definition, clean)
    else:
        raise StrategyError("type must be rules or python")
    return clean


@dataclass
class RuleEvaluator:
    """Evaluates a rules definition over one instrument's bars, one index at a time.

    Indicator series are computed once over all bars; they are causal (checked by tests),
    so the value at index i depends only on bars 0..i. A missing value makes a condition
    false. Python indicator series (`py:` operands) are not computed here: the caller puts
    them in `custom_series`, keyed by `canonical(operand)`.
    """

    definition: dict[str, Any]
    bars: Sequence[Bar]
    time_zone: str
    custom_series: dict[str, Series] = field(default_factory=dict)
    _cache: dict[str, Series] = field(default_factory=dict)

    def _series(self, operand: dict[str, Any]) -> Series | None:
        if "indicator" not in operand:
            return None
        key = canonical(operand)
        if key in self.custom_series:
            return self.custom_series[key]
        if parse_custom(operand["indicator"]) is not None:
            raise StrategyError(f"{operand['indicator']} was not computed for this run")
        if key not in self._cache:
            outputs = compute(operand["indicator"], self.bars, operand["params"], self.time_zone)
            self._cache[key] = outputs[operand["output"]]
        return self._cache[key]

    def _value(self, operand: dict[str, Any], i: int) -> float | None:
        if i < 0:
            return None
        series = self._series(operand)
        if series is not None:
            return series[i]
        if "price" in operand:
            return float(getattr(self.bars[i], operand["price"]))
        return float(operand["value"])

    def holds(self, condition: dict[str, Any], i: int) -> bool:
        if "all" in condition:
            return all(self.holds(item, i) for item in condition["all"])
        if "any" in condition:
            return any(self.holds(item, i) for item in condition["any"])
        left, right = self._value(condition["left"], i), self._value(condition["right"], i)
        if left is None or right is None:
            return False
        op = condition["op"]
        if op in ("cross_above", "cross_below"):
            before_left = self._value(condition["left"], i - 1)
            before_right = self._value(condition["right"], i - 1)
            if before_left is None or before_right is None:
                return False
            if op == "cross_above":
                return before_left <= before_right and left > right
            return before_left >= before_right and left < right
        return {
            "gt": left > right,
            "gte": left >= right,
            "lt": left < right,
            "lte": left <= right,
        }[op]
