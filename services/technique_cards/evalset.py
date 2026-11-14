"""Build the card-extraction eval set and score the gateway's report (ADR 0056 §5, #SIW-lib-4).

    python -m services.technique_cards.evalset synthetic --out-dir data/technique_eval/synthetic
    python -m services.technique_cards.evalset real --manifest m.json --out-dir <dir>
    python -m services.technique_cards.evalset score --report <report.json> --answers <answers.json>
        [--rules 1|2|3] [--without-post-hoc]
    python -m services.technique_cards.evalset real --structured ...   (card schema v2, rules 3)

`synthetic` writes `evalset.json` (data public), `smoke.json` (one case, for the strict-schema
HTTP 400 check) and `answers.json`. `real` does the same for source transcripts and hand-checked
answers listed in a manifest; its files are private and stay under the gitignored `data/`.
The gateway runs `local-ai-gateway models eval` on the eval set; `score` reads its report
(`results[].answer`) and compares each answer with the answer card. Nothing here calls a model.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from services.technique_cards.card_schema import response_schema
from services.technique_cards.citations import Line, format_transcript, parse_transcript
from services.technique_cards.prompt import (
    PROMPT_VERSION,
    STRUCTURED_PROMPT_VERSION,
    STRUCTURED_SYSTEM,
    SYSTEM,
    structured_prompt,
    user_prompt,
)
from services.technique_cards.scoring import (
    SCORING_RULES_VERSION,
    CardScore,
    score_card,
    summarise,
)
from services.technique_cards.structured_schema import structured_response_schema
from services.technique_cards.structured_scoring import (
    SCORING_RULES_VERSION_STRUCTURED,
    merge_parts,
    score_structured,
)
from services.technique_cards.synthetic import build

SPECS = Path(__file__).with_name("synthetic_specs.json")
MAX_TOKENS = 8000  # gateway: at least 6000
STRUCTURED_MAX_TOKENS = 16000  # gateway #SIW-lib-14: 8000 was cut off; with splitting, 16000
# Long sources are split into time slices (gateway #SIW-lib-14/16): over 30 minutes of
# transcript, 20-minute slices with 30 seconds of overlap, one case each, merged when scored.
SPLIT_OVER_SECONDS = 1800
SLICE_SECONDS = 1200
OVERLAP_SECONDS = 30
PART_MARK = "#p"


def split_parts(lines: list[Line]) -> list[list[Line]]:
    """The time slices of a transcript; one slice when it is 30 minutes or shorter."""
    if not lines or lines[-1].seconds <= SPLIT_OVER_SECONDS:
        return [lines]
    end = lines[-1].seconds
    count = -(-end // SLICE_SECONDS)  # ceiling
    size = -(-end // count)
    parts = []
    for k in range(count):
        start, stop = k * size, (k + 1) * size
        parts.append(
            [
                line
                for line in lines
                if start - OVERLAP_SECONDS <= line.seconds < stop + OVERLAP_SECONDS
            ]
        )
    return [part for part in parts if part]


def base_id(case_id: str) -> str:
    return case_id.split(PART_MARK, 1)[0]


def _case(source_id: str, title: str, transcript: str) -> dict[str, Any]:
    return {
        "id": source_id,
        "prompt": user_prompt(source_id, title, transcript),
        "checks": [
            {"type": "json"},
            {"type": "json_path", "path": "source_id", "equals": source_id},
        ],
    }


def eval_set(name: str, data: str, cases: list[dict[str, Any]], note: str) -> dict[str, Any]:
    return {
        "name": name,
        "data": data,
        "note": note,
        "system": SYSTEM,
        "maxTokens": MAX_TOKENS,
        "responseSchema": response_schema(),
        "cases": cases,
    }


def _write(out_dir: Path, name: str, data: str, items: list[dict[str, Any]], note: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    cases = [_case(item["source_id"], item["title"], item["transcript"]) for item in items]
    answers = {
        item["source_id"]: {"answer": item["answer"], "transcript": item["transcript"]}
        for item in items
    }
    full = eval_set(name, data, cases, note)
    smoke = eval_set(f"{name}-smoke", data, cases[:1], f"{note} 스키마 확인용 1건.")
    for file, content in (("evalset.json", full), ("smoke.json", smoke), ("answers.json", answers)):
        (out_dir / file).write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n")


def synthetic(out_dir: Path) -> list[dict[str, Any]]:
    items = []
    for spec in json.loads(SPECS.read_text()):
        transcript, answer = build(spec)
        items.append(
            {
                "source_id": spec["id"],
                "title": spec["title"],
                "transcript": transcript,
                "answer": answer,
            }
        )
    note = (
        f"SIW 기법 카드 추출(ADR 0056) 합성 자료 — 규칙 빌더 전략에서 템플릿으로 만든 한국어 자막, "
        f"실제 사람·영상 아님. 프롬프트 v{PROMPT_VERSION}."
    )
    _write(out_dir, "siw-technique-cards-synthetic", "public", items, note)
    return items


def _structured_cases(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cases = []
    for item in items:
        parts = split_parts(parse_transcript(item["transcript"]))
        for k, part in enumerate(parts, start=1):
            case_id = item["source_id"] if len(parts) == 1 else f"{item['source_id']}{PART_MARK}{k}"
            cases.append(
                {
                    "id": case_id,
                    "prompt": structured_prompt(
                        item["source_id"],
                        item["title"],
                        format_transcript(part),
                        None if len(parts) == 1 else (k, len(parts)),
                    ),
                    "checks": [
                        {"type": "json"},
                        {"type": "json_path", "path": "source_id", "equals": item["source_id"]},
                    ],
                }
            )
    return cases


def write_structured(
    out_dir: Path, name: str, data: str, items: list[dict[str, Any]], note: str
) -> None:
    """Eval set for structured cards (card schema v2, prompt v3, rules v3)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cases = _structured_cases(items)

    def eval_file(set_name: str, chosen: list[dict[str, Any]], set_note: str) -> dict[str, Any]:
        return {
            "name": set_name,
            "data": data,
            "note": set_note,
            "system": STRUCTURED_SYSTEM,
            "maxTokens": STRUCTURED_MAX_TOKENS,
            "responseSchema": structured_response_schema(),
            "cases": chosen,
        }

    answers = {
        item["source_id"]: {"answer": item["answer"], "transcript": item["transcript"]}
        for item in items
    }
    smoke = [min(cases, key=lambda c: len(c["prompt"]))]  # the shortest: a schema check
    files = (
        ("evalset.json", eval_file(name, cases, note)),
        ("smoke.json", eval_file(f"{name}-smoke", smoke, f"{note} 스키마 확인용 1건.")),
        ("answers.json", answers),
    )
    for file, content in files:
        (out_dir / file).write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n")


