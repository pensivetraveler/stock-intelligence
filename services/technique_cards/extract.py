"""Structured technique card extraction (ADR 0056 L2, model assignment confirmed 2026-11-14).

- Model: `claude-sonnet-5-5` with the Keys screen's Anthropic key, strict json_schema,
  max_tokens 16000. Fallback `openai/gpt-6-luna` through OpenRouter with data collection denied
  (data grade private). Chinese vendors are not used.
- A source over 30 minutes is cut into ~20-minute slices with 30 s overlap (the evaluated
  method); each slice is one call, and the cards are merged by script.
- A slice whose answer is cut off (length), refused, or does not match the schema is retried
  once on sonnet, then once on luna; after that the extraction fails and the user is told.
- Every call is logged: model, input / output tokens, cost. The transcript itself is never
  logged.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx

from services.technique_cards.citations import Line, format_time, format_transcript
from services.technique_cards.evalset import STRUCTURED_MAX_TOKENS, split_parts
from services.technique_cards.prompt import (
    STRUCTURED_PROMPT_VERSION,
    STRUCTURED_SYSTEM,
    structured_prompt,
)
from services.technique_cards.structured_schema import (
    STRUCTURED_SCHEMA,
    STRUCTURED_SCHEMA_NAME,
    STRUCTURED_SCHEMA_VERSION,
    validate_structured,
)
from services.technique_cards.structured_scoring import merge_parts

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
PRIMARY_MODEL = "claude-sonnet-5-5"
FALLBACK_MODEL = "openai/gpt-6-luna"
TIMEOUT_SECONDS = 600.0
# USD per million tokens (input, output), Anthropic first-party list price 2026-11.
SONNET_PRICE = (Decimal("2.00"), Decimal("10.00"))


class ExtractionError(RuntimeError):
    """No model produced a valid card for a slice; `attempts` says what was tried."""

    def __init__(self, message: str, attempts: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.attempts = attempts


class AttemptFailed(RuntimeError):
    """One call failed in a way a retry may fix (length, refusal, schema, HTTP)."""

    def __init__(self, reason: str, usage: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.usage = usage or {}


@dataclass(frozen=True)
class Keys:
    anthropic: str | None
    openrouter: str | None


@dataclass
class Extraction:
    card: dict[str, Any]
    attempts: list[dict[str, Any]] = field(default_factory=list)
    parts: int = 1

    @property
    def usage(self) -> dict[str, Any]:
        cost = sum((Decimal(a.get("cost_usd") or "0") for a in self.attempts), Decimal("0"))
        return {
            "input_tokens": sum(int(a.get("input_tokens") or 0) for a in self.attempts),
            "output_tokens": sum(int(a.get("output_tokens") or 0) for a in self.attempts),
            "cost_usd": format(cost, "f"),
            "models": sorted({str(a["model"]) for a in self.attempts if a.get("ok")}),
            "calls": len(self.attempts),
        }


Caller = Callable[[str, str, str, Keys], tuple[dict[str, Any], dict[str, Any]]]


def _parse_card(text: str) -> dict[str, Any]:
    try:
        card = json.loads(text)
    except ValueError as exc:
        raise AttemptFailed("not JSON") from exc
    errors = validate_structured(card)
    if errors:
        raise AttemptFailed(f"schema: {errors[0][:200]}")
    assert isinstance(card, dict)
    return card


def call_anthropic(
    system: str,
    prompt: str,
    keys: Keys,
    transport: httpx.BaseTransport | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not keys.anthropic:
        raise AttemptFailed("no Claude (Anthropic) key on the Keys screen")
    body = {
        "model": PRIMARY_MODEL,
        "max_tokens": STRUCTURED_MAX_TOKENS,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
        "output_config": {"format": {"type": "json_schema", "schema": STRUCTURED_SCHEMA}},
    }
    headers = {"x-api-key": keys.anthropic, "anthropic-version": ANTHROPIC_VERSION}
    try:
        with httpx.Client(transport=transport, timeout=TIMEOUT_SECONDS) as client:
            response = client.post(ANTHROPIC_URL, json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise AttemptFailed(f"could not reach Claude: {type(exc).__name__}") from exc
    if response.status_code >= 400:
        raise AttemptFailed(f"Claude HTTP {response.status_code}")
    try:
        message = response.json()
        raw = message.get("usage") or {}
    except ValueError as exc:
        raise AttemptFailed("Claude answer is not JSON") from exc
    usage: dict[str, Any] = {
        "input_tokens": int(raw.get("input_tokens") or 0),
        "output_tokens": int(raw.get("output_tokens") or 0),
    }
    usage["cost_usd"] = format(
        (SONNET_PRICE[0] * usage["input_tokens"] + SONNET_PRICE[1] * usage["output_tokens"]).scaleb(
            -6
        ),
        "f",
    )
    stop = message.get("stop_reason")
    if stop == "max_tokens":
        raise AttemptFailed("length", usage)
    if stop == "refusal":
        raise AttemptFailed("refusal", usage)
    text = "".join(
        b.get("text", "") for b in message.get("content") or [] if b.get("type") == "text"
    )
    try:
        return _parse_card(text), usage
    except AttemptFailed as exc:
        raise AttemptFailed(exc.reason, usage) from exc


def call_openrouter(
    system: str,
    prompt: str,
    keys: Keys,
    transport: httpx.BaseTransport | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not keys.openrouter:
        raise AttemptFailed("no OpenRouter key on the Keys screen")
    body = {
        "model": FALLBACK_MODEL,
        "max_tokens": STRUCTURED_MAX_TOKENS,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": STRUCTURED_SCHEMA_NAME,
                "strict": True,
                "schema": STRUCTURED_SCHEMA,
            },
        },
        # Data grade private: only providers that neither train on nor keep the prompt.
        "provider": {"data_collection": "deny", "require_parameters": True},
        "usage": {"include": True},
    }
    headers = {"Authorization": f"Bearer {keys.openrouter}"}
    try:
        with httpx.Client(transport=transport, timeout=TIMEOUT_SECONDS) as client:
            response = client.post(OPENROUTER_URL, json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise AttemptFailed(f"could not reach OpenRouter: {type(exc).__name__}") from exc
    if response.status_code >= 400:
        raise AttemptFailed(f"OpenRouter HTTP {response.status_code}")
    try:
        message = response.json()
        choice = message["choices"][0]
        raw = message.get("usage") or {}
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise AttemptFailed("OpenRouter answer has no choice") from exc
    usage = {
        "input_tokens": int(raw.get("prompt_tokens") or 0),
        "output_tokens": int(raw.get("completion_tokens") or 0),
        "cost_usd": format(Decimal(str(raw.get("cost") or 0)), "f"),
    }
    if choice.get("finish_reason") == "length":
        raise AttemptFailed("length", usage)
    text = str((choice.get("message") or {}).get("content") or "")
    try:
        return _parse_card(text), usage
    except AttemptFailed as exc:
        raise AttemptFailed(exc.reason, usage) from exc


# The retry ladder for one slice: sonnet, sonnet again, luna once.
LADDER: tuple[tuple[str, str], ...] = (
    (PRIMARY_MODEL, "anthropic"),
    (PRIMARY_MODEL, "anthropic"),
    (FALLBACK_MODEL, "openrouter"),
)


def _default_caller(
    transport: httpx.BaseTransport | None,
) -> Callable[[str, str, str, Keys], tuple[dict[str, Any], dict[str, Any]]]:
    def call(route: str, system: str, prompt: str, keys: Keys):  # type: ignore[no-untyped-def]
        if route == "anthropic":
            return call_anthropic(system, prompt, keys, transport)
        return call_openrouter(system, prompt, keys, transport)

    return call


def prompts_for(source_id: str, title: str, lines: list[Line]) -> list[str]:
    """The prompt of each time slice, exactly as sent (the screen can show slices and sizes)."""
    parts = split_parts(lines)
    whole = format_time(parts[-1][-1].seconds) if parts and parts[-1] else "0:00"
    return [
        structured_prompt(
            source_id,
            title,
            format_transcript(part),
            None if len(parts) == 1 else (k, len(parts)),
            (format_time(part[0].seconds), format_time(part[-1].seconds), whole),
        )
        for k, part in enumerate(parts, start=1)
    ]


def extract_card(
    source_id: str,
    title: str,
    lines: list[Line],
    keys: Keys,
    call: Caller | None = None,
    transport: httpx.BaseTransport | None = None,
) -> Extraction:
    if not lines:
        raise ExtractionError("the transcript has no timed lines", [])
    caller = call or _default_caller(transport)
    attempts: list[dict[str, Any]] = []
    cards: list[object] = []
    prompts = prompts_for(source_id, title, lines)
    for k, prompt in enumerate(prompts, start=1):
        card = None
        for model, route in LADDER:
            record: dict[str, Any] = {"part": k, "model": model, "ok": False}
            try:
                card, usage = caller(route, STRUCTURED_SYSTEM, prompt, keys)
                record.update(usage, ok=True)
            except AttemptFailed as exc:
                record.update(exc.usage, reason=exc.reason)
            attempts.append(record)
            if card is not None:
                break
        if card is None:
            raise ExtractionError(
                f"slice {k} of {len(prompts)}: no model gave a valid card", attempts
            )
        cards.append(card)
    merged = cards[0] if len(cards) == 1 else merge_parts(cards, source_id)
    assert isinstance(merged, dict)
    merged["source_id"] = source_id
    return Extraction(card=merged, attempts=attempts, parts=len(prompts))


VERSIONS = {
    "schema": STRUCTURED_SCHEMA_VERSION,
    "prompt": STRUCTURED_PROMPT_VERSION,
    "model": PRIMARY_MODEL,
    "fallback": FALLBACK_MODEL,
}
