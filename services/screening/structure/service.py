"""Wiring and command line of the structure model (ADR 0053).

`SIW_DATA_DIR/screening_structure.sqlite3` holds the session cache and the registry. The
research baseline parameter set is registered on first open. Approval is never done here
automatically: a person runs `register-parameters` with an approved document whose hashes
point at an eligible evaluation (and shadow audit). `$CLI` is
`uv run python -m services.screening.structure.service`:

    $CLI prefill STAK
    $CLI run STAK --instrument-id ID --cutoff 2026-11-06T14:00:00Z
    $CLI validate-due
    $CLI export-records --parameter-set-id ID --output r.jsonl
    $CLI evaluate --records r.jsonl --plan plan.json --output e.json
    $CLI shadow-audit --parameter-set-id ID --output a.json
    $CLI register-parameters --document approved.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from datetime import time as clock_time
from decimal import Decimal
from pathlib import Path
from typing import Any

from services.market_data.calendar import NEW_YORK, UsEquityCalendar
from services.market_data.daily import session_date_of
from services.market_data.provider import BarsQuery
from services.screening.structure.check import DailyBars, closed_sessions, structure_check
from services.screening.structure.evaluation import (
    audit_shadow,
    build_validation,
    evaluate,
    evaluation_record,
)
from services.screening.structure.registry import Registry
from services.screening.structure.sessions import (
    SessionCache,
    SessionFiller,
    canonical_sha256,
    open_database,
    utc,
)

CONFIG = Path(__file__).resolve().parent / "config"
DATABASE = "screening_structure.sqlite3"


def research_baseline() -> dict[str, Any]:
    return dict(json.loads((CONFIG / "research-baseline.json").read_text()))


def validation_policy() -> dict[str, Any]:
    return dict(json.loads((CONFIG / "validation-policy.json").read_text()))


@dataclass
class StructureStore:
    cache: SessionCache
    registry: Registry

    def parameters(self) -> dict[str, Any]:
        """The newest approved parameter set, else the research baseline."""
        approved = self.registry.operational_parameters()
        if approved is not None:
            return approved
        baseline = research_baseline()
        stored = self.registry.parameters(baseline["parameter_set_id"])
        return stored if stored is not None else baseline


def open_structure(path: Path, now: Callable[[], datetime]) -> StructureStore:
    connection, lock = open_database(path)
    store = StructureStore(SessionCache(connection, lock), Registry(connection, lock))
    baseline = research_baseline()
    if store.registry.parameters(baseline["parameter_set_id"]) is None:
        store.registry.register_parameters(baseline, utc(now()))
    return store


def daily_bars_from(adapter: Any) -> DailyBars:
    """Closed daily bars as traded from the Alpaca adapter (no adjustment: trade prices
    are as traded too). Only Alpaca bars: a bar from another source is never mixed in."""

    def load(instrument_id: str, start: date, end: date, as_of: datetime) -> list[dict[str, Any]]:
        query = BarsQuery(
            instrument_id,
            "1d",
            datetime.combine(start, clock_time(0), NEW_YORK).astimezone(timezone.utc),
            datetime.combine(end + timedelta(days=1), clock_time(0), NEW_YORK).astimezone(
                timezone.utc
            ),
            as_of_utc=as_of,
        )
        result = adapter.get_bars(query)
        bars = []
        for bar in result.bars:
            if not bar.get("is_final") or not str(bar.get("source_id", "")).startswith("alpaca"):
                continue
            if bar.get("adjustment") not in (None, "raw"):
                continue
            bars.append(
                {
                    "date": session_date_of(str(bar["interval_start_utc"])).isoformat(),
                    "high": Decimal(str(bar["high"])),
                    "low": Decimal(str(bar["low"])),
                    "close": Decimal(str(bar["close"])),
                }
            )
        return bars

    return load


def bars_sha256(bars: list[dict[str, Any]]) -> str:
    return canonical_sha256([{k: str(v) for k, v in bar.items()} for bar in bars])


def validate_due(
    store: StructureStore, daily_bars: DailyBars, now: datetime, policy: dict[str, Any]
) -> list[dict[str, Any]]:
    """Validates every scored forecast whose horizon has closed by `now`."""
    calendar = UsEquityCalendar("XNAS")
    added = []
    for forecast in store.registry.unvalidated():
        horizon = int(forecast["scores"]["horizon_sessions"])
        decision = datetime.fromisoformat(forecast["decision_timestamp_utc"].replace("Z", "+00:00"))
        day = decision.astimezone(NEW_YORK).date()
        closes: list[Any] = []
        while len(closes) < horizon and day < now.astimezone(NEW_YORK).date() + timedelta(days=1):
            day += timedelta(days=1)
            window = calendar.window_for_date(day)
            if window is not None:
                closes.append(window)
        if len(closes) < horizon or closes[-1].regular_close_utc > now:
            continue
        bars = daily_bars(
            forecast["instrument_id"], closes[0].session_date, closes[-1].session_date, now
        )
        validation = build_validation(
            forecast,
            store.registry.research(forecast["forecast_id"]),
            bars,
            policy,
            label_available_at_utc=utc(now),
            bars_sha256=bars_sha256(bars),
        )
        store.registry.add_validation(validation)
        added.append(validation)
    return added


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m services.screening.structure.service")
    commands = parser.add_subparsers(dest="command", required=True)
    prefill = commands.add_parser("prefill", help="read and cache the closed sessions")
    prefill.add_argument("symbol")
    prefill.add_argument("--cutoff")
    run = commands.add_parser("run", help="record one forecast at a cutoff (not a card)")
    run.add_argument("symbol")
    run.add_argument("--instrument-id", required=True, help="as the app's search shows it")
    run.add_argument("--cutoff")
    commands.add_parser("validate-due", help="post-hoc validation of closed horizons")
    export = commands.add_parser("export-records")
    export.add_argument("--parameter-set-id", required=True)
    export.add_argument("--output", type=Path, required=True)
    evaluate_cmd = commands.add_parser("evaluate")
    evaluate_cmd.add_argument("--records", type=Path, required=True)
    evaluate_cmd.add_argument("--plan", type=Path, required=True)
    evaluate_cmd.add_argument("--output", type=Path, required=True)
    evaluate_cmd.add_argument("--register", action="store_true")
    evaluate_cmd.add_argument("--parameter-set-id")
    shadow = commands.add_parser("shadow-audit")
    shadow.add_argument("--parameter-set-id", required=True)
    shadow.add_argument("--output", type=Path, required=True)
    shadow.add_argument("--minimum-sessions", type=int, default=20)
    shadow.add_argument("--maximum-unknown-rate", default="0.25")
    shadow.add_argument("--register", action="store_true")
    register = commands.add_parser("register-parameters")
    register.add_argument("--document", type=Path, required=True)
    args = parser.parse_args(argv)

    import os

    root = os.getenv("SIW_DATA_DIR", "").strip()
    if not root:
        print(json.dumps({"error": "set SIW_DATA_DIR"}))
        return 2
    now = datetime.now(timezone.utc)
    store = open_structure(Path(root) / DATABASE, lambda: now)

    def write(path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")

    if args.command == "register-parameters":
        document = json.loads(args.document.read_text())
        digest = store.registry.register_parameters(document, utc(now))
        print(json.dumps({"parameter_set_id": document["parameter_set_id"], "sha256": digest}))
        return 0
    if args.command == "evaluate":
        records = [
            json.loads(line) for line in args.records.read_text().splitlines() if line.strip()
        ]
        report = evaluate(records, json.loads(args.plan.read_text()))
        if args.register:
            store.registry.add_evaluation(str(uuid.uuid4()), args.parameter_set_id, report)
        write(args.output, report)
        print(
            json.dumps(
                {
                    "report_sha256": report["report_sha256"],
                    "eligible_for_shadow_review": report["eligible_for_shadow_review"],
                }
            )
        )
        return 0
    if args.command == "shadow-audit":
        report = audit_shadow(
            store.registry.forecasts_for(args.parameter_set_id),
            args.minimum_sessions,
            args.maximum_unknown_rate,
        )
        if args.register:
            store.registry.add_shadow_audit(str(uuid.uuid4()), args.parameter_set_id, report)
        write(args.output, report)
        print(
            json.dumps(
                {
                    "report_sha256": report["report_sha256"],
                    "eligible_for_production_review": report["eligible_for_production_review"],
                }
            )
        )
        return 0
    if args.command == "export-records":
        records = [
            evaluation_record(f, v, store.registry.research(f["forecast_id"]))
            for f, v in store.registry.validations_for(args.parameter_set_id)
        ]
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))
        print(json.dumps({"records": len(records)}))
        return 0

    from services.api.main import _LazyBoats, _LazyCredentials

    adapter = _LazyBoats(_LazyCredentials(None).get).get()
    if adapter is None:
        print(json.dumps({"error": "no enabled Alpaca key (SIW_DATA_DIR)"}))
        return 2
    daily = daily_bars_from(adapter)
    if args.command == "validate-due":
        added = validate_due(store, daily, now, validation_policy())
        print(json.dumps({"validations": len(added)}))
        return 0

    symbol = args.symbol.upper()
    cutoff = datetime.fromisoformat(args.cutoff.replace("Z", "+00:00")) if args.cutoff else now
    calendar = UsEquityCalendar("XNAS")
    filler = SessionFiller(store.cache, lambda: adapter.trade_pages, lambda: now, inline=True)
    if args.command == "prefill":
        known_until = min(cutoff, now - timedelta(minutes=adapter.config.publication_delay_minutes))
        windows = closed_sessions(
            calendar, known_until, int(store.parameters().get("long_window_sessions", 20))
        )
        filler.request(symbol, windows)
        missing = [
            {
                "session_date": w.session_date.isoformat(),
                "reason": filler.error(symbol, w.session_date),
            }
            for w in windows
            if store.cache.get(symbol, w.session_date) is None
        ]
        print(json.dumps({"required": len(windows), "missing": missing}, ensure_ascii=False))
        return 0
    outcome = structure_check(
        symbol=symbol,
        instrument_id=args.instrument_id,
        cutoff=cutoff,
        now=now,
        publication_delay_minutes=adapter.config.publication_delay_minutes,
        calendar=calendar,
        cache=store.cache,
        filler=filler,
        registry=store.registry,
        parameters=store.parameters(),
        trade_pages=adapter.trade_pages,
        split_actions=adapter.split_actions,
        daily_bars=daily,
        wait_seconds=0,
        sleep=time.sleep,
    )
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
