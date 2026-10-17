from __future__ import annotations

import argparse
import json
import os
import signal
import threading
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="siw-collector")
    parser.add_argument("command", choices=("doctor", "run", "backfill"))
    parser.add_argument("--config", type=Path)
    backfill = parser.add_argument_group("backfill (provider from SIW_MARKET_DATA_PROVIDER)")
    backfill.add_argument("--instrument-id")
    backfill.add_argument("--start-utc", type=_utc_argument)
    backfill.add_argument("--end-utc", type=_utc_argument)
    backfill.add_argument("--chunk-minutes", type=int, default=390)
    backfill.add_argument("--min-request-interval-seconds", type=float, default=0.0)
    backfill.add_argument("--max-attempts", type=int, default=3)
    backfill.add_argument("--data-dir", type=Path, help="defaults to SIW_DATA_DIR")
    return parser


def _utc_argument(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected an ISO 8601 UTC timestamp") from exc


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


def _error(code: str, message: str) -> None:
    _print({"error": {"code": code, "message": message}, "service": "collector", "status": "error"})


def _backfill(args: argparse.Namespace) -> int:
    # Imported here so `doctor` and `run` keep their fast, dependency-light startup.
    from services.collector.backfill import BackfillRequest, run_backfill
    from services.market_data.provider import (
        MarketDataError,
        ProviderAuthError,
        ProviderDataError,
        ProviderUnavailableError,
    )
    from services.market_data.selection import ProviderConfigError, provider_from_env
    from services.market_data.storage import LocalMarketDataStore, StorageError

    missing = [
        name for name in ("instrument_id", "start_utc", "end_utc") if getattr(args, name) is None
    ]
    if missing:
        _error("invalid_arguments", f"backfill requires --{missing[0].replace('_', '-')}")
        return 2
    data_dir = args.data_dir or (Path(value) if (value := os.getenv("SIW_DATA_DIR")) else None)
    if data_dir is None:
        _error("invalid_config", "backfill requires --data-dir or SIW_DATA_DIR")
        return 2

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda _signum, _frame: stop.set())
    signal.signal(signal.SIGTERM, lambda _signum, _frame: stop.set())
    request = BackfillRequest(
        instrument_id=args.instrument_id,
        start_utc=args.start_utc,
        end_utc=args.end_utc,
        chunk_minutes=args.chunk_minutes,
        max_attempts=args.max_attempts,
    )
    try:
        with LocalMarketDataStore(data_dir) as store:
            summary = run_backfill(
                store,
                provider_from_env(),
                request,
                min_interval_seconds=args.min_request_interval_seconds,
                stop=stop,
            )
    except ProviderConfigError as exc:
        _error("invalid_config", str(exc))
        return 2
    except ProviderAuthError as exc:
        _error("provider_auth_failed", str(exc))
        return 1
    except (ProviderUnavailableError, ProviderDataError) as exc:
        _error("provider_error", str(exc))
        return 1
    except MarketDataError as exc:
        _error("invalid_backfill_request", str(exc))
        return 2
    except StorageError as exc:
        _error("storage_error", type(exc).__name__)
        return 1
    _print({**summary, "external_calls": False})
    return {"succeeded": 0, "already_succeeded": 0, "stopped": 0, "job_in_progress": 3}.get(
        summary["status"], 1
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "doctor":
        return _doctor(args.config)
    if args.command == "backfill":
        return _backfill(args)
    return _run(args.config)
