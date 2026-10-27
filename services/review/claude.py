"""Ask Claude for a single-trade report (ADR 0042, step 2a).

What is sent: the trade's facts, the user's own note (marked as written after the trade)
and up to three chart images, each cut at its time. No account numbers, keys or record
IDs. The reply must follow a JSON schema (structured output); anything else is refused,
never guessed at.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import httpx

from services.agents.commentary import API_URL, API_VERSION, LANGUAGES

MODELS = ("claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5")
DEFAULT_MODEL = "claude-sonnet-5-5"
# These models take the server-side refusal fallback ("default" picks the model).
FALLBACK_MODELS = ("claude-sonnet-5-5", "claude-opus-5-5")
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 16_000
TIMEOUT_SECONDS = 180.0
SECTIONS = ("entry", "exit", "sizing")

SYSTEM = """You review one closed or open trade of a private investor. Use ONLY what is given:
the facts (JSON), the investor's own note, and the chart images. Rules:
- Every point must follow from the given facts or images. No outside knowledge about the
  company, news or later prices. If something cannot be told from what is given, say so.
- Facts flagged "hindsight": true were not knowable when the decision was made; say so
  whenever you use one. Each image says up to when it is drawn; nothing after that.
- The investor's note was written after the trade; treat it as their recollection, not as
  evidence of what they saw at the time.
- Write three sections: "entry" (timing of the buys), "exit" (selling and holding), and
  "sizing" (scaling in or out, and size against the account). In each, list the fact codes
  or image kinds you rely on in "evidence", then what went well, what to improve, and what
  to check next time. Leave a list empty rather than inventing a point.
- "trading_principles" holds the investor's own rules and how the app checked them. Use
  the app's results for numeric rules as they are. Rules with status "judgment" are text:
  judge them from the evidence and say plainly that it is your judgment. If the check is
  "after_the_fact", the rules were written after this trade; say so when you use them.
- No buy, sell or hold recommendations, no price targets. This is a review, not advice.
- Be concise and concrete. Answer in {language}."""

REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "sections", "uncertainty"],
    "properties": {
        "summary": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["key", "evidence", "went_well", "to_improve", "check_next"],
                "properties": {
                    "key": {"type": "string", "enum": list(SECTIONS)},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                    "went_well": {"type": "array", "items": {"type": "string"}},
                    "to_improve": {"type": "array", "items": {"type": "string"}},
                    "check_next": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "uncertainty": {"type": "string"},
    },
}


class ReportError(RuntimeError):
    """Claude could not be asked, or did not answer as required."""


def payload_for(draft: dict[str, Any]) -> dict[str, Any]:
    """The text part of what is sent; the screen shows it (with the images) first."""
    return {
        "trade": draft["trade"],
        "facts": draft["facts"],
        "missing_facts": draft["missing"],
        "known_uncertainty": draft["uncertainty"],
        "data_warnings": draft["warnings"][:20],
        "investor_note_written_after_the_trade": draft["note"],
        "trading_principles": draft.get("principles"),
        "images": [
            {
                "kind": image["kind"],
                "timeframe": image["timeframe"],
                "title": image["title"],
                "drawn_up_to_utc": image["cutoff_utc"],
            }
            for image in draft["images"]
        ],
    }


def _strings(value: object, limit: int = 10) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item)[:1_000] for item in value if isinstance(item, str) and item.strip()][:limit]


def ask_for_report(
    draft: dict[str, Any],
    images: list[tuple[str, str]],
    api_key: str,
    language: str,
    model: str = DEFAULT_MODEL,
    transport: httpx.BaseTransport | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    """`images` are (kind, base64 PNG) in the draft's order. Returns the report body."""
    if language not in LANGUAGES:
        raise ReportError("language must be en or ko")
    if model not in MODELS:
        raise ReportError(f"model must be one of {', '.join(MODELS)}")
    content: list[dict[str, Any]] = []
    titles = {image["kind"]: image["title"] for image in draft["images"]}
    for kind, data in images:
        content.append({"type": "text", "text": f"Image '{kind}': {titles.get(kind, kind)}"})
        content.append(
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}}
        )
    content.append({"type": "text", "text": json.dumps(payload_for(draft), ensure_ascii=False)})
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM.format(language=LANGUAGES[language]),
        "messages": [{"role": "user", "content": content}],
        "output_config": {"format": {"type": "json_schema", "schema": REPORT_SCHEMA}},
    }
    headers = {"x-api-key": api_key, "anthropic-version": API_VERSION}
    if model in FALLBACK_MODELS:
        # A safety decline is retried on a fallback model chosen by the API.
        body["fallbacks"] = "default"
        headers["anthropic-beta"] = FALLBACK_BETA
    try:
        with httpx.Client(transport=transport, timeout=TIMEOUT_SECONDS) as client:
            response = client.post(API_URL, json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise ReportError(f"could not reach Claude: {type(exc).__name__}") from exc
    if response.status_code in (401, 403):
        raise ReportError("Claude refused the API key; check it on the Keys screen")
    if response.status_code == 429:
        raise ReportError("Claude's rate limit was reached; try again later")
    if response.status_code >= 400:
        raise ReportError(f"Claude answered HTTP {response.status_code}")
    try:
        message = response.json()
        if message.get("stop_reason") == "refusal":
            raise ReportError("Claude declined to review this trade")
        if message.get("stop_reason") == "max_tokens":
            raise ReportError("Claude's answer was cut off; try again")
        text = "".join(
            block.get("text", "") for block in message["content"] if block.get("type") == "text"
        ).strip()
        parsed = json.loads(text)
    except ReportError:
        raise
    except (ValueError, KeyError, TypeError) as exc:
        raise ReportError("Claude did not answer in the required format") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("summary"), str):
        raise ReportError("Claude did not answer in the required format")
    sections = []
    for key in SECTIONS:
        found = next(
            (s for s in parsed.get("sections", []) if isinstance(s, dict) and s.get("key") == key),
            {},
        )
        sections.append(
            {
                "key": key,
                "evidence": _strings(found.get("evidence"), 20),
                "went_well": _strings(found.get("went_well")),
                "to_improve": _strings(found.get("to_improve")),
                "check_next": _strings(found.get("check_next")),
            }
        )
    usage = message.get("usage") or {}
    return {
        "model": str(message.get("model") or model)[:100],
        "language": language,
        "created_at_utc": now()
        .astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "summary": parsed["summary"][:2_000],
        "sections": sections,
        "uncertainty": str(parsed.get("uncertainty", ""))[:2_000],
        "usage": {
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
        },
    }


