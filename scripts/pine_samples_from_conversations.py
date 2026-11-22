"""Collect the Pine scripts in local assistant conversations, for the conversion survey.

ADR 0058 step 4 measures how many Pine indicators convert to formulas. Without the
user's own TradingView scripts, the scripts the indicator and strategy assistants
exchanged (the editor's code, each `run_pine` call and each saved script) are the
local sample. They are user data, so they go under `data/` (not committed; the
repository is public), one file per distinct script:

    python3 -I scripts/pine_samples_from_conversations.py \
        data/browser-preview/review.sqlite3 data/pine-samples/local-assistant

Then run the survey:

    SIW_PINE_SAMPLES=data/pine-samples/local-assistant \
        pnpm --filter @siw/desktop exec vitest run src/pine-convert.survey.test.ts
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

CODE_KEYS = {"code", "editor_code", "pine", "source", "start_code"}
STRATEGY = re.compile(r"^\s*strategy\s*\(", re.MULTILINE)


def scripts_in(value: Any, found: dict[str, str]) -> None:
    """Every Pine script inside a stored message, keyed by its whitespace-free hash."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in CODE_KEYS and isinstance(item, str):
                add(item, found)
            else:
                scripts_in(item, found)
    elif isinstance(value, list):
        for item in value:
            scripts_in(item, found)
    elif isinstance(value, str) and value.lstrip().startswith("{"):
        with contextlib.suppress(json.JSONDecodeError):
            scripts_in(json.loads(value), found)


def add(code: str, found: dict[str, str]) -> None:
    text = code.strip() + "\n"
    if "indicator(" not in text and "strategy(" not in text:
        return
    key = hashlib.sha256(re.sub(r"\s+", " ", text).encode()).hexdigest()[:12]
    found.setdefault(key, text)


def main(database: str, folder: str) -> int:
    out = Path(folder)
    out.mkdir(parents=True, exist_ok=True)
    found: dict[str, str] = {}
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        rows = connection.execute(
            "SELECT message FROM conversation_messages ORDER BY conversation_id, position"
        )
        for (message,) in rows:
            scripts_in(json.loads(message), found)
    for key, text in found.items():
        kind = "strategy" if STRATEGY.search(text) else "indicator"
        (out / f"{kind}-{key}.pine").write_text(text, encoding="utf-8")
    print(f"{len(found)} scripts written to {out}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
