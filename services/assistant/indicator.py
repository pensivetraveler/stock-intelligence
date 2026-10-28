"""Claude writes Pine indicators with the investor (ADR 0042, indicators step 1).

The app holds the Pine interpreter, so Claude's `run_pine` tool is run by the app: the
loop stops with the script to run and resumes when the app posts the result (errors with
line and column, or a summary of the computed values). Each request may run at most
`MAX_RUNS` scripts (the first try and five fixes); when the last one still fails, the loop
stops and shows the error instead of asking Claude again.

What is sent: the supported Pine subset (from the app, fixed for the conversation), the
editor's code, and the chart's symbol, timeframe and bar count. Never bar values, account
numbers, keys or record IDs. The history is append-only, as in the review conversations:
thinking blocks are stored as returned and replayed unchanged.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import httpx

from services.agents.commentary import API_URL, API_VERSION, LANGUAGES
from services.review.claude import FALLBACK_BETA, FALLBACK_MODELS, MODELS
from services.review.conversation import BINDING_BETA, ConversationError
from services.review.store import ReviewStore

# Model assignment #SIW-1 (Local Agent session, 2026-10-28): Sonnet at medium effort,
# provisional until the pilot; Opus only when the investor asks for a retry.
DEFAULT_MODEL = "claude-sonnet-5-5"
RETRY_MODEL = "claude-opus-5-5"
EFFORT = "medium"
MAX_RUNS = 6
MAX_STEPS = 12
MAX_TOKENS = 16_000
TIMEOUT_SECONDS = 180.0
MAX_REFERENCE = 20_000
MAX_CODE = 50_000
KEY_PREFIX = "indicator:"

SYSTEM = """You write Pine Script indicators with an investor, for SIW Research: a charting
app with its own Pine interpreter. It runs the subset of Pine v5/v6 described in the
reference below, not all of TradingView's Pine. Rules:
- Write indicator() scripts, never strategy(). Use only what the reference lists. If the
  request needs something unsupported, say so and write the closest supported version.
- Before presenting a script, run it with the run_pine tool. The app runs it on the
  investor's current chart and returns an error with its line and column, or a summary of
  the computed values of each plot and signal. Read the summary: values that are always 0,
  never change or are mostly empty usually mean a mistake. Fix it and run again.
- At most {limit} runs per request. Prefer one careful script over many quick tries.
- Never look ahead: no negative history offsets, no lookahead on current values.
- The investor sees the last script that ran without errors as a preview on the chart and
  decides whether to apply it. Do not say it was applied.
- When it is right, stop calling the tool and answer briefly in {language}: what it shows,
  its inputs and how to read it. Do not repeat the code; the app shows it.
- Describe what the indicator measures; no buy or sell recommendations.

