"""The resident paper-order runner process (ADR 0037 §6).

`uv run python -m services.paper_trading.service` — started by the SIW Agent LaunchAgent.

While disarmed it only writes heartbeats: no bars are read and no broker is called.
Armed, it rebuilds the approved plan (the strategy must still hash to the approved
SHA-256), opens the registered Kiwoom *demo* key whose account matches the plan, and runs
`PaperRunner.step` every few seconds. The adapter offers only markets recorded as
verified (P4), so before that every order is blocked by the limit check.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import signal
import sys
import threading
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.backtest.store import BacktestStore
from services.credentials.store import (
    CredentialStore,
    KeyringSecretBackend,
    SecretStorageError,
)
from services.market_data.ecos_fx import EcosError, EcosUsdKrw
from services.paper_trading.brokers.kiwoom_paper import KiwoomPaperBroker
from services.paper_trading.intent import FxRate
from services.paper_trading.paper_client import LiveDomainBlockedError, PaperOperationBlockedError
from services.paper_trading.runner import BarFeed, PaperRunner, plan_from_approval
from services.paper_trading.store import PaperOrderStore

LOG = logging.getLogger("siw.paper_runner")


def kiwoom_account_ref(app_key: str) -> str:
    return f"kiwoom-{hashlib.sha256(app_key.encode()).hexdigest()[:16]}-demo"


def demo_kiwoom_broker(
    credentials: CredentialStore, account_ref: str, markets: frozenset[str]
) -> KiwoomPaperBroker | None:
    """The enabled Kiwoom demo key whose account the plan names. Secrets stay in memory."""
    for record in credentials.list("kiwoom"):
        if not record.enabled or record.environment != "demo":
            continue
        secrets = credentials.secrets(record)
        if kiwoom_account_ref(secrets["app_key"]) == account_ref:
            return KiwoomPaperBroker(
                secrets["app_key"], secrets["secret_key"], "demo", markets=markets
            )
    return None


def ecos_fx(
    credentials: CredentialStore,
    *,
    transport: Any = None,
    now: Callable[[], datetime] | None = None,
) -> Callable[[], FxRate | None]:
    """Today's ECOS USD/KRW rate from the first enabled ECOS key; None when there is none.

    The key is read into memory once per key version: replacing or switching the key on the
    Keys screen takes effect without restarting the runner. A failed read gives None, so
    orders are blocked by the amount limit rather than checked against a stale or guessed
    rate; the client waits before asking ECOS again, and the same failure is logged once.
    """
    current: dict[str, Any] = {"version": None, "client": None, "logged": None}

    def rate() -> FxRate | None:
        def warn_once(message: str) -> None:
            if message != current["logged"]:
                LOG.warning("ECOS rate unavailable: %s", message)
                current["logged"] = message

        record = next((r for r in credentials.list("ecos") if r.enabled), None)
        version = None if record is None else (record.credential_id, record.secret_revision)
        if version != current["version"]:
            try:
                client = (
                    None
                    if record is None
                    else EcosUsdKrw(
                        credentials.secrets(record)["api_key"], transport=transport, now=now
                    )
                )
            except (SecretStorageError, EcosError) as exc:
                # The version is kept unchanged, so the key is read again next time.
                warn_once(f"the ECOS key could not be used: {exc}")
                return None
            if current["client"] is not None:
                current["client"].close()
            current.update(version=version, client=client, logged=None)
        client = current["client"]
        if client is None:
            return None
        try:
            found = client.rate()
        except EcosError as exc:
            warn_once(str(exc))
            return None
        current["logged"] = None
        return FxRate(found.krw_per_usd, found.source, found.fetched_at_utc)

    return rate


def default_feed(data_dir: Path, credentials: CredentialStore) -> BarFeed:
    from services.market_data.bars_service import MarketBarsService
    from services.market_data.selection import provider_from_env
    from services.market_data.storage import CachedMarketDataProvider, LocalMarketDataStore
    from services.paper_trading.feed import ServiceBarFeed

    provider = CachedMarketDataProvider(
        provider_from_env(credentials), LocalMarketDataStore(data_dir)
    )
    return ServiceBarFeed(MarketBarsService(provider).get_bars)


class RunnerService:
    """Arming-aware loop around `PaperRunner`; every outside part can be replaced in tests."""

    def __init__(
        self,
        store: PaperOrderStore,
        strategies: Callable[[str, int], dict[str, Any]],
        broker_for: Callable[[str, frozenset[str]], Any],
        feed: Callable[[], BarFeed],
        now: Callable[[], datetime] | None = None,
        fx_rate: Callable[[], FxRate | None] | None = None,
    ) -> None:
        self.store = store
        self._strategies = strategies
        self._broker_for = broker_for
        self._feed = feed
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._fx_rate = fx_rate
        self._runner: PaperRunner | None = None
        self._feed_instance: BarFeed | None = None
        self.last_error: str | None = None

    def _runner_for(self, approval_id: str) -> PaperRunner | None:
        if self._runner is not None and self._runner.approval_id == approval_id:
            return self._runner
        content = self.store.approval(approval_id)
        if content is None:
            return None
        strategy = content["strategy"]
        document = self._strategies(strategy["strategy_id"], int(strategy["strategy_version"]))
        plan = plan_from_approval(content, document["definition"])
        broker = self._broker_for(
            plan.broker_account_ref, self.store.verified_markets(plan.broker_id)
        )
        if broker is None:
            self.last_error = "no enabled Kiwoom demo key matches the approved account"
            return None
        if self._feed_instance is None:
            self._feed_instance = self._feed()
        self._runner = PaperRunner(
            self.store, broker, self._feed_instance, plan, fx_rate=self._fx_rate
        )
        if self._runner.approval_id != approval_id:
            raise ValueError("the rebuilt plan does not match its approval")
        return self._runner

    def tick(self) -> str:
        """One loop pass; returns what it did, for the log."""
        self.store.heartbeat()
        status = self.store.status()
        if status["halted"] or not status["armed"] or status["approval_id"] is None:
            self._runner = None
            return "idle"
        try:
            runner = self._runner_for(status["approval_id"])
            if runner is None:
                return "no_runner"
            report = runner.step(self._now())
        except (LiveDomainBlockedError, PaperOperationBlockedError) as exc:
            # A request tried to leave the paper domain or allowlist: stop everything.
            self.store.halt(f"blocked request: {type(exc).__name__}", source="runner")
            self.last_error = str(exc)[:200]
            return "halted"
        except Exception as exc:  # noqa: BLE001 - logged; the next pass tries again
            self.last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
            LOG.warning("paper runner step failed: %s", self.last_error)
            return "error"
        self.last_error = None
        return (
            f"step intents={report.intents} submitted={report.submitted} "
            f"blocked={report.blocked} fills={report.fills} reconciled={report.reconciled}"
        )


def load_env_file(path: Path) -> None:
    """KEY=VALUE lines from the repository's .env, without replacing what is already set.

    The LaunchAgent starts the runner without a shell, so this is how it gets the same
    settings as the API (which uvicorn reads with --env-file). Values are never logged.
    """
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name and name not in os.environ:
            os.environ[name] = value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m services.paper_trading.service")
    parser.add_argument("--data-dir", type=Path, help="defaults to SIW_DATA_DIR")
    parser.add_argument("--interval-seconds", type=float, default=10.0)
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
    store = PaperOrderStore(data_dir)
    credentials = CredentialStore(data_dir, KeyringSecretBackend())
    backtests = BacktestStore(data_dir / "backtests.sqlite3", lambda: datetime.now(timezone.utc))
    service = RunnerService(
        store,
        strategies=backtests.strategy,
        broker_for=lambda ref, markets: demo_kiwoom_broker(credentials, ref, markets),
        feed=lambda: default_feed(data_dir, credentials),
        fx_rate=ecos_fx(credentials),
    )
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    LOG.info("paper runner started (data dir %s)", data_dir)
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
    LOG.info("paper runner stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