def real(manifest: Path, out_dir: Path, structured: bool = False) -> list[dict[str, Any]]:
    """Manifest: [{source_id, title, transcript_path, answer_path}], paths relative to it."""
    base = manifest.parent
    items = []
    for entry in json.loads(manifest.read_text()):
        transcript = (base / entry["transcript_path"]).read_text()
        if not parse_transcript(transcript):
            raise SystemExit(f"{entry['source_id']}: transcript has no [mm:ss] lines")
        answer = json.loads((base / entry["answer_path"]).read_text())
        items.append(
            {
                "source_id": entry["source_id"],
                "title": entry["title"],
                "transcript": transcript,
                "answer": answer,
            }
        )
    if structured:
        note = (
            "SIW 구조화 기법 카드(ADR 0056) 실제 자료 — 제3자 저작물, 재배포 금지. "
            f"프롬프트 v{STRUCTURED_PROMPT_VERSION}, 카드 스키마 v2, 30분 넘으면 구간 분할."
        )
        write_structured(out_dir, "siw-technique-cards-structured", "private", items, note)
        return items
    note = (
        "SIW 기법 카드 추출(ADR 0056) 실제 자료 — 제3자 저작물, 재배포 금지. "
        f"프롬프트 v{PROMPT_VERSION}."
    )
    _write(out_dir, "siw-technique-cards-real", "private", items, note)
    return items


def _answer_json(text: str) -> object:
    fenced = text.split("```")
    candidate = fenced[1].removeprefix("json") if len(fenced) >= 3 else text
    return json.loads(candidate.strip())


