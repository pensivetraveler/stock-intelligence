"""Technique card schema, citation check, synthetic transcripts and scoring (ADR 0056 §5)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from services.technique_cards import evalset
from services.technique_cards.card_schema import CARD_SCHEMA, CONDITION_FIELDS, validate_card
from services.technique_cards.citations import check_citation, parse_transcript
from services.technique_cards.scoring import THRESHOLDS, matches, score_card, summarise
from services.technique_cards.synthetic import SyntheticError, build

SPECS: list[dict[str, Any]] = json.loads(evalset.SPECS.read_text())


def _objects(schema: dict[str, Any]) -> list[dict[str, Any]]:
    found = []
    if schema.get("type") == "object":
        found.append(schema)
        for child in schema["properties"].values():
            found.extend(_objects(child))
    if schema.get("type") == "array":
        found.extend(_objects(schema["items"]))
    return found


def _card_from(answer: dict[str, Any]) -> dict[str, Any]:
    """The card a perfect model would write: answer texts with the answer's citations."""

    def statement(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "text": item["quote"],
            "basis": "source",
            "citations": [{"locator": item["locator"], "quote": item["quote"]}],
        }

    card: dict[str, Any] = {
        "source_id": answer["source_id"],
        "title": answer["title"],
        "timeframe": [],
        "universe": [],
        "entry": [],
        "exit": [],
        "stop_loss": [],
        "position": [],
        "inexpressible": [statement(item) for item in answer["inexpressible"]],
        "ambiguities": [
            {
                "text": item["quote"],
                "question": "기준이 무엇인가요?",
                "citations": [{"locator": item["locator"], "quote": item["quote"]}],
            }
            for item in answer["ambiguities"]
        ],
    }
    for item in answer["conditions"]:
        card[item["field"]].append(statement(item))
    for item in answer["other"]:
        card[item["field"]].append(statement(item))
    return card


def test_schema_is_strict_everywhere() -> None:
    for item in _objects(CARD_SCHEMA):
        assert item["additionalProperties"] is False
        assert item["required"] == list(item["properties"])


def test_schema_rejects_missing_and_extra_fields() -> None:
    transcript, answer = build(SPECS[0])
    card = _card_from(answer)
    assert validate_card(card) == []
    broken = copy.deepcopy(card)
    del broken["entry"]
    broken["entry_rules"] = []
    errors = validate_card(broken)
    assert any("entry" in error for error in errors)
    assert any("entry_rules" in error for error in errors)


def test_parse_transcript_and_hours() -> None:
    lines = parse_transcript("[00:05] 첫 줄\n이어지는 줄\n[1:02:03] 한 시간 뒤")
    assert [(line.seconds, line.text) for line in lines] == [
        (5, "첫 줄 이어지는 줄"),
        (3723, "한 시간 뒤"),
    ]


def test_citation_window_and_failures() -> None:
    lines = parse_transcript(
        "[00:10] 9일 지수 이평이 EMA\n[00:14] 21을 상향 돌파하면 삽니다\n[00:40] 다른 말"
    )
    assert check_citation(lines, "00:10", "9일 지수 이평이 EMA 21을 상향 돌파하면 삽니다.").ok
    assert check_citation(lines, "00:14", "9일 지수 이평이 EMA 21을").ok  # cited at the second line
    far = check_citation(lines, "00:45", "9일 지수 이평이 EMA")
    assert not far.ok and "not found" in far.reason
    assert not check_citation(lines, "00:05", "9일").ok
    assert not check_citation(lines, "p.3", "9일").ok
    assert not check_citation(lines, "열 초", "9일").ok


@pytest.mark.parametrize("spec", SPECS, ids=[spec["id"] for spec in SPECS])
def test_synthetic_answers_agree_with_their_transcript(spec: dict[str, Any]) -> None:
    transcript, answer = build(spec)
    assert transcript == build(spec)[0]  # same spec, same transcript
    lines = parse_transcript(transcript)
    assert len(answer["ambiguities"]) >= 2
    items = answer["conditions"] + answer["ambiguities"] + answer["inexpressible"] + answer["other"]
    for item in items:
        assert check_citation(lines, item["locator"], item["quote"]).ok, item
        assert matches(item["terms"], item["quote"], item.get("exclude")), item
    for item in answer["conditions"]:
        assert matches(item["terms"], item["text"], item.get("exclude")), item
        # No condition is matched by another condition's words in the same field.
        for other in answer["conditions"]:
            if other is not item and other["field"] == item["field"]:
                assert not matches(item["terms"], other["quote"], item.get("exclude")), (
                    item,
                    other,
                )


