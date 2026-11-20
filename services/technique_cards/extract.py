"""Structured technique card extraction (ADR 0056 L2).

- Model: the default from the technique settings (Fable, Argon or Astra through OpenRouter,
  user request 2026-11-15; first default Argon, gateway assignment 2026-11-15
  `local-ai-gateway 9f2994a`), or the model the user picks for one run. Strict json_schema,
  max_tokens 16000, OpenRouter with data collection denied (data grade private). Sonnet with
  the Keys screen's Anthropic key stays choosable. Fallback `openai/gpt-6-luna`. Chinese
  vendors are not used.
- A source over 30 minutes is cut into ~20-minute slices with 30 s overlap (the evaluated
  method); each slice is one call, and the cards are merged by script.
- A slice whose answer is cut off (length), refused, or does not match the schema, or whose
  call fails for a passing reason (5xx, timeout), is retried once on the same model, then once
  on luna; after that the extraction fails and the user is told.
- A model at its limit (HTTP 429 or 402, or an error naming a rate limit, quota or credits) is
  not retried: the slice goes to the next model of another company (`chain`: Fable → the
  default setting or Argon → luna; gateway request #LAG-SIW-4, user instruction 2026-11-15).
  When the answer says when to try again, the model is skipped until then (`LimitBook`).
  The card's usage keeps the model asked for and every switch with its reason.
- Every call is logged: model, input / output tokens, cost. The transcript itself is never
  logged.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

import httpx

from services.technique_cards.citations import Line, format_locator, format_transcript, unit_of
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
SONNET_MODEL = "claude-sonnet-5-5"  # the only model called on the Anthropic API directly
PRIMARY_MODEL = SONNET_MODEL  # kept for older imports; the default is a setting now
FALLBACK_MODEL = "openai/gpt-6-luna"
# Models taken out of every list while paused. Fable 5.2 was paused on 2026-11-15
# (#LAG-SIW-2) and back the same day when the user lifted the pause (#LAG-SIW-3, ai-collab
# f96fe3d). To pause one, add it here and take it out of DEFAULT_CHOICES and the API's and
# screen's lists.
PAUSED_MODELS: tuple[str, ...] = ()
# Models the default setting may name (user request 2026-11-15 "fable, argon, astra").
DEFAULT_CHOICES = ("anthropic/claude-fable-5.2", "google/gemini-4-argon", "openai/gpt-6-astra")
INITIAL_DEFAULT = "google/gemini-4-argon"
# Models the user may pick for one run. Only sonnet and luna were evaluated (ADR 0056 §5);
# the others carry the same script checks and record their name on the card.
CHOOSABLE_MODELS = (*DEFAULT_CHOICES, SONNET_MODEL, FALLBACK_MODEL)
TIMEOUT_SECONDS = 600.0
# USD per million tokens (input, output), list prices 2026-11 (Anthropic; OpenRouter models
# list). Used for sonnet's cost and for every preview estimate; OpenRouter reports real cost.
SONNET_PRICE = (Decimal("2.00"), Decimal("10.00"))
PRICES = {
    "claude-sonnet-5-5": SONNET_PRICE,
    "anthropic/claude-fable-5.2": (Decimal("8.00"), Decimal("40.00")),
    "openai/gpt-6-astra": (Decimal("10.00"), Decimal("50.00")),
    "google/gemini-4-argon": (Decimal("2.00"), Decimal("10.00")),
    "openai/gpt-6-luna": (Decimal("0.10"), Decimal("0.50")),
}


class ExtractionError(RuntimeError):
    """No model produced a valid card for a slice; `attempts` says what was tried."""

    def __init__(self, message: str, attempts: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.attempts = attempts


class AttemptFailed(RuntimeError):
    """One call failed in a way a retry may fix (length, refusal, schema, HTTP).

    `limited`: the model is at its rate limit, quota or credits; retrying it does not help.
    `retry_at`: when the answer says the limit ends (UTC), if it says."""

    def __init__(
        self,
        reason: str,
        usage: dict[str, Any] | None = None,
        limited: bool = False,
        retry_at: datetime | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.usage = usage or {}
        self.limited = limited
        self.retry_at = retry_at


class LimitBook(Protocol):
    """Where a model's limit end is kept between runs (the card store)."""

    def limited_until(self, model: str) -> datetime | None: ...

    def hold(self, model: str, until: datetime, reason: str) -> None: ...


