"""Follow-up questions on a trade report (ADR 0042, step 2b).

One conversation per report. Its first message repeats exactly what the report sent (the
facts, the investor's note and the same image bytes) plus the report itself, so Claude
answers from the same evidence. From then on the history is append-only: the system
prompt and tools are fixed when the conversation starts, and every assistant turn is
stored as returned (thinking blocks included) and replayed unchanged.

Claude may call the read-only tools in `tools.py`, at most `MAX_TOOL_CALLS` per question.
A chart image is drawn by the app: the loop stops with the bars to draw and resumes when
the PNGs arrive.
"""

from __future__ import annotations

import base64
import contextlib
import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Protocol

import httpx

from services.agents.commentary import API_URL, API_VERSION, LANGUAGES
from services.review.claude import payload_for
from services.review.store import ReviewStore
from services.review.tools import IMAGE_TOOLS, TOOLS, ToolInputError, ToolResult, as_text


class ToolRunner(Protocol):
    def run(self, name: str, tool_input: Any) -> ToolResult: ...


MAX_TOOL_CALLS = 6
MAX_STEPS = 10
MAX_TOKENS = 16_000
TIMEOUT_SECONDS = 180.0
# Thinking blocks are bound to the conversation that made them. The history here is
# append-only, so a mismatch would be a bug; dropping the block keeps the answer going.
BINDING_BETA = "thinking-binding-controls-2026-08-01"

SYSTEM = """You answer an investor's follow-up questions about one of their trades and the
review you wrote of it. The first message holds the evidence the review used (facts,
the investor's note written after the trade, chart images) and the review itself.
Rules:
- Use only that evidence and what the tools return. No outside knowledge about the company,
  news or later prices.
- Facts or bars flagged hindsight were not knowable when the decision was made; say so
  whenever you use them. Tools stop at the trade's exit unless you ask for later bars.
- Prefer one or two well-chosen tool calls over many; at most {limit} per question.
- No buy, sell or hold recommendations, no price targets. This is a review, not advice.
- Answer concisely in {language}, in plain text with short paragraphs or bullet lists."""


class ConversationError(RuntimeError):
    """Claude could not be asked, or the answer could not be used."""


def call_claude(
    model: str,
    system: str,
    tools: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    api_key: str,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    body = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": system,
        "tools": tools,
        "messages": messages,
        "thinking": {
            "type": "adaptive",
            "block_binding": {"prefix_mismatch_behavior": "drop_block"},
        },
        # Each turn re-sends the images and history; the prefix never changes, so cache it.
        "cache_control": {"type": "ephemeral"},
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": API_VERSION,
        "anthropic-beta": BINDING_BETA,
    }
    try:
        with httpx.Client(transport=transport, timeout=TIMEOUT_SECONDS) as client:
            response = client.post(API_URL, json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise ConversationError(f"could not reach Claude: {type(exc).__name__}") from exc
    if response.status_code in (401, 403):
        raise ConversationError("Claude refused the API key; check it on the Keys screen")
    if response.status_code == 429:
        raise ConversationError("Claude's rate limit was reached; try again later")
    if response.status_code >= 400:
        detail = ""
        with contextlib.suppress(ValueError, KeyError, TypeError):
            detail = str(response.json()["error"]["message"])[:300]
        raise ConversationError(f"Claude answered HTTP {response.status_code} {detail}".strip())
    try:
        message: dict[str, Any] = response.json()
        message["content"]  # noqa: B018 - a reply without content is unusable
    except (ValueError, KeyError) as exc:
        raise ConversationError("Claude's reply could not be read") from exc
    return message


PERIOD_SYSTEM = """You answer an investor's follow-up questions about a review of their
trading over a period. The first message holds the period's statistics, their principles,
the best and worst trades with notes (written after the trades), and your commentary.
Rules:
- Use only that and what the period_trades tool returns; cite numbers. No outside
  knowledge about companies, news or later prices.
- What-if totals are hindsight; say so when you use them.
- Prefer one or two well-chosen tool calls; at most {limit} per question.
- No buy, sell or hold recommendations, no price targets. Not advice.
- Answer concisely in {language}, in plain text with short paragraphs or bullet lists."""


def period_system_prompt(language: str) -> str:
    if language not in LANGUAGES:
        raise ConversationError("language must be en or ko")
    return PERIOD_SYSTEM.format(language=LANGUAGES[language], limit=MAX_TOOL_CALLS)


def system_prompt(language: str) -> str:
    if language not in LANGUAGES:
        raise ConversationError("language must be en or ko")
    return SYSTEM.format(language=LANGUAGES[language], limit=MAX_TOOL_CALLS)


def first_content(
    draft: dict[str, Any], report: dict[str, Any], images: list[tuple[str, bytes]]
) -> list[dict[str, Any]]:
    """The evidence and report, as sent before, then the question follows."""
    content: list[dict[str, Any]] = []
    titles = {image["kind"]: image["title"] for image in draft["images"]}
    for kind, png in images:
        content.append({"type": "text", "text": f"Image '{kind}': {titles.get(kind, kind)}"})
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.b64encode(png).decode(),
                },
            }
        )
    evidence = payload_for(draft)
    if not images:
        evidence["note_on_images"] = "The report's images were not kept; they are left out."
    review = {k: report[k] for k in ("summary", "sections", "uncertainty")}
    content.append(
        {
            "type": "text",
            "text": json.dumps({"evidence": evidence, "your_review": review}, ensure_ascii=False),
        }
    )
    return content


