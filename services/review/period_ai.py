"""Claude on a period review (ADR 0042, step 4b).

What is sent: the saved statistics (without the curve and calendar), the investor's
current principles, and the 10 best and 10 worst trades of each currency with their
tags, broken principles and the investor's notes (marked as written after the trade).
No account numbers, keys or record IDs.

The commentary is structured output: summary, repeated mistakes, what went well, what to
focus on next, and principle suggestions (add, change or remove), each with the numbers
behind it. Suggestions are only proposals; the investor picks which to apply.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx

from services.agents.commentary import API_URL, API_VERSION, LANGUAGES
from services.review.claude import (
    DEFAULT_MODEL,
    FALLBACK_BETA,
    FALLBACK_MODELS,
    MODELS,
    TIMEOUT_SECONDS,
    ReportError,
)
from services.review.period import Row, _holding_bucket, _symbol, _time_bucket
from services.review.principles import KINDS
from services.review.tools import ToolInputError, ToolResult

TOP_TRADES = 10
TOOL_LIMIT = 30

PERIOD_SYSTEM = """You review one investor's trading over a period, using ONLY what is given:
the statistics (per currency, against the period before), their trading principles, and
the best and worst trades with their notes. Rules:
- Every point must follow from the given numbers or trades; cite the numbers. No outside
  knowledge about companies, news or later prices.
- "what_if" totals and anything marked hindsight were not knowable at the time; say so.
- Notes were written after the trades: treat them as recollection.
- Principle suggestions: propose adding, changing or removing items only when the numbers
  support it, and give the numbers in "reason". Use the given item_id for change/remove.
  Numeric kinds: stop_loss_pct, take_profit_pct, max_position_pct, no_entry_first_minutes,
  max_gap_chase_pct, max_trades_per_day; anything else is kind "text" with value null.
