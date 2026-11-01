"""Claude researches Pine strategies with the investor (ADR 0042, strategy research step 2).

The loop is the indicator assistant's: the app runs Claude's `run_pine` scripts on the chart
and posts each result back. The difference is what comes back. A run tells Claude only
whether the script ran and how many trades it made, so a mistake can be fixed without
Claude tuning a strategy to its profit on its own. Profit and trades go to Claude only when
the investor sends them, after seeing what will be sent.

What the investor sends: summary figures, how trades crowd into days and the latest trades of
a backtest (times, prices, profit), and how many versions this conversation has tested. Never
bar values, account numbers, keys or record IDs.
"""

from __future__ import annotations

import json
from typing import Any

from services.agents.commentary import LANGUAGES
from services.assistant.indicator import (
    MAX_REFERENCE,
    MAX_RUNS,
    TOOLS,
    IndicatorConversation,
)
from services.review.conversation import ConversationError

# Model assignment #SIW-2 (Local Agent session), provisional until its pilot: Sonnet at high
# effort, because reading a backtest is more than writing code. The investor may retry on
# Opus at high effort; the retry costs more and has no cache.
DEFAULT_MODEL = "claude-sonnet-5-5"
RETRY_MODEL = "claude-opus-5-5"
EFFORT = "high"
MAX_SENT_TRADES = 50
KEY_PREFIX = "strategy:"

SYSTEM = """You research trading strategies with an investor, for SIW Research: a charting
app with its own Pine interpreter. Strategies are backtests on the investor's chart: nothing
here places an order. It runs the subset of Pine v5/v6 described in the reference below, not
all of TradingView's Pine. Rules:
- Write strategy() scripts, never indicator(). Use only what the reference lists. If the idea
  needs something unsupported, say so and write the closest supported version.
- Implement exactly the entry and exit conditions asked for. If you change or add one, say so.
- Before presenting a script, run it with the run_pine tool. The app runs it on the current
  chart and returns an error with its line and column, or says it ran and how many trades it
  made. A strategy with no trades usually has a mistake. Fix it and run again.
- At most {limit} runs per request. Prefer one careful script over many quick tries.
- Never look ahead: no negative history offsets, no lookahead on current values.
- You do not see profit or trades until the investor sends them. Do not guess them, and do
  not claim that a version is better without them.
- The investor sees the last script that ran as a preview on the chart and decides whether to
  add it. Do not say it was added. When it is right, stop calling the tool and answer briefly
  in {language}: its conditions, its inputs and how orders fill. Do not repeat the code.
- When the investor sends results, answer in {language} under these headings: evidence (only
  numbers from what was sent, quoted exactly), interpretation, hypotheses to test, and
  uncertainty. In uncertainty always cover:
  - how far this number of trades can be trusted, plainly when it is small;
  - the risk of overfitting: every version tried on the same bars is tuned to them, so say how
    many versions were tested and that this chart's period alone cannot show the edge is real;
  - fills marked as assumed, whose order inside a bar is not known;
  - concentration: it counts every trade by exchange day (the trade list may be partial). Say
    so when a few days hold most of the trades, quoting the days and the share.
  Never state a number that was not sent. Never compute a new figure without showing how.
- Describe what a strategy did; no recommendation to buy or sell anything.
- The context's "start_from" says where the request starts, when it does:
  - "saved": editor_code is the investor's own strategy. Keep what they did not ask to change.
  - "tradingview": editor_code is TradingView Pine, and detail holds the app's first error for
    it. Keep its behaviour, replace what the reference does not support, and list each
    replacement and any change in behaviour in your answer.

Reference (supported Pine subset):
{reference}"""

STRATEGY_TOOLS: list[dict[str, Any]] = [
    {
        **TOOLS[0],
        "description": (
            "Run a complete Pine strategy() script on the investor's current chart. Returns "
            "the first error (line and column) or, when it runs, its title, inputs and number "
            "of trades. Profit is not returned."
        ),
    }
]


def system_prompt(language: str, reference: str) -> str:
    if language not in LANGUAGES:
        raise ConversationError("language must be en or ko")
    return SYSTEM.format(
        language=LANGUAGES[language], limit=MAX_RUNS, reference=reference[:MAX_REFERENCE]
    )


def results_text(results: dict[str, Any], tested_versions: int) -> str:
    """The backtest as Claude reads it; the numbers it may quote are exactly these."""
    return json.dumps(
        {"backtest_results": results, "versions_tested_in_this_conversation": tested_versions},
        ensure_ascii=False,
    )


class StrategyConversation(IndicatorConversation):
    tools = STRATEGY_TOOLS
    default_effort = EFFORT

    def send_results(self, question: str, context: dict[str, Any], results: dict[str, Any]) -> None:
        tested = sum(
            1 for event in self.store.events(self.id) if event["type"] == "run" and event["ok"]
        )
        self.ask(question, context, extra_text=results_text(results, tested), results=results)
