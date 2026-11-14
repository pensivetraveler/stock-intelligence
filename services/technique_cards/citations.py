"""Citation check (ADR 0056 §5): does the quoted text appear in the source near the cited time?

Transcripts are lines of `[mm:ss] text` (Commonplace stores YouTube captions this way). A quote
passes when, after normalising, it is inside the cited line joined with one line before and two
after; automatic captions split a sentence across lines, and a citation may point at its start.
PDF citations (`p.<page>`) are not checked yet: PDF text comes with knowledge-library-113.

Scoring rules v2 (gateway #SIW-lib-12) use a time window instead: every line starting within
±15 seconds of the cited time. Automatic captions put two lines on one time, so a window of lines
was a few seconds wide; a time window treats automatic and uploaded captions alike.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

LINE = re.compile(r"^\[(?:(\d+):)?(\d{1,2}):(\d{2})\]\s?(.*)$")
LOCATOR = re.compile(r"^\s*\[?(?:(\d+):)?(\d{1,2}):(\d{2})\]?\s*$")
DOT_OUTSIDE_NUMBER = re.compile(r"(?<!\d)\.|\.(?!\d)")
WINDOW_BEFORE = 1
WINDOW_AFTER = 2
WINDOW_SECONDS = 15  # rules v2


@dataclass(frozen=True)
class Line:
    seconds: int
    text: str


def _seconds(hours: str | None, minutes: str, seconds: str) -> int:
    return int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds)


def parse_transcript(text: str) -> list[Line]:
    """The timed lines of a transcript; lines without a time are joined to the line before."""
    lines: list[Line] = []
    for raw in text.splitlines():
        match = LINE.match(raw.strip())
        if match:
            lines.append(Line(_seconds(*match.group(1, 2, 3)), match.group(4).strip()))
        elif raw.strip() and lines:
            last = lines[-1]
            lines[-1] = Line(last.seconds, f"{last.text} {raw.strip()}")
    return lines


def format_time(seconds: int) -> str:
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def format_transcript(lines: list[Line]) -> str:
    return "\n".join(f"[{format_time(line.seconds)}] {line.text}" for line in lines)


def normalise(text: str) -> str:
    """Lower case, NFKC, letters and digits only: punctuation and spacing differ in captions."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    kept = "".join(ch for ch in folded if ch.isalnum() or ch in "%.")
    return DOT_OUTSIDE_NUMBER.sub("", kept)  # keep 2.5, drop a sentence's full stop


def locator_seconds(locator: str) -> int | None:
    match = LOCATOR.match(locator)
    return _seconds(*match.group(1, 2, 3)) if match else None


@dataclass(frozen=True)
class CitationResult:
    locator: str
    quote: str
    ok: bool
    reason: str


def window_text(lines: list[Line], at: int, seconds: int = WINDOW_SECONDS) -> str:
    """The text of every line starting within `seconds` of `at` (rules v2)."""
    return " ".join(line.text for line in lines if at - seconds <= line.seconds <= at + seconds)


def check_citation(
    lines: list[Line], locator: str, quote: str, window_seconds: int | None = None
) -> CitationResult:
    """`window_seconds` None: rules v1 (cited line, one before, two after); else a time window."""
    if locator.strip().lower().startswith("p."):
        return CitationResult(locator, quote, False, "pdf citations are not checked yet")
    at = locator_seconds(locator)
    if at is None:
        return CitationResult(locator, quote, False, "locator is not mm:ss")
    wanted = normalise(quote)
    if not wanted:
        return CitationResult(locator, quote, False, "empty quote")
    if window_seconds is not None:
        if not lines or at < lines[0].seconds - window_seconds:
            return CitationResult(locator, quote, False, "cited time is before the transcript")
        if wanted in normalise(window_text(lines, at, window_seconds)):
            return CitationResult(locator, quote, True, "")
        return CitationResult(locator, quote, False, "quote not found near the cited time")
    # The cited line: the last one starting at or before the cited time.
    index = max((i for i, line in enumerate(lines) if line.seconds <= at), default=None)
    if index is None:
        return CitationResult(locator, quote, False, "cited time is before the transcript")
    window = lines[max(0, index - WINDOW_BEFORE) : index + WINDOW_AFTER + 1]
    if wanted in normalise(" ".join(line.text for line in window)):
        return CitationResult(locator, quote, True, "")
    return CitationResult(locator, quote, False, "quote not found near the cited time")


def check_card(
    card: dict[str, Any], lines: list[Line], window_seconds: int | None = None
) -> list[CitationResult]:
    """Every citation in a card, checked; a sourced statement with no citation fails too."""
    results: list[CitationResult] = []
    for name, value in card.items():
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            citations = item.get("citations") or []
            if not citations and item.get("basis", "source") == "source":
                results.append(CitationResult("", "", False, f"{name}: no citation"))
            for citation in citations:
                results.append(
                    check_citation(
                        lines,
                        str(citation.get("locator", "")),
                        str(citation.get("quote", "")),
                        window_seconds,
                    )
                )
    return results