- No buy, sell or hold recommendations about any stock, no price targets. Not advice.
- Be concise and concrete. Answer in {language}."""

PERIOD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "summary",
        "repeated_mistakes",
        "went_well",
        "focus_next",
        "principle_suggestions",
        "uncertainty",
    ],
    "properties": {
        "summary": {"type": "string"},
        "repeated_mistakes": {"type": "array", "items": {"type": "string"}},
        "went_well": {"type": "array", "items": {"type": "string"}},
        "focus_next": {"type": "array", "items": {"type": "string"}},
        "principle_suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["action", "item_id", "scope", "kind", "value", "text", "reason"],
                "properties": {
                    "action": {"type": "string", "enum": ["add", "change", "remove"]},
                    "item_id": {"type": ["string", "null"]},
                    "scope": {"type": "string"},
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "value": {"type": ["number", "null"]},
                    "text": {"type": "string"},
                    "reason": {"type": "string"},
                },
            },
        },
        "uncertainty": {"type": "string"},
    },
}


def trade_summary(row: Row, note: Mapping[str, Any] | None) -> dict[str, Any]:
    """One trade as Claude sees it: no record ids or accounts."""
    broken = [
        r.get("text") or r["kind"]
        for r in (row.check or {}).get("results", [])
        if r["status"] == "broken"
    ]
    return {
        "symbol": _symbol(row.instrument_id),
        "opened_on": row.opened_on.isoformat(),
        "time_of_day": _time_bucket(row),
        "closed_on": row.closed_on.isoformat(),
        "holding": _holding_bucket(row),
        "net_pnl": format(row.net.normalize(), "f"),
        "peak_cost": format(row.peak.quantize(Decimal("0.01")), "f"),
        "tags": row.tags,
        "principles_broken": broken if row.check is not None else None,
        "note_written_after_the_trade": (note or {}).get("note") or None,
    }


def best_and_worst(
    rows: Sequence[Row], notes: Mapping[str, Mapping[str, Any]]
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    by_currency: dict[str, list[Row]] = {}
    for row in rows:
        by_currency.setdefault(row.currency, []).append(row)
    out = {}
    for currency, members in sorted(by_currency.items()):
        ordered = sorted(members, key=lambda r: r.net)
        out[currency] = {
            "best": [trade_summary(r, notes.get(r.trade_id)) for r in ordered[::-1][:TOP_TRADES]],
            "worst": [trade_summary(r, notes.get(r.trade_id)) for r in ordered[:TOP_TRADES]],
        }
    return out


def period_payload(
    review: Mapping[str, Any],
    principles: Sequence[Mapping[str, Any]],
    trades: Mapping[str, Any],
) -> dict[str, Any]:
    """The text sent to Claude; the screen shows it before anything is sent."""
    sections = [
        {k: v for k, v in section.items() if k not in ("curve", "daily")}
        for section in review["sections"]
    ]
    return {
        "period": {"from": review["from_date"], "to": review["to_date"]},
        "previous_period": {
            "from": review["previous_from_date"],
            "to": review["previous_to_date"],
        },
        "filters": {
            "ledger": review["filters"]["ledger"],
            "one_account_only": review["filters"]["account_ref"] is not None,
            "strategy": review["filters"]["strategy_id"],
        },
        "statistics": sections,
        "principles": [
            {k: item[k] for k in ("item_id", "scope", "kind", "value", "text")}
            for item in principles
        ],
        "best_and_worst_trades": trades,
    }


def _strings(value: object, limit: int = 10) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item)[:1_000] for item in value if isinstance(item, str) and item.strip()][:limit]


def ask_period_commentary(
    payload: dict[str, Any],
    api_key: str,
    language: str,
    model: str = DEFAULT_MODEL,
    transport: httpx.BaseTransport | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    if language not in LANGUAGES:
        raise ReportError("language must be en or ko")
    if model not in MODELS:
        raise ReportError(f"model must be one of {', '.join(MODELS)}")
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": 16_000,
        "system": PERIOD_SYSTEM.format(language=LANGUAGES[language]),
        "messages": [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        "output_config": {"format": {"type": "json_schema", "schema": PERIOD_SCHEMA}},
    }
    headers = {"x-api-key": api_key, "anthropic-version": API_VERSION}
    if model in FALLBACK_MODELS:
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
            raise ReportError("Claude declined to review this period")
        if message.get("stop_reason") == "max_tokens":
            raise ReportError("Claude's answer was cut off; try again")
        text = "".join(
            block.get("text", "") for block in message["content"] if block.get("type") == "text"
        )
        parsed = json.loads(text)
    except ReportError:
        raise
    except (ValueError, KeyError, TypeError) as exc:
        raise ReportError("Claude did not answer in the required format") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("summary"), str):
        raise ReportError("Claude did not answer in the required format")
    suggestions = []
    for item in parsed.get("principle_suggestions") or []:
        if not isinstance(item, dict) or item.get("action") not in ("add", "change", "remove"):
            continue
        suggestions.append(
            {
                "action": item["action"],
                "item_id": None if item.get("item_id") in (None, "") else str(item["item_id"])[:20],
                "scope": str(item.get("scope") or "shared")[:200],
                "kind": str(item.get("kind") or "text") if item.get("kind") in KINDS else "text",
                "value": None if item.get("value") is None else str(item["value"])[:40],
                "text": str(item.get("text") or "")[:500],
                "reason": str(item.get("reason") or "")[:1_000],
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
        "summary": parsed["summary"][:3_000],
        "repeated_mistakes": _strings(parsed.get("repeated_mistakes")),
        "went_well": _strings(parsed.get("went_well")),
        "focus_next": _strings(parsed.get("focus_next")),
        "principle_suggestions": suggestions[:10],
        "uncertainty": str(parsed.get("uncertainty") or "")[:2_000],
        "usage": {
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
        },
    }


# --- follow-up questions -------------------------------------------------------------

PERIOD_TOOLS: list[dict[str, Any]] = [
    {
        "name": "period_trades",
        "description": (
            "Trades closed in this review's period, filtered by one group as in the "
            "statistics (e.g. group 'tag' key 'breakout', group 'time_of_day' key 'pre'), "
            "sorted and limited. Returns symbol, dates, P&L, tags, broken principles and "
            f"the investor's notes. At most {TOOL_LIMIT} trades."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "currency": {"type": "string", "enum": ["USD", "KRW"]},
                "group": {
                    "type": "string",
                    "enum": ["all", "tag", "ticker", "time_of_day", "weekday", "holding"],
                },
                "key": {"type": "string"},
                "sort": {"type": "string", "enum": ["worst", "best", "earliest", "latest"]},
                "limit": {"type": "integer"},
            },
            "required": ["currency", "group"],
        },
    }
]


class PeriodTools:
    """The read-only `period_trades` tool over one period's closed trades."""

    def __init__(self, rows: Sequence[Row], notes: Mapping[str, Mapping[str, Any]]) -> None:
        self.rows = rows
        self.notes = notes

    def run(self, name: str, tool_input: Any) -> ToolResult:
        if name != "period_trades":
            raise ToolInputError(f"unknown tool {name}")
        from services.review.period import WEEKDAYS

        currency = tool_input.get("currency")
        group = tool_input.get("group", "all")
        key = str(tool_input.get("key") or "")
        keys: dict[str, Callable[[Row], list[str]]] = {
            "all": lambda r: [""],
            "tag": lambda r: r.tags or ["(none)"],
            "ticker": lambda r: [_symbol(r.instrument_id)],
            "time_of_day": lambda r: [_time_bucket(r)],
            "weekday": lambda r: [WEEKDAYS[r.opened_on.weekday()]],
            "holding": lambda r: [_holding_bucket(r)],
        }
        if group not in keys:
            raise ToolInputError(f"group must be one of {', '.join(keys)}")
        chosen = [
            r
            for r in self.rows
            if r.currency == currency and (group == "all" or key in keys[group](r))
        ]
        sort = tool_input.get("sort", "worst")
        orders: dict[str, Callable[[Row], Any]] = {
            "worst": lambda r: r.net,
            "best": lambda r: -r.net,
            "earliest": lambda r: (r.closed_on, r.closed_at or datetime.min),
            "latest": lambda r: (-r.closed_on.toordinal(),),
        }
        if sort not in orders:
            raise ToolInputError("sort must be worst, best, earliest or latest")
        try:
            limit = max(1, min(TOOL_LIMIT, int(tool_input.get("limit", 10))))
        except (TypeError, ValueError) as exc:
            raise ToolInputError("limit must be a number") from exc
        picked = sorted(chosen, key=orders[sort])[:limit]
        total = sum((r.net for r in chosen), Decimal(0))
        data = {
            "matched": len(chosen),
            "net_pnl_of_matched": format(total.normalize(), "f"),
            "trades": [trade_summary(r, self.notes.get(r.trade_id)) for r in picked],
        }
        return ToolResult(
            f"{currency} {group}{'=' + key if key else ''}: {len(chosen)} trades, "
            f"{len(picked)} listed",
            data=data,
        )
