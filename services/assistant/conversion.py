"""Claude turns a Pine strategy into a rules strategy (ADR 0042, strategy research step 3).

The app lists the Pine script's conditions from its own parser, so the list does not depend on
Claude. Claude must say, for each one, where it went in the rules or why it cannot go there;
this code then checks that nothing was dropped and nothing was invented. `validate_definition`
only checks the form, and a comparison of two backtests cannot show a condition that never
fired, so this check is what stands against a silently missing condition.

Saving is decided here too (`save_blocks`), by code rather than by trust in a model: assignment
#SIW-2 says the model is not the safeguard. Passing means the two behaved alike on these bars,
not that the strategies are the same.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from collections.abc import Callable, Iterator
from typing import Any

import httpx

from services.agents.commentary import API_URL, API_VERSION, LANGUAGES
from services.backtest.indicators import INDICATORS
from services.backtest.strategy import fingerprint, validate_definition
from services.review.conversation import ConversationError

# Assignment #SIW-2: Opus at high effort. No fallback: a refusal is shown as a failure rather
# than quietly handled by a model nobody has measured on this.
MODEL = "claude-opus-5-5"
EFFORT = "high"
MAX_TOKENS = 16_000
TIMEOUT_SECONDS = 180.0
MAX_ATTEMPTS = 3
MAX_CONDITIONS = 100
SAVE_OPEN = True
# ADR 0042. A smaller sample cannot show that two strategies behave alike: 2026-11-01 user
# decision, both sides at least this many closed trades.
MIN_COMPARISON_TRADES = 20
MAX_TRADES_GAP_PERCENT = 10.0
MAX_NET_GAP_PERCENT = 20.0
MIN_ENTRY_MATCH_PERCENT = 80.0
# The engine's orders expire and Pine's do not, so these never match the script exactly.
UNMATCHED_FIELDS = ("entry_order", "exit_order")

SYSTEM = """You convert a Pine strategy into the rules format of SIW Research's backtest engine
and paper runner. A wrong conversion can run as a different strategy from the one the investor
tested, so never guess. Answer in the required JSON; write `notes` and `reason` in {language}.

The rules format, as JSON in `definition_json`:
{{"type": "rules", "entry": Condition, "exit": Condition or null,
 "sizing": {{"mode": "percent_equity" | "fixed_cash" | "fixed_shares", "value": "..."}},
 "max_positions": 1, "stop_loss_pct": "..." or null, "take_profit_pct": "..." or null,
 "stop_loss_atr": {{"length": 14, "mult": "2"}} (optional, not with stop_loss_pct),
 "trailing_stop": {{"mode": "pct", "value": "5"}} or {{"mode": "atr", "length": 14, "mult": "3"}}
 (optional), "entry_order" and "exit_order": {{"type": "limit" | "stop", "offset_pct": "-1",
 "expire_bars": 1}} (optional; offset from the signal bar's close)}}
Condition: {{"all": [Condition...]}} | {{"any": [Condition...]}} |
 {{"op": "cross_above"|"cross_below"|"gt"|"gte"|"lt"|"lte", "left": Operand, "right": Operand}} |
 {{"time": {{"from": "09:45", "to": "15:30"}}}} (the bar's start in exchange time; from included,
 to excluded; same day; intraday only)
Operand: {{"indicator": id, "params": {{...}}, "output": name, "offset": n}} |
 {{"price": "open"|"high"|"low"|"close"|"volume", "offset": n}} | {{"value": "70"}}
 ("offset" is optional: n bars back, 1 to 500.)

Indicators (id: default params; outputs):
{indicators}

How the engine behaves: long positions only, whole shares. Conditions are read at a bar's
close; market orders fill at the next bar's open. It holds one position per instrument; a new
entry signal while holding does nothing. Stops are percent below the fill or ATR multiples;
there are no partial exits, no short positions, no pyramiding, no values from another
timeframe or symbol, and no variables that carry state across bars.

Rules for `mapping`, one item for every numbered condition the investor's message lists:
- "mapped": the rules do exactly what the condition does. `rule_paths` are JSON pointers into
  your definition, such as "/entry/all/0", "/exit", "/trailing_stop" or "/sizing".
- "unconvertible": the rules cannot do it exactly. Leave it out of the definition, give the
  reason, and leave `rule_paths` empty. Never substitute something close. A short entry, a
  variable carrying state and a partial exit are unconvertible.
- Add nothing the Pine script does not have. Every condition in the definition must be
  reached by a pointer.
- When the entry itself cannot be expressed, set `definition_json` to an empty string."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["definition_json", "mapping", "notes"],
    "properties": {
        "definition_json": {"type": "string"},
        "mapping": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["index", "status", "rule_paths", "reason"],
                "properties": {
                    "index": {"type": "integer"},
                    "status": {"type": "string", "enum": ["mapped", "unconvertible"]},
                    "rule_paths": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                },
            },
        },
        "notes": {"type": "string"},
    },
}


