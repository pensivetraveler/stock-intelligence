"""The card extraction prompt (ADR 0056 §3), shared by the eval set and, later, the product.

v2 (2026-11-13): v1 let every model put stated-but-unmeasured remarks ("양봉이 힘 있게",
"시장 분위기가 좋을 때") into entry/exit (synthetic eval, misplaced 2-9 per model).
"""

from __future__ import annotations

PROMPT_VERSION = 2

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
- entry, exit and stop_loss hold only conditions a backtest can test as stated (a number, a \
level, an indicator crossing). A stated condition with no measure ("volume should surge", \
"a strong candle", "when it gets near the line", "exit if it looks unclear") goes to \
ambiguities if a number could settle it, otherwise to inexpressible. Never put it in both.
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


# --- v3: structured cards (card schema v2, user decision 2026-11-14) -----------------------

STRUCTURED_PROMPT_VERSION = 3

STRUCTURED_SYSTEM = SYSTEM

STRUCTURED_INSTRUCTIONS = """Write the technique card for the source below as JSON matching the \
schema. Each entry / exit / stop-loss rule is one item of `conditions`, written in slots.

Rules:
- Only rules the source states as part of this technique. Comparisons with other versions, \
backtest variations, parameter sweeps and anecdotes are not rules. If you infer something the \
source does not say, mark it basis "claude_inference".
- Rules stated as "no / never / only" are rules too: long only, no profit target, no stop, \
skip a signal, do not trade when ... — write them as conditions (comparator "none" or "other" \
when they have no comparison).
- Slots: field (entry, exit incl. take profit, stop_loss incl. trailing), side (long, short or \
both), left · comparator · right (for example close cross_above ema period 20; rsi below value \
30; stop_price below entry_price by atr_multiple 2; holding_bars at_or_above bars 3), timeframe \
of the condition or "". Use name "other" and comparator "other" when no slot fits, and say it \
in text. period and value are null unless the source says the number.
- Every number in a condition (period or value) has a value_citation: the number, and the \
locator and exact quote of the line where the source says it. Do not cite one line for a \
number said on another. Numbers said in words count ("fifty", "두 배").
- Every condition and statement has citations: locator is the transcript time of the line \
where the words start (mm:ss as shown, or h:mm:ss), quote is the source's words copied \
exactly, one sentence or less.
- A stated rule with no measure ("volume should surge", "a strong candle", "near the line") is \
not a condition: put it in ambiguities if a number could settle it (give the question to ask \
the user), otherwise in inexpressible. Never in both.
- timeframe, universe, position (size) and inexpressible are short statements with citations. \
Write text in the source's language. Empty lists when absent.
- The transcript may be automatic captions: no punctuation, sentences split across lines, \
mis-heard words.
{part_note}
source_id: {source_id}
title: {title}

Source:
{source}"""


def structured_prompt(
    source_id: str,
    title: str,
    source: str,
    part: tuple[int, int] | None = None,
    span: tuple[str, str, str] | None = None,
) -> str:
    """`part` (k, n) and `span` (from, to, whole length) mark a time slice of a long source."""
    note = ""
    if part is not None:
        where = f" covering {span[0]}-{span[1]} of a {span[2]} source" if span else ""
        note = (
            f"- This is part {part[0]} of {part[1]}{where}, a time slice; write the rules said "
            "in this part only. The parts are merged afterwards. The speaker may revise a rule "
            "said in another part: write what this part says.\n"
        )
    return STRUCTURED_INSTRUCTIONS.format(
        source_id=source_id, title=title, source=source, part_note=note
    )
