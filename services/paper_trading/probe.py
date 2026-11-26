"""Hand-run checks of what the Kiwoom US paper server takes (dashboard item 316).

ADR 0057 stage 6 needs to know whether Kiwoom's US paper server takes a sell after the
regular session (the same-day exit is 19:50 ET) and STOP / STOP LIMIT orders. The runner
sends limit orders only, so this command sends one order at a time by hand, to the paper
host only (`KiwoomPaperBroker` refuses a real key and `PaperOnlyClient` any other host).

    uv run python -m services.paper_trading.probe --env-file .env orders
    uv run python -m services.paper_trading.probe --env-file .env order \
        --side sell --symbol AMD --venue ND --type limit --price 120.00 --yes
    uv run python -m services.paper_trading.probe --env-file .env cancel \
        --ref 000000123 --symbol AMD --venue ND --yes

Guards: one share per order, at most $1,000 at the order or stop price, `--yes` on every
order and cancel, and nothing while the paper runner is armed or under the kill switch
(the runner owns the demo key's token, ADR 0037 §6). Probe orders are not written to the
paper ledger; what they showed goes into the ADR by hand. Keys are never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from services.credentials.store import CredentialStore, KeyringSecretBackend
from services.paper_trading.brokers.kiwoom_paper import KiwoomPaperBroker
from services.paper_trading.service import load_env_file
from services.paper_trading.store import PaperOrderStore

NEW_YORK = ZoneInfo("America/New_York")
# Kiwoom `trde_tp` from the ust20000/ust20001 spec.
ORDER_TYPES = {"limit": "00", "stop": "35", "stop_limit": "34"}
VENUE_MIC = {"ND": "xnas", "NY": "xnys", "NA": "xase"}
MAX_QUANTITY = Decimal(1)
MAX_NOTIONAL_USD = Decimal(1_000)


class ProbeRefused(RuntimeError):
    pass


def _decimal(text: str | None, name: str) -> Decimal | None:
    if text is None:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ProbeRefused(f"{name} is not a number: {text!r}") from exc
    if not value.is_finite() or value <= 0:
        raise ProbeRefused(f"{name} must be positive")
    return value


def check_order(order_type: str, price: Decimal | None, stop: Decimal | None) -> None:
    """The spec's required prices for each type, and the probe's size cap."""
    if order_type in ("limit", "stop_limit") and price is None:
        raise ProbeRefused(f"{order_type} needs --price")
    if order_type in ("stop", "stop_limit") and stop is None:
        raise ProbeRefused(f"{order_type} needs --stop")
    if order_type == "stop" and price is not None:
        raise ProbeRefused("a STOP order carries no --price (the spec leaves ord_uv empty)")
    for value in (price, stop):
        if value is not None and value * MAX_QUANTITY > MAX_NOTIONAL_USD:
            raise ProbeRefused(f"one share at {value} is more than ${MAX_NOTIONAL_USD}")


def check_runner_idle(store: PaperOrderStore) -> None:
    status = store.status()
    if status["armed"] or status["halted"]:
        raise ProbeRefused(
            "the paper runner is armed or under the kill switch; disarm it (and stop the "
            "resident runner) first, so two clients do not share the demo key's token"
        )


def demo_broker(credentials: CredentialStore, label: str) -> KiwoomPaperBroker:
    for record in credentials.list("kiwoom"):
        if record.label == label and record.enabled and record.environment == "demo":
            secrets = credentials.secrets(record)
            return KiwoomPaperBroker(secrets["app_key"], secrets["secret_key"], "demo")
    raise ProbeRefused(f"no enabled Kiwoom demo key labelled {label!r}")


def stamp(now: datetime) -> dict[str, str]:
    return {
        "at_utc": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "at_new_york": now.astimezone(NEW_YORK).isoformat(timespec="seconds"),
    }


def run(args: argparse.Namespace, broker: KiwoomPaperBroker, now: datetime) -> dict[str, Any]:
    if args.command == "orders":
        rows = [asdict(order) for order in broker.today_orders()]
        return {"command": "orders", **stamp(now), "orders": rows}
    if not args.yes:
        raise ProbeRefused("add --yes to send this to the paper server")
    if args.command == "cancel":
        result = broker.cancel(
            args.ref, f"us:probe:{VENUE_MIC[args.venue]}:{args.symbol}", args.symbol
        )
        return {"command": "cancel", **stamp(now), "result": asdict(result)}
    price = _decimal(args.price, "--price")
    stop = _decimal(args.stop, "--stop")
    check_order(args.type, price, stop)
    result = broker.probe_order(
        args.side,
        args.symbol,
        args.venue,
        MAX_QUANTITY,
        ORDER_TYPES[args.type],
        price,
        stop,
    )
    return {
        "command": "order",
        **stamp(now),
        "request": {
            "side": args.side,
            "symbol": args.symbol,
            "venue": args.venue,
            "type": args.type,
            "price": None if price is None else str(price),
            "stop": None if stop is None else str(stop),
        },
        "result": asdict(result),
    }


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="python -m services.paper_trading.probe")
    root.add_argument("--data-dir", type=Path, help="defaults to SIW_DATA_DIR")
    root.add_argument("--env-file", type=Path)
    root.add_argument("--key", default="해외모의", help="label of the Kiwoom demo key")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("orders", help="today's orders on the paper account")
    order = commands.add_parser("order", help="send one 1-share order")
    order.add_argument("--side", choices=("buy", "sell"), required=True)
    order.add_argument("--symbol", required=True)
    order.add_argument("--venue", choices=("ND", "NY", "NA"), required=True)
    order.add_argument("--type", choices=tuple(ORDER_TYPES), required=True)
    order.add_argument("--price")
    order.add_argument("--stop")
    order.add_argument("--yes", action="store_true")
    cancel = commands.add_parser("cancel", help="cancel one open order")
    cancel.add_argument("--ref", required=True)
    cancel.add_argument("--symbol", required=True)
    cancel.add_argument("--venue", choices=("ND", "NY", "NA"), required=True)
    cancel.add_argument("--yes", action="store_true")
    return root


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.env_file is not None and args.env_file.is_file():
        load_env_file(args.env_file)
    configured = os.getenv("SIW_DATA_DIR", "").strip()
    data_dir = args.data_dir or (Path(configured) if configured else None)
    if data_dir is None:
        print("SIW_DATA_DIR is not set; pass --data-dir", file=sys.stderr)
        return 2
    try:
        check_runner_idle(PaperOrderStore(data_dir.resolve()))
        broker = demo_broker(CredentialStore(data_dir.resolve(), KeyringSecretBackend()), args.key)
        report = run(args, broker, datetime.now(timezone.utc))
    except ProbeRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
