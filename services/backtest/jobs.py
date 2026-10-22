"""Queued backtest runs, executed one at a time on a background thread.

A run's configuration is checked and frozen when it is submitted. Cancelling a queued run
takes effect at once; a running one stops at its next step (a Python strategy's process
is killed). A failure is stored with its reason; nothing is retried silently.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from packages.contracts.validator import validate_document
from services.backtest.data import (
    ADJUSTMENTS,
    MAX_BAR_COUNT,
    GetAdjusted,
    GetBars,
    load_instrument,
)
from services.backtest.engine import (
    Backtest,
    Cancelled,
    EngineConfig,
    PythonSignals,
    RuleSignals,
    SignalSource,
)
from services.backtest.sandbox import SandboxError, StrategyProcess
from services.backtest.store import BacktestStore
from services.backtest.strategy import StrategyError
from services.backtest.walkforward import WalkForward, WalkForwardError, clean_spec
from services.custom_indicators.store import CustomIndicatorStore
from services.market_data.instruments import market_of
from services.market_data.provider import MarketDataError

LOGGER = logging.getLogger(__name__)
TIMEFRAME = re.compile(r"^([1-9][0-9]{0,2}m|1d|1w)$")
MAX_INSTRUMENTS = 20
SURVIVORSHIP = (
    "the universe is the list chosen today; instruments delisted before today are not "
    "included, which can flatter results (survivorship bias)"
)


class RunConfigError(ValueError):
    pass


def _decimal(value: object, name: str, low: Decimal, high: Decimal) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise RunConfigError(f"{name} must be a number")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise RunConfigError(f"{name} must be a number") from exc
    if not number.is_finite() or not low <= number <= high:
        raise RunConfigError(f"{name} must be between {low} and {high}")
    return format(number.normalize(), "f") if number else "0"


def _date(value: object, name: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise RunConfigError(f"{name} must be a date (YYYY-MM-DD)") from exc


def validate_config(request: dict[str, Any]) -> dict[str, Any]:
    ids = request.get("instrument_ids")
    if (
        not isinstance(ids, list)
        or not 1 <= len(ids) <= MAX_INSTRUMENTS
        or not all(isinstance(item, str) and 0 < len(item) <= 200 for item in ids)
        or len(set(ids)) != len(ids)
    ):
        raise RunConfigError(f"instrument_ids must list 1 to {MAX_INSTRUMENTS} distinct IDs")
    currency = request.get("currency")
    if currency not in ("USD", "KRW"):
        raise RunConfigError("currency must be USD or KRW")
    for instrument_id in ids:
        expected = "KRW" if market_of(instrument_id) == "KR" else "USD"
        if expected != currency:
            raise RunConfigError(
                f"{instrument_id} trades in {expected}; one run uses one currency ({currency})"
            )
    timeframe = request.get("timeframe", "1d")
    if not isinstance(timeframe, str) or not TIMEFRAME.fullmatch(timeframe):
        raise RunConfigError("timeframe must be like 5m, 1d or 1w")
    start, end = (
        _date(request.get("start_date"), "start_date"),
        _date(request.get("end_date"), "end_date"),
    )
    if end < start:
        raise RunConfigError("end_date must not be before start_date")
    as_of = request.get("as_of_utc")
    if as_of is not None:
        try:
            moment = datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
        except ValueError as exc:
            raise RunConfigError("as_of_utc must be a UTC time") from exc
        if moment.utcoffset() is None or moment.utcoffset().total_seconds() != 0:  # type: ignore[union-attr]
            raise RunConfigError("as_of_utc must be a UTC time")
        as_of = moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    adjustment = request.get("adjustment", "raw")
    if adjustment not in ADJUSTMENTS:
        raise RunConfigError("adjustment must be raw, split_adjusted or fully_adjusted")
    if adjustment != "raw" and timeframe not in ("1d", "1w"):
        raise RunConfigError("adjusted prices are available for daily and weekly runs")
    if adjustment == "fully_adjusted" and currency == "KRW":
        # Toss's adjusted Korean candles leave cash dividends out (toss.py).
        raise RunConfigError("Korean runs offer split_adjusted or raw prices")
    seed = request.get("seed", 0)
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise RunConfigError("seed must be a whole number")
    bar_count = request.get("bar_count")
    if bar_count is not None and (
        isinstance(bar_count, bool)
        or not isinstance(bar_count, int)
        or not 1 <= bar_count <= MAX_BAR_COUNT
    ):
        raise RunConfigError(f"bar_count must be a whole number from 1 to {MAX_BAR_COUNT}")
    config = {
        "instrument_ids": list(ids),
        "timeframe": timeframe,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "initial_capital": _decimal(
            request.get("initial_capital"), "initial_capital", Decimal(1), Decimal("1e12")
        ),
        "currency": currency,
        "fee_rate": _decimal(request.get("fee_rate", 0), "fee_rate", Decimal(0), Decimal("0.1")),
        "fee_per_order": _decimal(
            request.get("fee_per_order", 0), "fee_per_order", Decimal(0), Decimal("1e6")
        ),
        "slippage_bps": _decimal(
            request.get("slippage_bps", 0), "slippage_bps", Decimal(0), Decimal(1000)
        ),
        "fill_policy": "next_eligible_bar_open",
        "adjustment": adjustment,
        "as_of_utc": as_of,
        "seed": seed,
    }
    if bar_count is not None:
        config["bar_count"] = bar_count
    if request.get("walk_forward") is not None:
        try:
            config["walk_forward"] = clean_spec(request["walk_forward"])
        except WalkForwardError as exc:
            raise RunConfigError(str(exc)) from exc
    return config


class BacktestRunner:
    def __init__(
        self,
        store: BacktestStore,
        get_bars: Callable[[], GetBars],
        process: Callable[..., StrategyProcess] = StrategyProcess,
        get_adjusted: Callable[[], GetAdjusted | None] = lambda: None,
        indicators: Callable[[], CustomIndicatorStore | None] = lambda: None,
    ) -> None:
        self.store = store
        self._indicators = indicators
        self._get_bars = get_bars
        self._get_adjusted = get_adjusted
        self._process = process
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._cancel: set[str] = set()
        self._lock = threading.Lock()
        self.store.fail_interrupted()
        self._thread = threading.Thread(target=self._loop, name="backtest-runner", daemon=True)
        self._thread.start()

    def custom_indicator(self, indicator_id: str, version: int) -> dict[str, Any]:
        """A saved Python indicator version (ADR 0023), for `py:` rule operands."""
        indicators = self._indicators()
        if indicators is None:
            raise LookupError("the indicator store is not configured (set SIW_DATA_DIR)")
        return indicators.indicator(indicator_id, version)

    def save_strategy(
        self, name: str, definition: object, strategy_id: str | None = None
    ) -> dict[str, Any]:
        return self.store.save_strategy(name, definition, strategy_id, self.custom_indicator)

    def submit(
        self, strategy_id: str, version: int | None, request: dict[str, Any]
    ) -> dict[str, Any]:
        strategy = self.store.strategy(strategy_id, version)
        run = self.store.create_run(strategy, validate_config(request))
        self._wake.set()
        return run

    def cancel(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            self._cancel.add(run_id)
        return self.store.request_cancel(run_id)

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """For tests: wait until no queued or running run is left."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if all(run["status"] not in ("queued", "running") for run in self.store.runs()):
                return True
            self._stop.wait(0.02)
        return False

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=15)

    def _cancelled(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._cancel or self._stop.is_set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=5)
            self._wake.clear()
            while not self._stop.is_set():
                queued = self.store.queued_run_ids()
                if not queued:
                    break
                self._execute(queued[0])

    def _execute(self, run_id: str) -> None:
        if not self.store.mark_running(run_id):
            return
        try:
            results = self._results(run_id)
        except Cancelled:
            self.store.finish(run_id, "cancelled")
        except (StrategyError, SandboxError, MarketDataError, WalkForwardError, ValueError) as exc:
            self.store.finish(run_id, "failed", error=str(exc) or type(exc).__name__)
        except Exception as exc:  # noqa: BLE001  (stored, never raised into the thread)
            LOGGER.exception("backtest %s failed", run_id)
            self.store.finish(run_id, "failed", error=f"internal error ({type(exc).__name__})")
        else:
            self.store.finish(run_id, "succeeded", results=results)
        finally:
            with self._lock:
                self._cancel.discard(run_id)

    def _results(self, run_id: str) -> dict[str, Any]:
        run = self.store.run(run_id)
        config = run["config"]
        strategy = self.store.strategy(run["strategy_id"], run["strategy_version"])
        definition = strategy["definition"]
        get_bars = self._get_bars()
        as_of = config["as_of_utc"]
        inputs = []
        for instrument_id in config["instrument_ids"]:
            if self._cancelled(run_id):
                raise Cancelled
            inputs.append(
                load_instrument(
                    get_bars,
                    instrument_id,
                    config["timeframe"],
                    date.fromisoformat(config["start_date"]),
                    date.fromisoformat(config["end_date"]),
                    None if as_of is None else datetime.fromisoformat(as_of.replace("Z", "+00:00")),
                    # Runs stored before adjusted prices existed used raw prices.
                    config.get("adjustment", "raw"),
                    self._get_adjusted(),
                    config.get("bar_count"),
                )
            )
        engine_config = EngineConfig(
            Decimal(config["initial_capital"]),
            config["currency"],
            Decimal(config["fee_rate"]),
            Decimal(config["fee_per_order"]),
            Decimal(config["slippage_bps"]),
        )
        if config.get("walk_forward") is not None:
            self.store.set_progress(run_id, 0.1)
            results = WalkForward(
                run_id,
                definition,
                config["walk_forward"],
                inputs,
                engine_config,
                lambda chosen: RuleSignals(chosen, inputs, self.custom_indicator, self._process),
                custom=self.custom_indicator,
                cancelled=lambda: self._cancelled(run_id),
                progress=lambda fraction: self.store.set_progress(run_id, 0.1 + 0.9 * fraction),
            ).run()
            return self._finish(results)
        source: SignalSource
        if definition["type"] == "rules":
            source = RuleSignals(definition, inputs, self.custom_indicator, self._process)
        else:
            source = PythonSignals(definition, inputs, self._process)
        engine = Backtest(
            run_id,
            definition,
            inputs,
            engine_config,
            source,
            cancelled=lambda: self._cancelled(run_id),
            progress=lambda fraction: self.store.set_progress(run_id, 0.1 + 0.9 * fraction),
        )
        self.store.set_progress(run_id, 0.1)
        results = engine.run()
        if isinstance(source, PythonSignals):
            results["logs"] = source.logs[-200:]
        if isinstance(source, RuleSignals) and source.custom_pins:
            results["custom_indicators"] = source.custom_pins
        return self._finish(results)

    @staticmethod
    def _finish(results: dict[str, Any]) -> dict[str, Any]:
        results["warnings"].append(SURVIVORSHIP)
        results["warning_details"].append(
            {"code": "survivorship", "instrument_id": None, "count": None, "dates": []}
        )
        validate_document(results)
        return results
