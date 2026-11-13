"""The card extraction prompt (ADR 0056 §3), shared by the eval set and, later, the product."""

from __future__ import annotations

PROMPT_VERSION = 1

SYSTEM = """You read a trading lesson (a video transcript or a document) and write a technique \
card: the buy and sell conditions the source states, each with its citation. You never invent a \
condition. The card feeds a backtest; a wrong condition costs money, a missing one costs nothing."""

INSTRUCTIONS = """Write the technique card for the source below as JSON matching the schema.

Rules:
- Only conditions the source states. If you infer something the source does not say, mark it \
basis "claude_inference" and keep it out of entry, exit and stop_loss.
- Every sourced statement has at least one citation: locator is the transcript time of the line \
where the words start (mm:ss as shown, or h:mm:ss), quote is the source's words copied exactly, \
one sentence or less. Do not paraphrase inside quote.
- Fields: timeframe (chart period), universe (which stocks), entry (buy conditions), exit \
(sell / take-profit conditions), stop_loss (stop-loss and trailing stops), position (size), \
inexpressible (advice no rule can test, such as market mood or gut feel).
- ambiguities: anything vague the user must settle before a backtest (for example "a bit", \
"strong", "near" with no number). Give the question to ask the user.
- One condition per statement. Write text in the source's language. Empty lists when absent.
- The transcript may be automatic captions: no punctuation, sentences split across lines.

source_id: {source_id}
title: {title}

Source:
{source}"""


def user_prompt(source_id: str, title: str, source: str) -> str:
    return INSTRUCTIONS.format(source_id=source_id, title=title, source=source)