def code_hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _gap_percent(a: float, b: float) -> float:
    base = max(abs(a), abs(b))
    return 0.0 if base == 0 else abs(a - b) / base * 100


def comparison_blocks(
    pine_trades: int, rule_trades: int, pine_net: float, rule_net: float, entry_match: float
) -> list[str]:
    """Why this comparison does not support saving; too few trades is no evidence at all."""
    blocks = []
    if _gap_percent(pine_trades, rule_trades) > MAX_TRADES_GAP_PERCENT:
        blocks.append("trades_gap")
    if _gap_percent(pine_net, rule_net) > MAX_NET_GAP_PERCENT:
        blocks.append("net_gap")
    if entry_match < MIN_ENTRY_MATCH_PERCENT:
        blocks.append("entry_match")
    if min(pine_trades, rule_trades) < MIN_COMPARISON_TRADES:
        blocks.append("too_few_trades")
    return blocks


def save_blocks(
    conversion: dict[str, Any] | None, comparison: dict[str, Any] | None, code: str
) -> list[str]:
    """Everything that stops this script's conversion from being saved; empty when it can be."""
    if conversion is None or conversion.get("definition") is None:
        return ["no_rules"]
    blocks = []
    if (
        not conversion["complete"]
        or conversion["problems"]
        or any(item["status"] != "mapped" for item in conversion["mapping"])
    ):
        blocks.append("conversion_open")
    definition = conversion["definition"]
    if any(definition.get(name) is not None for name in UNMATCHED_FIELDS):
        blocks.append("unmatched_order_terms")
    digest = code_hash(code)
    if conversion.get("code_sha256") != digest:
        blocks.append("code_changed")
    if comparison is None:
        return [*blocks, "no_comparison"]
    if comparison["code_sha256"] != digest or comparison["definition_sha256"] != fingerprint(
        definition
    ):
        blocks.append("comparison_stale")
    return [*blocks, *comparison["blocks"]]


def indicator_catalogue() -> str:
    return "\n".join(
        f"{name}: {json.dumps(spec.params)}; {', '.join(spec.outputs)}"
        for name, spec in sorted(INDICATORS.items())
    )


def system_prompt(language: str) -> str:
    if language not in LANGUAGES:
        raise ConversationError("language must be en or ko")
    return SYSTEM.format(language=LANGUAGES[language], indicators=indicator_catalogue())


def request_text(code: str, conditions: list[str]) -> str:
    listed = "\n".join(f"{index}. {text}" for index, text in enumerate(conditions))
    return f"The Pine strategy:\n{code}\n\nIts conditions, from the app's parser:\n{listed}"


def call_convert_claude(
    system: str,
    messages: list[dict[str, Any]],
    api_key: str,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": system,
        "messages": messages,
        "thinking": {"type": "adaptive"},
        "output_config": {
            "effort": EFFORT,
            "format": {"type": "json_schema", "schema": SCHEMA},
        },
    }
    headers = {"x-api-key": api_key, "anthropic-version": API_VERSION}
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


def _resolves(definition: dict[str, Any], pointer: str) -> bool:
    if not pointer.startswith("/"):
        return False
    node: Any = definition
    for part in pointer[1:].split("/"):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return False
    return node is not None


def _leaves(condition: dict[str, Any] | None, path: str) -> Iterator[str]:
    if condition is None:
        return
    for group in ("all", "any"):
        if group in condition:
            for index, item in enumerate(condition[group]):
                yield from _leaves(item, f"{path}/{group}/{index}")
            return
    yield path


def rule_parts(definition: dict[str, Any]) -> list[str]:
    """Every condition and protective stop in a definition: each must come from the Pine."""
    parts = [*_leaves(definition.get("entry"), "/entry"), *_leaves(definition.get("exit"), "/exit")]
    for name in ("stop_loss_pct", "take_profit_pct", "stop_loss_atr", "trailing_stop"):
        if definition.get(name) is not None:
            parts.append(f"/{name}")
    return parts


