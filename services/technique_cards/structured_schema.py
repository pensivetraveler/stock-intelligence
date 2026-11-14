"""Structured technique card (card schema v2, user decision 2026-11-14 "(b) 구조화 출력").

Free-text cards (`card_schema`, v1) could not be scored: matching words could not tell a
condition said in one shorter sentence from one left out (#SIW-lib-14). Here every entry / exit /
stop-loss condition has slots — what is compared, how, with what, on which timeframe — so the
script compares slot by slot, and every number carries the quote of the line that says it
(`value_citations`). The slots are close to the rule builder's JSON (ADR 0042), the next step.

Strict structured output: every property required, `additionalProperties: false`, nullable
numbers as ["number", "null"], repeated objects defined once in `$defs` (Anthropic refuses a
grammar written out many times — #SIW-lib-5). Check any change with one strict haiku call.
"""

from __future__ import annotations

from typing import Any

import jsonschema

STRUCTURED_SCHEMA_NAME = "technique_card_structured"
STRUCTURED_SCHEMA_VERSION = 2

SIDES = ("long", "short", "both")
COMPARATORS = (
    "cross_above",
    "cross_below",
    "above",
    "below",
    "at_or_above",
    "at_or_below",
    "equal",
    "touch",
    "rises",
    "falls",
    "none",
    "other",
)
OPERAND_KINDS = (
    "price",  # a price series of the bar: close, high, previous_high ...
    "indicator",  # an indicator line: ema, atr, bollinger_upper ...
    "value",  # a plain number: RSI 30, 500,000 shares
    "percent",  # a percentage of price or of the account: 2 (%)
    "atr_multiple",  # so many ATR (or N) from a price
    "range_multiple",  # so many times a bar's range
    "bars",  # a count of bars or days: hold 3 days, within 15 bars
    "none",
    "other",
)
NAMES = (
    "close", "open", "high", "low", "volume", "entry_price", "stop_price", "target_price",
    "previous_close", "previous_high", "previous_low", "previous_range",
    "highest_high", "lowest_low", "highest_close", "lowest_close",
    "ma", "ema", "sma", "wma", "rsi", "atr", "macd", "macd_signal", "macd_histogram",
    "bollinger_upper", "bollinger_middle", "bollinger_lower", "bollinger_width",
    "adx", "plus_di", "minus_di", "stochastic_k", "stochastic_d", "vwap", "obv",
    "volume_average", "trading_value", "market_cap", "units", "holding_bars",
    "candle", "none", "other",
)  # fmt: skip


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


_NUMBER_OR_NULL = {"type": ["number", "null"]}
_CITATION = _object(
    {
        "locator": {
            "type": "string",
            "description": "Transcript time mm:ss (or h:mm:ss) of the quoted line, or p.<page>.",
        },
        "quote": {"type": "string", "description": "The source's words copied exactly, short."},
    }
)
_OPERAND = _object(
    {
        "kind": {"type": "string", "enum": list(OPERAND_KINDS)},
        "name": {"type": "string", "enum": list(NAMES)},
        "period": {**_NUMBER_OR_NULL, "description": "Indicator or look-back length, if said."},
        "value": {**_NUMBER_OR_NULL, "description": "The number for value/percent/multiple/bars."},
    }
)
_VALUE_CITATION = _object(
    {
        "value": {"type": "number", "description": "A number used in this condition."},
        "locator": {"type": "string"},
        "quote": {"type": "string", "description": "The exact words where the source says it."},
    }
)
_CONDITION = _object(
    {
        "field": {"type": "string", "enum": ["entry", "exit", "stop_loss"]},
        "side": {"type": "string", "enum": list(SIDES)},
        "text": {"type": "string", "description": "The condition in one sentence."},
        "basis": {"type": "string", "enum": ["source", "claude_inference"]},
        "left": {"$ref": "#/$defs/operand"},
        "comparator": {"type": "string", "enum": list(COMPARATORS)},
        "right": {"$ref": "#/$defs/operand"},
        "timeframe": {"type": "string", "description": "Chart period of this condition, or ''."},
        "citations": {"type": "array", "items": {"$ref": "#/$defs/citation"}},
        "value_citations": {"type": "array", "items": {"$ref": "#/$defs/value_citation"}},
    }
)
_STATEMENT = _object(
    {
        "text": {"type": "string"},
        "basis": {"type": "string", "enum": ["source", "claude_inference"]},
        "citations": {"type": "array", "items": {"$ref": "#/$defs/citation"}},
    }
)
_AMBIGUITY = _object(
    {
        "text": {"type": "string", "description": "What the source leaves vague."},
        "question": {"type": "string", "description": "The question to ask the user."},
        "citations": {"type": "array", "items": {"$ref": "#/$defs/citation"}},
    }
)

STRUCTURED_SCHEMA: dict[str, Any] = {
    **_object(
        {
            "source_id": {"type": "string"},
            "title": {"type": "string"},
            "conditions": {"type": "array", "items": {"$ref": "#/$defs/condition"}},
            **{
                name: {"type": "array", "items": {"$ref": "#/$defs/statement"}}
                for name in ("timeframe", "universe", "position", "inexpressible")
            },
            "ambiguities": {"type": "array", "items": {"$ref": "#/$defs/ambiguity"}},
        }
    ),
    "$defs": {
        "citation": _CITATION,
        "operand": _OPERAND,
        "value_citation": _VALUE_CITATION,
        "condition": _CONDITION,
        "statement": _STATEMENT,
        "ambiguity": _AMBIGUITY,
    },
}


def structured_response_schema() -> dict[str, Any]:
    return {"name": STRUCTURED_SCHEMA_NAME, "strict": True, "schema": STRUCTURED_SCHEMA}


def validate_structured(card: object) -> list[str]:
    validator = jsonschema.Draft202012Validator(STRUCTURED_SCHEMA)
    errors = sorted(validator.iter_errors(card), key=lambda error: list(error.absolute_path))
    return [
        f"{'/'.join(str(p) for p in error.absolute_path) or '(card)'}: {error.message}"
        for error in errors
    ]
