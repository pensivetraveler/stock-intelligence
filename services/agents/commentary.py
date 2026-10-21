"""Claude commentary on stored evidence (ADR 0022).

Only the target description and the facts are sent: no account numbers, keys or
ledger IDs. Claude must reason from those facts alone, keep inferences, hypotheses and
questions apart, and never recommend trades. The reply is JSON; anything else is
stored as a failed commentary, never guessed at.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import httpx

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 1_500
TIMEOUT_SECONDS = 60.0
LANGUAGES = {"en": "English", "ko": "Korean"}

SYSTEM = """You review trades, charts and backtests for one private investor, using ONLY the
evidence given as JSON. Rules:
- Every statement must follow from the given facts. Do not use outside knowledge about the
  company, news or later prices. If the facts do not support a point, say it is unknown.
- Facts flagged "hindsight": true were not knowable at the time; say so when you use them.
- Keep inferences (what the facts suggest), hypotheses (possible explanations to test) and
  questions (what the investor could check or recall) separate.
- No buy, sell or hold recommendations, no price targets, no position sizing advice. This
  is analysis, not investment advice.
- Be concise and concrete. Answer in {language}.
Reply with one JSON object and nothing else:
{{"summary": "...", "inferences": ["..."], "hypotheses": ["..."], "questions": ["..."],
  "uncertainty": "..."}}"""


class CommentaryError(RuntimeError):
    """Claude could not be asked, or did not answer as required."""


def payload_for(analysis: dict[str, Any]) -> dict[str, Any]:
    """Exactly what is sent; the screen shows this before the user sends it."""
    target = {k: v for k, v in analysis["target"].items() if k != "run_id"}
    return {
        "target": target,
        "cutoff_utc": analysis["cutoff_utc"],
        "facts": [
            {k: fact[k] for k in ("code", "subject", "value", "unit", "text", "hindsight")}
            for fact in analysis["facts"]
        ],
        "missing_facts": analysis["missing"],
        "known_uncertainty": analysis["uncertainty"],
        "data_warnings": analysis["warnings"][:20],
    }


def _strings(value: object, limit: int = 12) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item)[:1_000] for item in value if isinstance(item, str) and item.strip()][:limit]


def ask_claude(
    analysis: dict[str, Any],
    api_key: str,
    language: str,
    model: str = DEFAULT_MODEL,
    transport: httpx.BaseTransport | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    if language not in LANGUAGES:
        raise CommentaryError("language must be en or ko")
    body = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM.format(language=LANGUAGES[language]),
        "messages": [
            {"role": "user", "content": json.dumps(payload_for(analysis), ensure_ascii=False)}
        ],
    }
    headers = {"x-api-key": api_key, "anthropic-version": API_VERSION}
    try:
        with httpx.Client(transport=transport, timeout=TIMEOUT_SECONDS) as client:
            response = client.post(API_URL, json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise CommentaryError(f"could not reach Claude: {type(exc).__name__}") from exc
    if response.status_code in (401, 403):
        raise CommentaryError("Claude refused the API key; check it on the Keys screen")
    if response.status_code == 429:
        raise CommentaryError("Claude's rate limit was reached; try again later")
    if response.status_code >= 400:
        raise CommentaryError(f"Claude answered HTTP {response.status_code}")
    try:
        message = response.json()
        text = "".join(
            block.get("text", "") for block in message["content"] if block.get("type") == "text"
        ).strip()
    except (ValueError, KeyError, TypeError) as exc:
        raise CommentaryError("Claude's reply could not be read") from exc
    start, end = text.find("{"), text.rfind("}")
    try:
        parsed = json.loads(text[start : end + 1]) if start >= 0 and end > start else None
    except ValueError:
        parsed = None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("summary"), str):
        raise CommentaryError("Claude did not answer in the required format")
    return {
        "model": str(message.get("model") or model)[:100],
        "language": language,
        "created_at_utc": now()
        .astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "summary": parsed["summary"][:4_000],
        "inferences": _strings(parsed.get("inferences")),
        "hypotheses": _strings(parsed.get("hypotheses")),
        "questions": _strings(parsed.get("questions")),
        "uncertainty": str(parsed.get("uncertainty") or "")[:2_000],
    }