def score(
    report: dict[str, Any],
    answers: dict[str, Any],
    rules: int = SCORING_RULES_VERSION,
    include_post_hoc: bool = True,
) -> dict[str, Any]:
    by_model: dict[str, list[CardScore]] = {}
    costs: dict[str, float] = {}
    if rules == SCORING_RULES_VERSION_STRUCTURED:
        return _score_structured(report, answers, include_post_hoc)
    for result in report.get("results", []):
        case = answers.get(result["caseId"])
        if case is None:
            continue
        lines = parse_transcript(case["transcript"])
        text = result.get("answer")
        try:
            card: object = _answer_json(text) if isinstance(text, str) else None
        except json.JSONDecodeError:
            card = None
        item = score_card(card, case["answer"], lines, rules, include_post_hoc)
        if card is None:
            item.schema_errors = ["no JSON answer"]
        by_model.setdefault(result["model"], []).append(item)
        costs[result["model"]] = costs.get(result["model"], 0.0) + float(result.get("costUsd") or 0)
    return {
        model: {
            "summary": summarise(items, rules),
            "cost_usd": round(costs[model], 6),
            "cards": [item.as_dict() for item in items],
        }
        for model, items in by_model.items()
    }


def _score_structured(
    report: dict[str, Any], answers: dict[str, Any], include_post_hoc: bool
) -> dict[str, Any]:
    """Rules v3: a long source's slices are merged into one card before scoring."""
    parts: dict[tuple[str, str], list[tuple[str, object]]] = {}
    costs: dict[str, float] = {}
    for result in report.get("results", []):
        source = base_id(result["caseId"])
        if source not in answers:
            continue
        text = result.get("answer")
        try:
            card: object = _answer_json(text) if isinstance(text, str) else None
        except json.JSONDecodeError:
            card = None
        parts.setdefault((result["model"], source), []).append((result["caseId"], card))
        costs[result["model"]] = costs.get(result["model"], 0.0) + float(result.get("costUsd") or 0)
    by_model: dict[str, list[CardScore]] = {}
    for (model, source), cards in sorted(parts.items()):
        ordered = [card for _, card in sorted(cards, key=lambda pair: pair[0])]
        merged = ordered[0] if len(ordered) == 1 else merge_parts(ordered, source)
        case = answers[source]
        item = score_structured(
            merged, case["answer"], parse_transcript(case["transcript"]), include_post_hoc
        )
        if merged is None:
            item.schema_errors = ["no JSON answer (or a part missing)"]
        by_model.setdefault(model, []).append(item)
    return {
        model: {
            "summary": summarise(items, SCORING_RULES_VERSION_STRUCTURED),
            "cost_usd": round(costs[model], 6),
            "cards": [item.as_dict() for item in items],
        }
        for model, items in by_model.items()
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m services.technique_cards.evalset")
    sub = parser.add_subparsers(dest="command", required=True)
    syn = sub.add_parser("synthetic")
    syn.add_argument("--out-dir", type=Path, required=True)
    rl = sub.add_parser("real")
    rl.add_argument("--manifest", type=Path, required=True)
    rl.add_argument("--out-dir", type=Path, required=True)
    rl.add_argument(
        "--structured", action="store_true", help="card schema v2, prompt v3, split long sources"
    )
    sc = sub.add_parser("score")
    sc.add_argument("--report", type=Path, required=True)
    sc.add_argument("--answers", type=Path, nargs="+", required=True)
    sc.add_argument("--rules", type=int, choices=(1, 2, 3), default=SCORING_RULES_VERSION)
    sc.add_argument(
        "--without-post-hoc",
        action="store_true",
        help="score against the answer as first written (leave out items marked post_hoc)",
    )
    args = parser.parse_args(argv)
    if args.command == "synthetic":
        items = synthetic(args.out_dir)
        print(f"{len(items)} synthetic cases -> {args.out_dir}")
    elif args.command == "real":
        items = real(args.manifest, args.out_dir, args.structured)
        print(f"{len(items)} real cases -> {args.out_dir}")
    else:
        answers: dict[str, Any] = {}
        for path in args.answers:
            answers.update(json.loads(path.read_text()))
        json.dump(
            score(
                json.loads(args.report.read_text()),
                answers,
                args.rules,
                not args.without_post_hoc,
            ),
            sys.stdout,
            ensure_ascii=False,
            indent=2,
        )
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