# Words in an error body that mean a limit, not a bad request ("limit" alone also names the
# context length, which another model would not fix either).
_LIMIT_WORDS = re.compile(
    r"rate[ _-]?limit|quota|credit|too many requests|usage limit|insufficient (funds|balance)",
    re.IGNORECASE,
)
LIMIT_STATUSES = (402, 429)
# A limit end further away than this is not trusted (a wrong header would hide a model).
MAX_HOLD = timedelta(days=7)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _retry_at(headers: httpx.Headers, now: datetime) -> datetime | None:
    """When to try again, from Retry-After (seconds or a date) or a reset header."""
    value = headers.get("retry-after")
    found: datetime | None = None
    if value:
        try:
            found = now + timedelta(seconds=float(value))
        except ValueError:
            try:
                found = parsedate_to_datetime(value)
            except (TypeError, ValueError):
                found = None
    reset = headers.get("x-ratelimit-reset")  # OpenRouter: epoch milliseconds
    if found is None and reset and reset.isdigit():
        found = datetime.fromtimestamp(int(reset) / 1000, timezone.utc)
    if found is None:
        return None
    if found.tzinfo is None:
        found = found.replace(tzinfo=timezone.utc)
    return found if now < found <= now + MAX_HOLD else None


def _error_text(response: httpx.Response) -> str:
    """The provider's error message and raw metadata, for the limit check only (never logged)."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:2000]
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return ""
    raw = (
        (error.get("metadata") or {}).get("raw") if isinstance(error.get("metadata"), dict) else ""
    )
    return f"{error.get('message') or ''} {error.get('type') or ''} {raw or ''}"[:2000]


def http_failure(service: str, response: httpx.Response) -> AttemptFailed:
    """An HTTP error answer, sorted into a limit or a failure a retry may fix."""
    status = response.status_code
    limited = status in LIMIT_STATUSES or bool(_LIMIT_WORDS.search(_error_text(response)))
    if not limited:
        return AttemptFailed(f"{service} HTTP {status}")
    return AttemptFailed(
        f"{service} HTTP {status}: limit",
        limited=True,
        retry_at=_retry_at(response.headers, _now()),
    )


@dataclass(frozen=True)
class Keys:
    anthropic: str | None
    openrouter: str | None


@dataclass
class Extraction:
    card: dict[str, Any]
    attempts: list[dict[str, Any]] = field(default_factory=list)
    parts: int = 1
    requested: str = ""
    # Each move to another model: {"part", "from", "to", "reason": "limit" | "failed"}.
    switches: list[dict[str, Any]] = field(default_factory=list)

    @property
    def usage(self) -> dict[str, Any]:
        cost = sum((Decimal(a.get("cost_usd") or "0") for a in self.attempts), Decimal("0"))
        return {
            "input_tokens": sum(int(a.get("input_tokens") or 0) for a in self.attempts),
            "output_tokens": sum(int(a.get("output_tokens") or 0) for a in self.attempts),
            "cost_usd": format(cost, "f"),
            "models": sorted({str(a["model"]) for a in self.attempts if a.get("ok")}),
            "calls": len(self.attempts),
            "requested_model": self.requested,
            "switches": self.switches,
        }


# (model, system, prompt, keys) -> (card, usage)
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
        "model": SONNET_MODEL,
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
        raise http_failure("Claude", response)
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
    model: str = FALLBACK_MODEL,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not keys.openrouter:
        raise AttemptFailed("no OpenRouter key on the Keys screen")
    body = {
        "model": model,
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
        raise http_failure("OpenRouter", response)
    try:
        message = response.json()
        error = message.get("error") if isinstance(message, dict) else None
        if isinstance(error, dict) and not message.get("choices"):
            # An error inside a 200 answer (an upstream provider's limit, for one).
            code = error.get("code")
            limited = code in LIMIT_STATUSES or bool(_LIMIT_WORDS.search(json.dumps(error)))
            raise AttemptFailed(
                f"OpenRouter error {code}" + (": limit" if limited else ""), limited=limited
            )
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


def vendor(model: str) -> str:
    """The company behind a model; a limit is often shared by one company's models."""
    return "anthropic" if model == SONNET_MODEL else model.split("/", 1)[0]


def chain(model: str, default: str = INITIAL_DEFAULT) -> tuple[str, ...]:
    """The models a slice goes to, in order, when the one before is at its limit: the chosen
    one, then the default setting or Argon if that is another company's, then luna."""
    if model not in CHOOSABLE_MODELS:
        raise ValueError(f"model must be one of {', '.join(CHOOSABLE_MODELS)}")
    order = [model]
    for other in (default, INITIAL_DEFAULT):
        if other in CHOOSABLE_MODELS and other != model and vendor(other) != vendor(model):
            order.append(other)
            break
    if FALLBACK_MODEL not in order:
        order.append(FALLBACK_MODEL)
    return tuple(order)


