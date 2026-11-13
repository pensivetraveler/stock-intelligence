"""Build the card-extraction eval set and score the gateway's report (ADR 0056 §5, #SIW-lib-4).

    python -m services.technique_cards.evalset synthetic --out-dir data/technique_eval/synthetic
    python -m services.technique_cards.evalset real --manifest m.json --out-dir <dir>
    python -m services.technique_cards.evalset score --report <report.json> --answers <answers.json>

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
from services.technique_cards.citations import parse_transcript
from services.technique_cards.prompt import PROMPT_VERSION, SYSTEM, user_prompt
from services.technique_cards.scoring import CardScore, score_card, summarise
from services.technique_cards.synthetic import build

SPECS = Path(__file__).with_name("synthetic_specs.json")
MAX_TOKENS = 8000  # gateway: at least 6000


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


def real(manifest: Path, out_dir: Path) -> list[dict[str, Any]]:
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


def score(report: dict[str, Any], answers: dict[str, Any]) -> dict[str, Any]:
    by_model: dict[str, list[CardScore]] = {}
    costs: dict[str, float] = {}
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
        item = score_card(card, case["answer"], lines)
        if card is None:
            item.schema_errors = ["no JSON answer"]
        by_model.setdefault(result["model"], []).append(item)
        costs[result["model"]] = costs.get(result["model"], 0.0) + float(result.get("costUsd") or 0)
    return {
        model: {
            "summary": summarise(items),
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
    sc = sub.add_parser("score")
    sc.add_argument("--report", type=Path, required=True)
    sc.add_argument("--answers", type=Path, nargs="+", required=True)
    args = parser.parse_args(argv)
    if args.command == "synthetic":
        items = synthetic(args.out_dir)
        print(f"{len(items)} synthetic cases -> {args.out_dir}")
    elif args.command == "real":
        items = real(args.manifest, args.out_dir)
        print(f"{len(items)} real cases -> {args.out_dir}")
    else:
        answers: dict[str, Any] = {}
        for path in args.answers:
            answers.update(json.loads(path.read_text()))
        json.dump(
            score(json.loads(args.report.read_text()), answers),
            sys.stdout,
            ensure_ascii=False,
            indent=2,
        )
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