Reference (supported Pine subset):
{reference}"""

TOOLS: list[dict[str, Any]] = [
    {
        "name": "run_pine",
        "description": (
            "Run a complete Pine indicator script on the investor's current chart. Returns "
            "the first error (line and column) or, when it runs, a summary per plot (last "
            "value, minimum, maximum, share of empty bars) and the count of drawn signals."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["code", "explanation"],
            "properties": {
                "code": {"type": "string", "description": "The whole script."},
                "explanation": {
                    "type": "string",
                    "description": "One or two sentences: what this version does or fixes.",
                },
            },
        },
    }
]


def system_prompt(language: str, reference: str) -> str:
    if language not in LANGUAGES:
        raise ConversationError("language must be en or ko")
    return SYSTEM.format(
        language=LANGUAGES[language], limit=MAX_RUNS, reference=reference[:MAX_REFERENCE]
    )


def call_indicator_claude(
    model: str,
    system: str,
    tools: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    api_key: str,
    transport: httpx.BaseTransport | None = None,
    effort: str = EFFORT,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": system,
        "tools": tools,
        "messages": messages,
        "thinking": {
            "type": "adaptive",
            "block_binding": {"prefix_mismatch_behavior": "drop_block"},
        },
        "output_config": {"effort": effort},
        # The reference and history never change; only new turns are added.
        "cache_control": {"type": "ephemeral"},
    }
    betas = [BINDING_BETA]
    if model in FALLBACK_MODELS:
        # A safety decline is retried on a fallback model chosen by the API.
        body["fallbacks"] = "default"
        betas.append(FALLBACK_BETA)
    headers = {
        "x-api-key": api_key,
        "anthropic-version": API_VERSION,
        "anthropic-beta": ",".join(betas),
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


def context_text(context: dict[str, Any]) -> str:
    """The chart and editor as they are now; sent with every request, after the history."""
    code = context.get("code")
    return json.dumps(
        {
            "chart": {
                "symbol": context["symbol"],
                "timeframe": context["timeframe"],
                "bars": context["bars"],
            },
            "editor_code": code[:MAX_CODE] if code else None,
        },
        ensure_ascii=False,
    )


def _utc(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


Call = Callable[[str, str, list[dict[str, Any]], list[dict[str, Any]]], dict[str, Any]]


class IndicatorConversation:
    """Runs one request until Claude answers, pausing whenever a script must be run."""

    def __init__(
        self,
        store: ReviewStore,
        conversation: dict[str, Any],
        call: Call,
        now: Callable[[], datetime],
    ) -> None:
        self.store = store
        self.id = conversation["conversation_id"]
        self.model = conversation["model"]
        self.system = conversation["system"]
        self.state: dict[str, Any] = conversation["state"]
        self.call = call
        self.now = now

    def _save(self) -> None:
        self.store.set_conversation_state(self.id, self.state)

    def _event(self, event: dict[str, Any]) -> None:
        self.store.append_event(self.id, {**event, "at_utc": _utc(self.now())})

    def ask(self, question: str, context: dict[str, Any], model: str | None = None) -> None:
        if self.state.get("status") == "running":
            raise ConversationError("the previous request is still running")
        if model is not None and model not in MODELS:
            raise ConversationError(f"model must be one of {', '.join(MODELS)}")
        chosen = model or self.model
        # A script left unrun (the app was closed or the investor moved on) is answered
        # first, so every tool call keeps its result.
        pending = self.state.get("pending") if self.state.get("status") == "needs_run" else None
        unrun = (
            [
                {
                    "type": "tool_result",
                    "tool_use_id": pending["tool_use_id"],
                    "is_error": True,
                    "content": "Not run: the investor sent a new request instead.",
                },
                *pending.get("others", []),
            ]
            if pending
            else []
        )
        content = [
            *unrun,
            {"type": "text", "text": context_text(context)},
            {"type": "text", "text": question},
        ]
        self.store.append_message(self.id, {"role": "user", "content": content})
        self._event({"type": "question", "text": question, "model": chosen})
        self.state.update(status="running", runs=0, error=None, pending=None, request_model=chosen)
        self._save()
        self._run()

    def add_run(self, tool_use_id: str, ok: bool, error: str | None, summary: str | None) -> None:
        pending = self.state.get("pending")
        if self.state.get("status") != "needs_run" or not pending:
            raise ConversationError("no script run is awaited")
        if pending["tool_use_id"] != tool_use_id:
            raise ConversationError("that is not the awaited script run")
        if ok:
            result: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": f"Ran without errors.\n{summary or ''}".strip(),
            }
        else:
            result = {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "is_error": True,
                "content": f"Error: {error or 'unknown error'}",
            }
        self._event(
            {
                "type": "run",
                "tool_use_id": tool_use_id,
                "ok": ok,
                "text": (summary if ok else error) or "",
            }
        )
        self.store.append_message(
            self.id, {"role": "user", "content": [result, *pending.get("others", [])]}
        )
        if not ok and self.state.get("runs", 0) >= MAX_RUNS:
            # The last allowed run failed: stop here and show it rather than ask again.
            message = f"Claude could not fix the script in {MAX_RUNS - 1} tries"
            self._event({"type": "stopped", "text": message})
            self.state.update(status="stopped", pending=None, error=message)
            self._save()
            return
        self.state.update(status="running", pending=None)
        self._save()
        self._run()

    def _run(self) -> None:
        model = self.state.get("request_model") or self.model
        for _step in range(MAX_STEPS):
            try:
                message = self.call(model, self.system, TOOLS, self.store.messages(self.id))
            except ConversationError as exc:
                self._fail(str(exc))
                return
            content = message["content"]
            self.store.append_message(self.id, {"role": "assistant", "content": content})
            self._count(message.get("usage") or {})
            text = "".join(
                block.get("text", "") for block in content if block.get("type") == "text"
            ).strip()
            stop = message.get("stop_reason")
            if stop == "tool_use":
                if text:
                    self._event({"type": "answer", "text": text, "model": message.get("model")})
                try:
                    if self._tools(content):
                        return  # waiting for the app to run the script
                except ConversationError as exc:
                    self._fail(str(exc))
                    return
                continue
            if stop == "refusal":
                self._fail("Claude declined this request")
                return
            if stop == "max_tokens":
                self._fail("Claude's answer was cut off")
                return
            self._event({"type": "answer", "text": text, "model": message.get("model")})
            self.state.update(status="idle")
            self._save()
            return
        self._fail("Claude kept running scripts without answering")

    def _count(self, usage: dict[str, Any]) -> None:
        # input_tokens counts only uncached input; cache reads and writes are separate.
        for key, field in (
            ("input_tokens", "input_tokens"),
            ("output_tokens", "output_tokens"),
            ("cache_read_tokens", "cache_read_input_tokens"),
            ("cache_write_tokens", "cache_creation_input_tokens"),
        ):
            self.state[key] = self.state.get(key, 0) + int(usage.get(field) or 0)

    def _tools(self, content: list[dict[str, Any]]) -> bool:
        """Takes the turn's first script to run; True when the app must run it."""
        pending: dict[str, Any] | None = None
        others: list[dict[str, Any]] = []
        for block in content:
            if block.get("type") != "tool_use":
                continue
            tool_input = block.get("input") or {}
            code = tool_input.get("code") if isinstance(tool_input, dict) else None
            refusal = None
            if block.get("name") != "run_pine" or not isinstance(code, str) or not code.strip():
                refusal = "Call run_pine with the whole script in `code`."
            elif pending is not None:
                refusal = "Run one script at a time; this one was not run."
            elif self.state.get("runs", 0) >= MAX_RUNS:
                refusal = "The run limit for this request is reached; answer with what you have."
            if refusal is not None:
                others.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block["id"],
                        "is_error": True,
                        "content": refusal,
                    }
                )
                continue
            script = str(code)[:MAX_CODE]
            self.state["runs"] = self.state.get("runs", 0) + 1
            explanation = str(tool_input.get("explanation") or "")
            self._event(
                {
                    "type": "code",
                    "tool_use_id": block["id"],
                    "code": script,
                    "text": explanation[:2000],
                }
            )
            pending = {"tool_use_id": block["id"], "code": script}
        if pending is not None:
            self.state.update(status="needs_run", pending={**pending, "others": others})
            self._save()
            return True
        if not others:
            raise ConversationError("Claude stopped for a tool without calling one")
        self.store.append_message(self.id, {"role": "user", "content": others})
        self._save()
        return False

    def _fail(self, error: str) -> None:
        self._event({"type": "error", "text": error})
        self.state.update(status="failed", error=error, pending=None)
        self._save()
