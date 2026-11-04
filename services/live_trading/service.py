"""The resident live-order runner (ADR 0047 §6).

`uv run python -m services.live_trading.service` — a process apart from the paper
runner, with its own ledger (`live_orders.sqlite3`) and kill switch. Disarmed it only
writes heartbeats. Armed, it opens the registered Kiwoom *real* key whose account the
approved plan names, and every order it decides waits for the user's confirmation on the
SIW Agent screen before anything is sent.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from services.backtest.store import BacktestStore
from services.credentials.store import CredentialStore, KeyringSecretBackend
from services.live_trading.kiwoom_live import KiwoomLiveBroker, live_account_ref
from services.live_trading.runner import LiveRunner
from services.live_trading.store import LiveOrderStore
from services.paper_trading.service import RunnerService, default_feed, load_env_file

LOG = logging.getLogger("siw.live_runner")
# The live adapter offers US listings only (ADR 0047 §3); no verification record gates it.
LIVE_MARKETS = frozenset({"us"})


def real_kiwoom_broker(credentials: CredentialStore, account_ref: str) -> KiwoomLiveBroker | None:
    """The enabled Kiwoom real key whose account the plan names. Secrets stay in memory."""
    for record in credentials.list("kiwoom"):
        if not record.enabled or record.environment != "real":
            continue
        secrets = credentials.secrets(record)
        if live_account_ref(secrets["app_key"]) == account_ref:
            return KiwoomLiveBroker(
                secrets["app_key"], secrets["secret_key"], "real", markets=LIVE_MARKETS
            )
    return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m services.live_trading.service")
    parser.add_argument("--data-dir", type=Path, help="defaults to SIW_DATA_DIR")
    parser.add_argument("--interval-seconds", type=float, default=5.0)
    parser.add_argument("--once", action="store_true", help="one pass, then exit")
    parser.add_argument("--env-file", type=Path, help="settings file such as the repo's .env")
    args = parser.parse_args(argv)
    if args.env_file is not None and args.env_file.is_file():
        load_env_file(args.env_file)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    configured = os.getenv("SIW_DATA_DIR", "").strip()
    data_dir = args.data_dir or (Path(configured) if configured else None)
    if data_dir is None:
        print("SIW_DATA_DIR is not set; pass --data-dir", file=sys.stderr)
        return 2
    data_dir = data_dir.resolve()
    store = LiveOrderStore(data_dir)
    credentials = CredentialStore(data_dir, KeyringSecretBackend())
    backtests = BacktestStore(data_dir / "backtests.sqlite3", lambda: datetime.now(timezone.utc))
    service = RunnerService(
        store,
        strategies=backtests.strategy,
        broker_for=lambda ref, _markets: real_kiwoom_broker(credentials, ref),
        feed=lambda: default_feed(data_dir, credentials),
        runner_class=LiveRunner,
        missing_key="no enabled Kiwoom real key matches the approved account",
    )
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    LOG.info("live runner started (data dir %s)", data_dir)
    previous = ""
    while not stop.is_set():
        outcome = service.tick()
        active = (
            outcome.startswith("step")
            and outcome != "step intents=0 submitted=0 blocked=0 fills=0 reconciled=0"
        )
        if outcome != previous or active:
            LOG.info("%s", outcome)
            previous = outcome
        if args.once:
            break
        stop.wait(args.interval_seconds)
    LOG.info("live runner stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