def _utc(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class Conversation:
    """Runs one question to its answer, pausing for chart images drawn by the app."""

    def __init__(
        self,
        store: ReviewStore,
        conversation: dict[str, Any],
        tools: ToolRunner,
        call: Callable[[str, str, list[dict[str, Any]], list[dict[str, Any]]], dict[str, Any]],
        now: Callable[[], datetime],
        specs: list[dict[str, Any]] = TOOLS,
    ) -> None:
        self.store = store
        self.id = conversation["conversation_id"]
        self.model = conversation["model"]
        self.system = conversation["system"]
        self.state: dict[str, Any] = conversation["state"]
        self.tools = tools
        # The tool definitions sent with every turn; fixed for the conversation.
        self.specs = specs
        self.call = call
        self.now = now

    def _save(self) -> None:
        self.store.set_conversation_state(self.id, self.state)

    def _event(self, event: dict[str, Any]) -> None:
        self.store.append_event(self.id, {**event, "at_utc": _utc(self.now())})

    def ask(self, question: str, opening: list[dict[str, Any]] | None) -> None:
        if self.state.get("status") == "needs_images":
            raise ConversationError("the previous answer is still waiting for chart images")
        content = [*(opening or []), {"type": "text", "text": question}]
        self.store.append_message(self.id, {"role": "user", "content": content})
        self._event({"type": "question", "text": question})
        self.state.update(status="running", tool_calls=0, error=None)
        self._save()
        self._run()

    def add_images(self, images: dict[str, str]) -> None:
        pending = self.state.get("pending")
        if self.state.get("status") != "needs_images" or not pending:
            raise ConversationError("no chart image is awaited")
        wanted = {item["tool_use_id"] for item in pending["images"]}
        if set(images) != wanted:
            raise ConversationError("send exactly the awaited images")
        results = []
        for result in pending["results"]:
            if result["tool_use_id"] in images:
                image = next(
                    i for i in pending["images"] if i["tool_use_id"] == result["tool_use_id"]
                )
                note = f"Chart drawn up to {image['cutoff_utc']}: {image['title']}"
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": result["tool_use_id"],
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": images[result["tool_use_id"]],
                                },
                            },
                            {"type": "text", "text": note},
                        ],
                    }
                )
            else:
                results.append(result)
        self.store.append_message(self.id, {"role": "user", "content": results})
        self.state.update(status="running", pending=None)
        self._save()
        self._run()

    def _run(self) -> None:
        for _step in range(MAX_STEPS):
            try:
                message = self.call(
                    self.model, self.system, self.specs, self.store.messages(self.id)
                )
            except ConversationError as exc:
                self._fail(str(exc))
                return
            content = message["content"]
            self.store.append_message(self.id, {"role": "assistant", "content": content})
            usage = message.get("usage") or {}
            self.state["input_tokens"] = self.state.get("input_tokens", 0) + int(
                usage.get("input_tokens") or 0
            )
            self.state["output_tokens"] = self.state.get("output_tokens", 0) + int(
                usage.get("output_tokens") or 0
            )
            # input_tokens counts only uncached input; cache reads and writes are separate.
            for key, field in (
                ("cache_read_tokens", "cache_read_input_tokens"),
                ("cache_write_tokens", "cache_creation_input_tokens"),
            ):
                self.state[key] = self.state.get(key, 0) + int(usage.get(field) or 0)
            text = "".join(
                block.get("text", "") for block in content if block.get("type") == "text"
            ).strip()
            stop = message.get("stop_reason")
            if stop == "tool_use":
                if text:
                    self._event({"type": "answer", "text": text, "model": message.get("model")})
                if self._tools(content):
                    return  # waiting for chart images
                continue
            if stop == "refusal":
                self._fail("Claude declined to answer this question")
                return
            if stop == "max_tokens":
                self._fail("Claude's answer was cut off")
                return
            self._event({"type": "answer", "text": text, "model": message.get("model")})
            self.state.update(status="idle")
            self._save()
            return
        self._fail("Claude kept asking for data without answering")

    def _tools(self, content: list[dict[str, Any]]) -> bool:
        """Runs the turn's tool calls; True when chart images must be drawn first."""
        results: list[dict[str, Any]] = []
        images: list[dict[str, Any]] = []
        for block in content:
            if block.get("type") != "tool_use":
                continue
            tool_use_id, name, tool_input = block["id"], block["name"], block.get("input") or {}
            self.state["tool_calls"] = self.state.get("tool_calls", 0) + 1
            if self.state["tool_calls"] > MAX_TOOL_CALLS:
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "is_error": True,
                        "content": "The data request limit for this question is reached; "
                        "answer with what you have.",
                    }
                )
                self._event(
                    {
                        "type": "request",
                        "tool": name,
                        "input": tool_input,
                        "summary": "limit reached",
                        "error": True,
                        "hindsight": False,
                    }
                )
                continue
            try:
                result = self.tools.run(name, tool_input)
            except ToolInputError as exc:
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "is_error": True,
                        "content": str(exc),
                    }
                )
                self._event(
                    {
                        "type": "request",
                        "tool": name,
                        "input": tool_input,
                        "summary": str(exc),
                        "error": True,
                        "hindsight": False,
                    }
                )
                continue
            self._event(
                {
                    "type": "request",
                    "tool": name,
                    "input": tool_input,
                    "summary": result.summary,
                    "error": False,
                    "hindsight": result.hindsight,
                }
            )
            if name in IMAGE_TOOLS and result.image is not None:
                images.append({"tool_use_id": tool_use_id, **result.image})
                results.append({"type": "tool_result", "tool_use_id": tool_use_id})
            else:
                results.append(
                    {"type": "tool_result", "tool_use_id": tool_use_id, "content": as_text(result)}
                )
        if images:
            self.state.update(status="needs_images", pending={"results": results, "images": images})
            self._save()
            return True
        self.store.append_message(self.id, {"role": "user", "content": results})
        self._save()
        return False

    def _fail(self, error: str) -> None:
        self._event({"type": "error", "text": error})
        self.state.update(status="failed", error=error, pending=None)
        self._save()