def _covers(pointer: str, part: str) -> bool:
    return pointer == part or part.startswith(f"{pointer}/") or pointer.startswith(f"{part}/")


def check_mapping(
    definition: dict[str, Any] | None, conditions: list[str], mapping: list[dict[str, Any]]
) -> list[str]:
    """What stops this conversion from being complete; empty when nothing is unresolved."""
    problems: list[str] = []
    seen: dict[int, dict[str, Any]] = {}
    for item in mapping:
        index = item.get("index")
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or not (0 <= index < len(conditions))
        ):
            problems.append(f"mapping names condition {index}, which does not exist")
        elif index in seen:
            problems.append(f"condition {index} is mapped twice")
        else:
            seen[index] = item
    for index, text in enumerate(conditions):
        entry = seen.get(index)
        if entry is None:
            problems.append(f"condition {index} has no mapping: {text}")
        elif entry["status"] == "unconvertible":
            problems.append(f"condition {index} cannot be converted: {text}")
            if not str(entry.get("reason") or "").strip():
                problems.append(f"condition {index} gives no reason")
        elif not entry.get("rule_paths"):
            problems.append(f"condition {index} is marked mapped but points nowhere: {text}")
        elif definition is not None:
            for pointer in entry["rule_paths"]:
                if not _resolves(definition, str(pointer)):
                    problems.append(f"condition {index} points at {pointer}, which does not exist")
    if definition is None:
        problems.append("there is no rules strategy")
        return problems
    pointers = [
        str(pointer)
        for item in seen.values()
        if item["status"] == "mapped"
        for pointer in item.get("rule_paths") or []
    ]
    for part in rule_parts(definition):
        if not any(_covers(pointer, part) for pointer in pointers):
            problems.append(f"{part} is in the rules but comes from no Pine condition")
    return problems


Call = Callable[[str, list[dict[str, Any]]], dict[str, Any]]


def convert(
    code: str, conditions: list[str], language: str, call: Call
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(the conversion as stored, each reply's usage). A definition that fails validation goes
    back to Claude with the error; a mapping problem does not, it is shown to the investor."""
    system = system_prompt(language)
    messages: list[dict[str, Any]] = [{"role": "user", "content": request_text(code, conditions)}]
    usage: list[dict[str, Any]] = []
    answer: dict[str, Any] = {}
    definition: dict[str, Any] | None = None
    error: str | None = None
    for _attempt in range(MAX_ATTEMPTS):
        message = call(system, messages)
        usage.append(message.get("usage") or {})
        stop = message.get("stop_reason")
        if stop == "refusal":
            raise ConversationError("Claude declined this conversion")
        if stop == "max_tokens":
            raise ConversationError("Claude's answer was cut off")
        content = message["content"]
        text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
        try:
            answer = json.loads(text)
        except ValueError as exc:
            raise ConversationError("Claude's conversion could not be read") from exc
        source = str(answer.get("definition_json") or "").strip()
        definition, error = None, None
        if not source:
            break
        try:
            parsed = validate_definition(json.loads(source))
            if parsed["type"] == "rules":
                definition = parsed
            else:
                error = "type must be rules"
        except ValueError as exc:  # StrategyError and bad JSON alike
            error = str(exc)
        if error is None:
            break
        messages += [
            {"role": "assistant", "content": content},
            {"role": "user", "content": f"That definition is invalid: {error}. Answer again."},
        ]
    mapping = [
        {
            "index": item.get("index"),
            "condition": (
                conditions[item["index"]]
                if isinstance(item.get("index"), int) and 0 <= item["index"] < len(conditions)
                else ""
            ),
            "status": item.get("status"),
            "rule_paths": [str(pointer) for pointer in item.get("rule_paths") or []],
            "reason": str(item.get("reason") or ""),
        }
        for item in answer.get("mapping") or []
        if item.get("status") in ("mapped", "unconvertible")
    ]
    problems = check_mapping(definition, conditions, mapping)
    if error is not None:
        problems.insert(0, f"the rules are invalid: {error}")
    return (
        {
            "code_sha256": code_hash(code),
            "definition": definition,
            "mapping": mapping,
            "problems": [problem[:1000] for problem in problems],
            "notes": str(answer.get("notes") or "")[:8000],
            "complete": not problems,
            "save_open": SAVE_OPEN,
        },
        usage,
    )
