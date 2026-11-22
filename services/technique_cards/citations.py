"""Citation check (ADR 0056 §5): does the quoted text appear in the source near the cited place?

Three kinds of source, told apart by their text (`parse_source`); each line keeps its position
and the position's unit:
- "time": transcripts, lines of `[mm:ss] text` (Commonplace stores YouTube captions this way).
  A quote passes when, after normalising, it is inside the cited line joined with one line
  before and two after; automatic captions split a sentence across lines.
- "page": PDF text as the library stores it (`[p. N]` before each page, knowledge-library
  pdftext). Cited as `p. N`; the quote must be on that page or the one before or after.
- "para": text a person wrote (a library markdown note). SIW numbers the paragraphs (blank-line
  separated; a heading is a paragraph) and cites `¶N`; the quote must be in that paragraph or
  the one before or after. The numbering is the same each time for the same text.

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
PAGE_LINE = re.compile(r"^\[p\.\s?(\d+)\]\s*$")
PAGE_LOCATOR = re.compile(r"^\s*\[?p\.?\s*(\d+)\]?\s*$", re.IGNORECASE)
PARA_LOCATOR = re.compile(r"^\s*\[?¶\s*(\d+)\]?\s*$")
UNITS = ("time", "page", "para")
NEAR_POSITIONS = 1  # a page or paragraph citation also looks one before and one after
DOT_OUTSIDE_NUMBER = re.compile(r"(?<!\d)\.|\.(?!\d)")
WINDOW_BEFORE = 1
WINDOW_AFTER = 2
WINDOW_SECONDS = 15  # rules v2


@dataclass(frozen=True)
class Line:
    # The position: seconds for a transcript, the page for a PDF, the paragraph for a text.
    seconds: int
    text: str
    unit: str = "time"


def unit_of(lines: list[Line]) -> str:
    return lines[0].unit if lines else "time"


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


def parse_pages(text: str) -> list[Line]:
    """PDF text: every non-blank line under `[p. N]` is a line of page N."""
    lines: list[Line] = []
    page: int | None = None
    for raw in text.splitlines():
        match = PAGE_LINE.match(raw.strip())
        if match:
            page = int(match.group(1))
        elif raw.strip() and page is not None:
            lines.append(Line(page, raw.strip(), "page"))
    return lines


def parse_paragraphs(text: str) -> list[Line]:
    """A text a person wrote: paragraphs (blank-line separated, a heading on its own), from 1."""
    paragraphs: list[str] = []
    current: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            if current:
                paragraphs.append(" ".join(current))
                current = []
            if stripped:
                paragraphs.append(stripped)
            continue
        current.append(stripped)
    if current:
        paragraphs.append(" ".join(current))
    return [Line(n, text, "para") for n, text in enumerate(paragraphs, start=1)]


def source_unit(text: str) -> str:
    """ "time" when the text has `[mm:ss]` lines, "page" when it has `[p. N]` lines, else "para"."""
    lines = text.splitlines()
    if any(LINE.match(line.strip()) for line in lines):
        return "time"
    if any(PAGE_LINE.match(line.strip()) for line in lines):
        return "page"
    return "para"


def parse_source(text: str) -> list[Line]:
    unit = source_unit(text)
    if unit == "time":
        return parse_transcript(text)
    return parse_pages(text) if unit == "page" else parse_paragraphs(text)


def render_source(lines: list[Line]) -> str:
    """Lines back to source text that `parse_source` reads into the same lines."""
    unit = unit_of(lines)
    if unit == "time":
        return "\n".join(f"[{format_time(line.seconds)}] {line.text}" for line in lines) + "\n"
    if unit == "para":
        return "\n\n".join(line.text for line in lines) + "\n"
    out: list[str] = []
    page: int | None = None
    for line in lines:
        if line.seconds != page:
            page = line.seconds
            out.append(f"[p. {page}]")
        out.append(line.text)
    return "\n".join(out) + "\n"


def join_volumes(texts: list[str]) -> tuple[str, list[tuple[int, int]]]:
    """The volumes of a series as one source, numbered on from the volume before.

    Pages, paragraphs or times of volume 2 start after the last of volume 1, so `p. N` names one
    place in the whole series. Returns the joined text and each volume's first and last position.
    Volumes of different kinds (a PDF and a transcript) cannot be joined (ValueError).
    """
    units = {source_unit(text) for text in texts if text.strip()}
    if len(units) > 1:
        raise ValueError("the volumes of the series are different kinds of text")
    joined: list[Line] = []
    ranges: list[tuple[int, int]] = []
    offset = 0
    for text in texts:
        lines = parse_source(text)
        if not lines:
            ranges.append((offset + 1, offset))
            continue
        # A transcript's next volume starts a second after the last line (its 00:00 is a time).
        shift = offset + (1 if lines[0].unit == "time" and joined else 0)
        shifted = [Line(line.seconds + shift, line.text, line.unit) for line in lines]
        joined.extend(shifted)
        ranges.append((shifted[0].seconds, shifted[-1].seconds))
        offset = shifted[-1].seconds
    return (render_source(joined) if joined else ""), ranges


def format_time(seconds: int) -> str:
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def format_locator(unit: str, position: int) -> str:
    if unit == "page":
        return f"p. {position}"
    if unit == "para":
        return f"¶{position}"
    return format_time(position)


def format_transcript(lines: list[Line]) -> str:
    """The source as sent to the model: `[mm:ss] text`, `[¶N] text`, or `[p. N]` before a page."""
    if unit_of(lines) != "page":
        return "\n".join(
            f"[{format_locator(line.unit, line.seconds)}] {line.text}" for line in lines
        )
    out: list[str] = []
    page: int | None = None
    for line in lines:
        if line.seconds != page:
            if out:
                out.append("")
            out.append(f"[p. {line.seconds}]")
            page = line.seconds
        out.append(line.text)
    return "\n".join(out)


def normalise(text: str) -> str:
    """Lower case, NFKC, letters and digits only: punctuation and spacing differ in captions."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    kept = "".join(ch for ch in folded if ch.isalnum() or ch in "%.")
    return DOT_OUTSIDE_NUMBER.sub("", kept)  # keep 2.5, drop a sentence's full stop