def ladder(model: str = INITIAL_DEFAULT) -> tuple[str, ...]:
    """The retry ladder for one slice: the chosen model twice, then luna once."""
    if model not in CHOOSABLE_MODELS:
        raise ValueError(f"model must be one of {', '.join(CHOOSABLE_MODELS)}")
    return (model, model) if model == FALLBACK_MODEL else (model, model, FALLBACK_MODEL)


def _default_caller(transport: httpx.BaseTransport | None) -> Caller:
    def call(
        model: str, system: str, prompt: str, keys: Keys
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if model == SONNET_MODEL:
            return call_anthropic(system, prompt, keys, transport)
        return call_openrouter(system, prompt, keys, transport, model=model)

    return call


def prompts_for(source_id: str, title: str, lines: list[Line]) -> list[str]:
    """The prompt of each time slice, exactly as sent (the screen can show slices and sizes)."""
    parts = split_parts(lines)
    unit = unit_of(lines)
    whole = format_locator(unit, parts[-1][-1].seconds) if parts and parts[-1] else "0:00"
    return [
        structured_prompt(
            source_id,
            title,
            format_transcript(part),
            None if len(parts) == 1 else (k, len(parts)),
            (format_locator(unit, part[0].seconds), format_locator(unit, part[-1].seconds), whole),
            unit,
        )
        for k, part in enumerate(parts, start=1)
    ]


def _slice(
    k: int,
    prompt: str,
    keys: Keys,
    caller: Caller,
    model: str,
    order: tuple[str, ...],
    limits: LimitBook | None,
    attempts: list[dict[str, Any]],
    switches: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """One slice through `order`; None when no model gave a valid card."""
    index, tries = 0, 0

    def move(to: int, reason: str) -> int:
        if to < len(order):
            switches.append({"part": k, "from": order[index], "to": order[to], "reason": reason})
        return to

    while index < len(order):
        step = order[index]
        until = limits.limited_until(step) if limits is not None else None
        if until is not None and until > _now():
            attempts.append({"part": k, "model": step, "ok": False, "skipped": True,
                             "reason": f"limit until {until.isoformat()}"})  # fmt: skip
            index, tries = move(index + 1, "limit"), 0
            continue
        record: dict[str, Any] = {"part": k, "model": step, "ok": False}
        try:
            card, usage = caller(step, STRUCTURED_SYSTEM, prompt, keys)
        except AttemptFailed as exc:
            record.update(exc.usage, reason=exc.reason)
            attempts.append(record)
            if exc.limited:
                if exc.retry_at is not None and limits is not None:
                    limits.hold(step, exc.retry_at, exc.reason)
                index, tries = move(index + 1, "limit"), 0
                continue
            tries += 1
            if step == model and tries < 2:
                continue  # the chosen model gets a second try
            last = order.index(FALLBACK_MODEL)
            if step == FALLBACK_MODEL or last <= index:
                return None
            index, tries = move(last, "failed"), 0
            continue
        record.update(usage, ok=True)
        attempts.append(record)
        return card
    return None


def extract_card(
    source_id: str,
    title: str,
    lines: list[Line],
    keys: Keys,
    call: Caller | None = None,
    transport: httpx.BaseTransport | None = None,
    model: str = INITIAL_DEFAULT,
    default: str = INITIAL_DEFAULT,
    limits: LimitBook | None = None,
) -> Extraction:
    order = chain(model, default)
    if not lines:
        raise ExtractionError("the source has no text lines", [])
    caller = call or _default_caller(transport)
    attempts: list[dict[str, Any]] = []
    switches: list[dict[str, Any]] = []
    cards: list[object] = []
    prompts = prompts_for(source_id, title, lines)
    for k, prompt in enumerate(prompts, start=1):
        card = _slice(k, prompt, keys, caller, model, order, limits, attempts, switches)
        if card is None:
            raise ExtractionError(
                f"slice {k} of {len(prompts)}: no model gave a valid card", attempts
            )
        cards.append(card)
    merged = cards[0] if len(cards) == 1 else merge_parts(cards, source_id)
    assert isinstance(merged, dict)
    merged["source_id"] = source_id
    return Extraction(
        card=merged, attempts=attempts, parts=len(prompts), requested=model, switches=switches
    )


VERSIONS = {
    "schema": STRUCTURED_SCHEMA_VERSION,
    "prompt": STRUCTURED_PROMPT_VERSION,
    "model": INITIAL_DEFAULT,
    "fallback": FALLBACK_MODEL,
}
