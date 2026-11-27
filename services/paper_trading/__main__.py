"""Kill switch without the app: `uv run python -m services.paper_trading halt --reason …`.

Commands only change the local kill-switch state; nothing is sent to any broker.
`summary --day YYYY-MM-DD` prints that day's paper fills per instrument (ADR 0057 stage 7),
read from the local ledger only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from services.paper_trading import daily
from services.paper_trading.store import PaperOrderStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m services.paper_trading")
    parser.add_argument("command", choices=("status", "halt", "release", "summary"))
    parser.add_argument("--day", type=date.fromisoformat, help="summary: the local day")
    parser.add_argument("--time-zone", default="America/New_York", help="summary: its zone")
    parser.add_argument("--reason", default="halted from the command line")
    parser.add_argument("--data-dir", type=Path, help="defaults to SIW_DATA_DIR")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    data_dir = args.data_dir or (
        Path(os.environ["SIW_DATA_DIR"]) if os.getenv("SIW_DATA_DIR", "").strip() else None
    )
    if data_dir is None:
        print("SIW_DATA_DIR is not set; pass --data-dir", file=sys.stderr)
        return 2
    store = PaperOrderStore(data_dir)
    if args.command == "summary":
        if args.day is None:
            print("summary needs --day YYYY-MM-DD", file=sys.stderr)
            return 2
        summary = daily.store_day_summary(store, args.day, args.time_zone)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    if args.command == "halt":
        store.halt(args.reason, source="cli")
    elif args.command == "release":
        store.release(source="cli")
    print(json.dumps(store.status(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