def locator_seconds(locator: str) -> int | None:
    match = LOCATOR.match(locator)
    return _seconds(*match.group(1, 2, 3)) if match else None


def locator_position(locator: str, unit: str = "time") -> int | None:
    """The cited position in this unit, or None when the locator is not one."""
    if unit == "time":
        return locator_seconds(locator)
    match = (PAGE_LOCATOR if unit == "page" else PARA_LOCATOR).match(locator)
    return int(match.group(1)) if match else None


@dataclass(frozen=True)
class CitationResult:
    locator: str
    quote: str
    ok: bool
    reason: str


def window_text(lines: list[Line], at: int, seconds: int = WINDOW_SECONDS) -> str:
    """The text of every line starting within `seconds` of `at` (rules v2); for a page or a
    paragraph, the cited one and one on each side."""
    if unit_of(lines) != "time":
        seconds = NEAR_POSITIONS
    return " ".join(line.text for line in lines if at - seconds <= line.seconds <= at + seconds)


def _check_position(lines: list[Line], locator: str, quote: str) -> CitationResult:
    unit = unit_of(lines)
    at = locator_position(locator, unit)
    if at is None:
        return CitationResult(locator, quote, False, f"locator is not {format_locator(unit, 1)}")
    wanted = normalise(quote)
    if not wanted:
        return CitationResult(locator, quote, False, "empty quote")
    if not any(line.seconds == at for line in lines):
        return CitationResult(locator, quote, False, f"{unit} {at} is not in the source")
    if wanted in normalise(window_text(lines, at)):
        return CitationResult(locator, quote, True, "")
    return CitationResult(locator, quote, False, f"quote not found near the cited {unit}")


def check_citation(
    lines: list[Line], locator: str, quote: str, window_seconds: int | None = None
) -> CitationResult:
    """`window_seconds` None: rules v1 (cited line, one before, two after); else a time window.
    A page or paragraph source is checked by position whatever the rules."""
    if unit_of(lines) != "time":
        return _check_position(lines, locator, quote)
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
