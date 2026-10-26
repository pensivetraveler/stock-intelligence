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
