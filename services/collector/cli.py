from __future__ import annotations

import argparse
import json
import signal
import threading
from collections.abc import Sequence
from pathlib import Path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="siw-collector")
    parser.add_argument("command", choices=("doctor", "run"))
    parser.add_argument("--config", type=Path)
    return parser


def _valid_config(path: Path | None) -> bool:
    if path is None:
        return True
    return path.is_file()


def _print(payload: dict[str, object]) -> None:
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))


def _doctor(config: Path | None) -> int:
    if not _valid_config(config):
        _print(
            {
                "error": {"code": "invalid_config", "message": "Configuration file not found"},
                "service": "collector",
                "status": "error",
            }
        )
        return 2
    _print({"external_calls": False, "service": "collector", "status": "ok"})
    return 0


def _run(config: Path | None) -> int:
    if not _valid_config(config):
        return _doctor(config)

    stop = threading.Event()

    def handle_stop(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)
    _print({"external_calls": False, "service": "collector", "status": "running"})
    stop.wait()
    _print({"service": "collector", "status": "stopped"})
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "doctor":
        return _doctor(args.config)
    return _run(args.config)
