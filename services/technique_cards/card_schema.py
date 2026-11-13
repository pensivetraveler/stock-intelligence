"""The technique card's JSON schema (ADR 0056 §3), written for strict structured output.

Strict mode (OpenAI / OpenRouter `json_schema` with `strict: true`) needs every property listed in
`required` and `additionalProperties: false` on every object, so absent values are empty lists.
The schema stays shallow and defines the repeated statement object once (`$defs`) to keep
within provider limits on grammar size and depth.
"""

from __future__ import annotations

from typing import Any

import jsonschema

CARD_SCHEMA_NAME = "technique_card"
CARD_SCHEMA_VERSION = 1

# The fields a card holds, in the order the card screen shows them.
STATEMENT_FIELDS = (
    "timeframe",
    "universe",
    "entry",
    "exit",
    "stop_loss",
    "position",
    "inexpressible",
)
# The fields scored as conditions (ADR 0056 §5: entry, exit and stop-loss agreement).
CONDITION_FIELDS = ("entry", "exit", "stop_loss")
BASES = ("source", "claude_inference")


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


_CITATION = _object(
    {
        "locator": {
            "type": "string",
            "description": "Where the quote is: transcript time mm:ss (or h:mm:ss), or p.<page>.",
        },
        "quote": {
            "type": "string",
            "description": "The source's words copied exactly, short (one sentence or less).",
        },
    }
)

_STATEMENT = _object(
    {
        "text": {"type": "string", "description": "The condition in one sentence."},
        "basis": {
            "type": "string",
            "enum": list(BASES),
            "description": "source when the source says it; claude_inference when you inferred it.",
        },
        "citations": {"type": "array", "items": _CITATION},
    }
)

_AMBIGUITY = _object(
    {
        "text": {"type": "string", "description": "What the source leaves vague."},
        "question": {"type": "string", "description": "The question to ask the user."},
        "citations": {"type": "array", "items": _CITATION},
    }
)

# The statement object is defined once and referenced by the seven fields: written out seven
# times, Anthropic's strict grammar was refused as too large (HTTP 400, gateway #SIW-lib-5).
# Any change here needs one strict call through a small model (haiku) before use.
CARD_SCHEMA: dict[str, Any] = {
    **_object(
        {
            "source_id": {"type": "string"},
            "title": {"type": "string"},
            **{
                name: {"type": "array", "items": {"$ref": "#/$defs/condition"}}
                for name in STATEMENT_FIELDS
            },
            "ambiguities": {"type": "array", "items": _AMBIGUITY},
        }
    ),
    "$defs": {"condition": _STATEMENT},
}


def response_schema() -> dict[str, Any]:
    """The `responseSchema` / `json_schema` block as the eval set and the product send it."""
    return {"name": CARD_SCHEMA_NAME, "strict": True, "schema": CARD_SCHEMA}


def validate_card(card: object) -> list[str]:
    """Schema errors in a card, as readable lines; empty means the card is well formed."""
    validator = jsonschema.Draft202012Validator(CARD_SCHEMA)
    errors = sorted(validator.iter_errors(card), key=lambda error: list(error.absolute_path))
    return [
        f"{'/'.join(str(p) for p in error.absolute_path) or '(card)'}: {error.message}"
        for error in errors
    ]
