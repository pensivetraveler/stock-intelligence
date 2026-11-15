"""PDFs and written texts as technique sources (user request 2026-11-15)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from services.technique_cards import evalset
from services.technique_cards.checks import check_card, timeline
from services.technique_cards.citations import (
    check_citation,
    format_transcript,
    parse_source,
    source_unit,
)
from services.technique_cards.extract import prompts_for
from services.technique_cards.library import Library, LibraryError
from tests.technique_cards.test_card_pipeline import ITEM, FakeCli, _condition, _config, _op

PDF_TEXT = """[p. 1]
Turtle rules, a summary

[p. 2]
Buy when the price breaks the 20-day high.
Sell when it breaks the 10-day low.

[p. 3]

[p. 4]
Risk one percent of the account on each trade.
"""

NOTE_TEXT = """# My breakout rule

Buy when the close is above the 50-day moving average
and RSI is under 70.

Exit when the close falls below the 50-day average.
"""


def test_a_pdf_is_cited_by_page_and_checked_near_it() -> None:
    lines = parse_source(PDF_TEXT)
    assert source_unit(PDF_TEXT) == "page"
    assert [line.seconds for line in lines] == [1, 2, 2, 4]  # page 3 has no text
    assert format_transcript(lines).startswith("[p. 1]\nTurtle rules, a summary\n\n[p. 2]\n")
    assert check_citation(lines, "p. 2", "breaks the 20-day high").ok
    assert check_citation(lines, "p.1", "breaks the 20-day high").ok  # one page off
    far = check_citation(lines, "p. 4", "breaks the 20-day high")
    assert not far.ok and "near the cited page" in far.reason
    assert check_citation(lines, "p. 9", "x").reason == "page 9 is not in the source"
    assert check_citation(lines, "00:10", "x").reason == "locator is not p. 1"


def test_a_written_text_is_cited_by_paragraph() -> None:
    lines = parse_source(NOTE_TEXT)
    assert source_unit(NOTE_TEXT) == "para"
    assert [(line.seconds, line.text[:12]) for line in lines] == [
        (1, "# My breakou"),
        (2, "Buy when the"),
        (3, "Exit when th"),
    ]
    assert "and RSI is under 70" in lines[1].text  # a wrapped paragraph is one paragraph
    assert format_transcript(lines).splitlines()[1].startswith("[¶2] Buy when")
    assert check_citation(lines, "¶2", "RSI is under 70").ok
    assert not check_citation(lines, "2", "RSI is under 70").ok


def test_captions_still_parse_as_times() -> None:
    lines = parse_source("[00:05] buy the breakout\n[00:10] sell the low")
    assert [line.unit for line in lines] == ["time", "time"]


def test_a_long_document_is_cut_at_page_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(evalset, "SPLIT_OVER_CHARS", 50)
    monkeypatch.setattr(evalset, "SLICE_CHARS", 40)
    text = "\n".join(f"[p. {n}]\n{'word ' * 6}" for n in range(1, 7))
    parts = evalset.split_parts(parse_source(text))
    assert len(parts) > 1
    for before, after in zip(parts, parts[1:], strict=False):
        assert after[0].seconds == before[-1].seconds  # the edge page opens the next slice
    assert {line.seconds for part in parts for line in part} == set(range(1, 7))


def test_the_prompt_says_how_a_pdf_is_cited() -> None:
    prompt = prompts_for("p-1", "Turtle", parse_source(PDF_TEXT))[0]
    assert "the page the words are on" in prompt and "[p. 2]" in prompt
    assert "automatic captions" not in prompt
    note = prompts_for("m-1", "Mine", parse_source(NOTE_TEXT))[0]
    assert "paragraph number" in note and "[¶3]" in note


def test_checks_and_timeline_work_on_pages() -> None:
    lines = parse_source(PDF_TEXT)
    condition = _condition(
        text="Buy above the 20-day high",
        comparator="above",
        right=_op("price", "highest_high", period=20),
        citations=[{"locator": "p. 2", "quote": "Buy when the price breaks the 20-day high"}],
        value_citations=[{"value": 20, "locator": "p. 2", "quote": "the 20-day high"}],
    )
    card = {"conditions": [condition], "universe": [], "ambiguities": [], "inexpressible": []}
    checked = check_card(card, lines)
    assert checked["conditions"][0]["flags"] == []
    rows = timeline(card, lines)
    assert [row["time"] for row in rows] == ["p. 1", "p. 2", "p. 2", "p. 4"]
    assert [row["conditions"] for row in rows] == [[], [0], [0], []]


class LayeredCli(FakeCli):
    """An item whose hydrated layers are given; `archive get` says each layer's kind."""

    def __init__(self, layers: dict[str, tuple[bytes, str, str]]) -> None:
        super().__init__()
        self.layers = layers  # name -> (bytes, kind, origin_type)

    def __call__(self, args: list[str], request: dict[str, Any]) -> dict[str, Any]:
        ok = {"status": "success", "reason_code": "OK"}
        if args[:2] == ["context", "hydrate"]:
            self.calls.append((args, request))
            out = Path(args[args.index("--out-dir") + 1])
            for name, (data, _kind, _origin) in self.layers.items():
                (out / name).write_bytes(data)
                (out / f"{name}.meta.json").write_text(
                    json.dumps({"revision_ref": f"kl://kl-lib/revision/{name}"})
                )
            return ok | {"data": {"delivered_items": []}}
        if args[:2] == ["archive", "get"]:
            self.calls.append((args, request))
            name = request["target"]["revision_ref"].rsplit("/", 1)[1]
            _data, kind, origin = self.layers[name]
            return ok | {"data": {"kind": kind, "origin_type": origin}}
        return super().__call__(args, request)


def test_the_library_keeps_a_pdfs_text_layer(tmp_path: Path) -> None:
    cli = LayeredCli(
        {
            "META": (json.dumps({"title": "Turtle"}).encode(), "raw", "observed"),
            "PDF": (b"%PDF-1.7 binary", "raw", "observed"),
            "TEXT": (PDF_TEXT.encode(), "transcript", "transformed"),
            "SUM": (b"A summary of the turtle rules.", "summary", "inferred"),
        }
    )
    fetched = Library(_config(), tmp_path, cli).fetch(ITEM)
    assert fetched.revision_ref == "kl://kl-lib/revision/TEXT"
    assert fetched.path.read_text() == PDF_TEXT
    assert not any(args[:2] == ["archive", "get"] for args, _ in cli.calls)


def test_the_library_keeps_a_written_note_and_never_its_summary(tmp_path: Path) -> None:
    long_summary = ("A much longer summary of the note. " * 20).encode()
    cli = LayeredCli(
        {
            "META": (json.dumps({"title": "Mine"}).encode(), "raw", "observed"),
            "NOTE": (NOTE_TEXT.encode(), "raw", "observed"),
            "SUM": (long_summary, "summary", "inferred"),
            "AI": (b"What the model thinks it means.", "interpretation", "inferred"),
        }
    )
    fetched = Library(_config(), tmp_path, cli).fetch(ITEM)
    assert fetched.revision_ref == "kl://kl-lib/revision/NOTE"
    assert parse_source(fetched.path.read_text())[1].text.startswith("Buy when")


def test_an_item_with_only_a_summary_is_refused(tmp_path: Path) -> None:
    cli = LayeredCli({"SUM": (b"Only a summary.", "summary", "inferred")})
    with pytest.raises(LibraryError, match="no captions, PDF text or written text"):
        Library(_config(), tmp_path, cli).fetch(ITEM)
    assert not (tmp_path / "hydrated").exists()