PRINCIPLES_SYSTEM = """Turn an investor's trading principles, written in their own words, into
items. Each sentence or rule becomes one item. Use a numeric kind only when the rule is
exactly one of these, with its number:
- stop_loss_pct: sell when the price falls this % below the average buy;
- take_profit_pct: sell when the price rises this % above the average buy;
- max_position_pct: one position at most this % of the account capital;
- no_entry_first_minutes: no buying in this many minutes after the regular open;
- max_gap_chase_pct: no buying at or above this % over the previous close;
- max_trades_per_day: at most this many trades opened in a day.
Anything else is kind "text" with value null; keep the investor's meaning, do not add
rules or numbers that were not written. Keep each item's "text" short, in {language}.
"scope" is "shared" unless the investor ties the rule to one of these strategies (use its
id): {strategies}. Put anything you were unsure about in "notes"."""

PRINCIPLES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items", "notes"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["scope", "kind", "value", "text"],
                "properties": {
                    "scope": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": [
                            "stop_loss_pct",
                            "take_profit_pct",
                            "max_position_pct",
                            "no_entry_first_minutes",
                            "max_gap_chase_pct",
                            "max_trades_per_day",
                            "text",
                        ],
                    },
                    "value": {"type": ["number", "null"]},
                    "text": {"type": "string"},
                },
            },
        },
        "notes": {"type": "string"},
    },
}


def convert_principles(
    text: str,
    strategies: list[dict[str, str]],
    api_key: str,
    language: str,
    model: str = DEFAULT_MODEL,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Claude's proposed items for the investor's text; nothing is stored here."""
    if language not in LANGUAGES:
        raise ReportError("language must be en or ko")
    listed = ", ".join(f"{s['strategy_id']} ({s['name']})" for s in strategies) or "none"
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": 8_000,
        "system": PRINCIPLES_SYSTEM.format(language=LANGUAGES[language], strategies=listed),
        "messages": [{"role": "user", "content": text}],
        "output_config": {"format": {"type": "json_schema", "schema": PRINCIPLES_SCHEMA}},
    }
    headers = {"x-api-key": api_key, "anthropic-version": API_VERSION}
    try:
        with httpx.Client(transport=transport, timeout=TIMEOUT_SECONDS) as client:
            response = client.post(API_URL, json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise ReportError(f"could not reach Claude: {type(exc).__name__}") from exc
    if response.status_code in (401, 403):
        raise ReportError("Claude refused the API key; check it on the Keys screen")
    if response.status_code >= 400:
        raise ReportError(f"Claude answered HTTP {response.status_code}")
    try:
        message = response.json()
        if message.get("stop_reason") in ("refusal", "max_tokens"):
            raise ReportError("Claude could not convert these principles; try shorter text")
        reply = "".join(
            block.get("text", "") for block in message["content"] if block.get("type") == "text"
        )
        parsed = json.loads(reply)
    except ReportError:
        raise
    except (ValueError, KeyError, TypeError) as exc:
        raise ReportError("Claude did not answer in the required format") from exc
    items = parsed.get("items") if isinstance(parsed, dict) else None
    if not isinstance(items, list):
        raise ReportError("Claude did not answer in the required format")
    return {
        "items": [
            {
                "scope": str(item.get("scope") or "shared"),
                "kind": str(item.get("kind") or "text"),
                "value": None if item.get("value") is None else str(item["value"]),
                "text": str(item.get("text") or "")[:500],
            }
            for item in items
            if isinstance(item, dict)
        ],
        "notes": str(parsed.get("notes") or "")[:2_000],
        "model": str(message.get("model") or model)[:100],
    }
