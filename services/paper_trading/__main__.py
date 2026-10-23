"""Kill switch without the app: `uv run python -m services.paper_trading halt --reason …`.

Commands only change the local kill-switch state; nothing is sent to any broker.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from services.paper_trading.store import PaperOrderStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m services.paper_trading")
    parser.add_argument("command", choices=("status", "halt", "release"))
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
    if args.command == "halt":
        store.halt(args.reason, source="cli")
    elif args.command == "release":
        store.release(source="cli")
    print(json.dumps(store.status(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
