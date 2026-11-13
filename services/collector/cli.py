from __future__ import annotations

import argparse
import json
import os
import signal
import threading
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# The user's choice (2026-11-05): the condition watched in realtime.
WATCHED = "SIW_당일급등주"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="siw-collector")
    parser.add_argument(
        "command",
        choices=(
            "doctor",
            "run",
            "backfill",
            "watch-condition",
            "export-condition",
            "watch-halts",
            "export-halts",
        ),
    )
    parser.add_argument("--config", type=Path)
    backfill = parser.add_argument_group("backfill (provider from SIW_MARKET_DATA_PROVIDER)")
    backfill.add_argument("--instrument-id")
    backfill.add_argument("--start-utc", type=_utc_argument)
    backfill.add_argument("--end-utc", type=_utc_argument)
    backfill.add_argument("--chunk-minutes", type=int, default=390)
    backfill.add_argument("--min-request-interval-seconds", type=float, default=0.0)
    backfill.add_argument("--max-attempts", type=int, default=3)
    backfill.add_argument("--data-dir", type=Path, help="defaults to SIW_DATA_DIR")
    watch = parser.add_argument_group("watch-condition / export-condition (ADR 0051)")
    watch.add_argument("--name", help=f"a connected Kiwoom condition (default {WATCHED})")
    watch.add_argument(
        "--date", type=date.fromisoformat, help="export: a New York session date, YYYY-MM-DD"
    )
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
    data_dir = _data_dir(args)
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


def _data_dir(args: argparse.Namespace) -> Path | None:
    return args.data_dir or (Path(value) if (value := os.getenv("SIW_DATA_DIR")) else None)


def _watch_condition(args: argparse.Namespace) -> int:
    """Record one Kiwoom condition in realtime, every US session, until stopped."""
    from services.collector.condition_watch import ConditionWatchStore, run_watch
    from services.credentials.store import CredentialStore, KeyringSecretBackend
    from services.market_data.kiwoom_account import real_account_session
    from services.market_data.kiwoom_conditions import CONNECTED_CONDITIONS, KiwoomConditionSearch

    name = args.name or WATCHED
    if name not in CONNECTED_CONDITIONS:
        _error("invalid_arguments", f"{name!r} is not a connected Kiwoom condition")
        return 2
    data_dir = _data_dir(args)
    if data_dir is None:
        _error("invalid_config", "watch-condition requires --data-dir or SIW_DATA_DIR")
        return 2
    session = real_account_session(CredentialStore(data_dir, KeyringSecretBackend()))
    if session is None:
        _error("invalid_config", "no enabled real-account Kiwoom key is registered")
        return 2
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda _signum, _frame: stop.set())
    signal.signal(signal.SIGTERM, lambda _signum, _frame: stop.set())
    store = ConditionWatchStore.in_data_dir(data_dir)
    try:
        return run_watch(
            KiwoomConditionSearch(session.access_token, "real"),
            store,
            name,
            "real",
            stop=stop,
            clock=lambda: datetime.now(timezone.utc),
            report=lambda payload: _print({**payload, "service": "collector"}),
        )
    finally:
        store.close()


def _export_condition(args: argparse.Namespace) -> int:
    """Print what was recorded during one New York session date, as JSON."""
    from services.collector.condition_watch import ConditionWatchStore
    from services.market_data.calendar import NEW_YORK

    data_dir = _data_dir(args)
    if data_dir is None or args.date is None:
        _error("invalid_arguments", "export-condition requires --date and a data dir")
        return 2
    start = datetime.combine(args.date, datetime.min.time(), NEW_YORK)
    store = ConditionWatchStore.in_data_dir(data_dir)
    try:
        document = store.export(start, start + timedelta(days=1))
    finally:
        store.close()
    name = args.name or WATCHED
    document["watches"] = [
        item for item in document["watches"] if item["condition"]["name"] == name
    ]
    print(json.dumps(document, ensure_ascii=False, indent=2))
    return 0


def _watch_halts(args: argparse.Namespace) -> int:
    """Record Alpaca SIP trading statuses (halts) in realtime, every US session, until stopped."""
    from services.collector.halt_watch import StatusWatchStore, run_halt_watch
    from services.credentials.store import CredentialStore, KeyringSecretBackend

    data_dir = _data_dir(args)
    if data_dir is None:
        _error("invalid_config", "watch-halts requires --data-dir or SIW_DATA_DIR")
        return 2
    credentials = CredentialStore(data_dir, KeyringSecretBackend())
    record = next((item for item in credentials.list("alpaca") if item.enabled), None)
    if record is None:
        _error("invalid_config", "no enabled Alpaca key is registered")
        return 2
    secrets = credentials.secrets(record)
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda _signum, _frame: stop.set())
    signal.signal(signal.SIGTERM, lambda _signum, _frame: stop.set())
    store = StatusWatchStore.in_data_dir(data_dir)
    try:
        return run_halt_watch(
            secrets["api_key_id"],
            secrets["api_secret_key"],
            store,
            stop=stop,
            clock=lambda: datetime.now(timezone.utc),
            report=lambda payload: _print({**payload, "service": "collector"}),
        )
    finally:
        store.close()


def _export_halts(args: argparse.Namespace) -> int:
    """Print the listens and status changes received during one New York date, as JSON."""
    from services.collector.halt_watch import StatusWatchStore
    from services.market_data.calendar import NEW_YORK

    data_dir = _data_dir(args)
    if data_dir is None or args.date is None:
        _error("invalid_arguments", "export-halts requires --date and a data dir")
        return 2
    start = datetime.combine(args.date, datetime.min.time(), NEW_YORK)
    store = StatusWatchStore.in_data_dir(data_dir)
    try:
        document = store.export(start, start + timedelta(days=1))
    finally:
        store.close()
    print(json.dumps(document, ensure_ascii=False, indent=2))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "doctor":
        return _doctor(args.config)
    if args.command == "backfill":
        return _backfill(args)
    if args.command == "watch-condition":
        return _watch_condition(args)
    if args.command == "export-condition":
        return _export_condition(args)
    if args.command == "watch-halts":
        return _watch_halts(args)
    if args.command == "export-halts":
        return _export_halts(args)
    return _run(args.config)