def test_synthetic_refuses_too_few_vague_remarks_and_unphrased_rules() -> None:
    spec = copy.deepcopy(SPECS[0])
    spec["ambiguities"] = ["quick_exit"]
    with pytest.raises(SyntheticError):
        build(spec)
    spec = copy.deepcopy(SPECS[0])
    spec["rules"]["entry"] = {"time": {"from": "09:30", "to": "10:00"}}
    with pytest.raises(SyntheticError):
        build(spec)


def test_perfect_card_passes() -> None:
    scores = []
    for spec in SPECS:
        transcript, answer = build(spec)
        scores.append(score_card(_card_from(answer), answer, parse_transcript(transcript)))
    summary = summarise(scores)
    assert summary["pass"], summary
    assert summary["field_rate"] == 1 and summary["citation_rate"] == 1
    assert summary["ambiguity_recall"] == 1 and summary["made_up"] == 0


def test_made_up_misplaced_wrong_direction_and_bad_citation() -> None:
    transcript, answer = build(SPECS[0])
    lines = parse_transcript(transcript)
    card = _card_from(answer)
    entry = card["entry"][0]
    # Wrong direction: the same averages crossing downwards is not the entry condition.
    card["entry"] = [{**entry, "text": "EMA 9가 EMA 21을 하향 돌파하면 매수"}]
    card["exit"].append({**entry, "text": "RSI 14가 80을 넘으면 매도"})  # not in the source
    card["stop_loss"].append({**entry, "text": "흐름이 애매하면 빨리 정리"})  # a vague remark
    card["ambiguities"] = card["ambiguities"][:1]
    card["position"][0]["citations"] = [{"locator": "00:00", "quote": "없는 말"}]
    result = score_card(card, answer, lines)
    assert result.conditions_matched == result.conditions - 1
    assert [text for text in result.made_up if "RSI" in text]
    # The downward cross in entry matches the exit condition, so it is not made up, but the
    # entry condition is missed.
    assert any(text.startswith("entry:") for text in result.missed)
    assert result.misplaced and "애매" in result.misplaced[0]
    assert result.ambiguities_found == 1
    assert result.citations_ok < result.citations
    assert not summarise([result])["pass"]


def test_condition_fields_and_thresholds_match_the_adr() -> None:
    assert CONDITION_FIELDS == ("entry", "exit", "stop_loss")
    assert THRESHOLDS == {
        "made_up": 0,
        "citation_rate": 0.95,
        "field_rate": 0.90,
        "ambiguity_recall": 0.70,
    }


def test_evalset_files_and_scoring_a_report(tmp_path: Path) -> None:
    items = evalset.synthetic(tmp_path)
    full = json.loads((tmp_path / "evalset.json").read_text())
    smoke = json.loads((tmp_path / "smoke.json").read_text())
    answers = json.loads((tmp_path / "answers.json").read_text())
    assert full["data"] == "public" and full["maxTokens"] >= 6000
    assert full["responseSchema"]["strict"] is True
    assert len(full["cases"]) == len(items) == 5 and len(smoke["cases"]) == 1
    first = items[0]
    assert first["transcript"] in full["cases"][0]["prompt"]
    perfect = json.dumps(_card_from(first["answer"]), ensure_ascii=False)
    report = {
        "results": [
            {"caseId": first["source_id"], "model": "m/a", "answer": perfect, "costUsd": 0.1},
            {"caseId": first["source_id"], "model": "m/b", "answer": "not json", "costUsd": 0.05},
        ]
    }
    scored = evalset.score(report, answers)
    assert scored["m/a"]["summary"]["field_rate"] == 1
    assert scored["m/b"]["summary"]["schema_failures"] == 1
    assert not scored["m/b"]["summary"]["pass"]


def test_real_manifest(tmp_path: Path) -> None:
    transcript, answer = build(SPECS[1])
    (tmp_path / "t.txt").write_text(transcript)
    (tmp_path / "a.json").write_text(json.dumps(answer, ensure_ascii=False))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            [{"source_id": "v1", "title": "t", "transcript_path": "t.txt", "answer_path": "a.json"}]
        )
    )
    evalset.real(manifest, tmp_path / "out")
    assert json.loads((tmp_path / "out" / "evalset.json").read_text())["data"] == "private"
    (tmp_path / "t.txt").write_text("no times here")
    with pytest.raises(SystemExit):
        evalset.real(manifest, tmp_path / "out2")
