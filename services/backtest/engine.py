"""Deterministic multi-symbol, long-only portfolio backtest (ADR 0021).

At each bar time, across instruments in ID order:
  1. orders queued at an earlier close fill, sells before buys: market orders at this
     bar's open; limit and stop orders when this bar trades through their price (a gap
     fills at the open), otherwise they wait until their expiry in bars (ADR 0031);
  2. stops and targets are checked against this bar's high and low;
  3. positions are marked to the close;
  4. a final bar is shown to the strategy, whose orders wait for the instrument's next bar.

Money is Decimal throughout. Nothing here is random; identical inputs give identical
results. Fills stay in the result document; nothing is written to any ledger.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal
from typing import Any, Protocol

from services.backtest.data import EngineBar, InstrumentInput
from services.backtest.indicators import Bar, Series
from services.backtest.sandbox import StrategyProcess
from services.backtest.strategy import (
    CustomLookup,
    RuleEvaluator,
    StrategyError,
    canonical,
    parse_custom,
)
from services.custom_indicators import runner as custom_runner

ENGINE_VERSION = "siw-backtest-1"
FILL_MODEL = "next-open-slippage-v1"
# Limit fills need a trade through the limit (a touch is not enough) and take no slippage;
# stop fills take slippage from the stop price or from a gapped open.
ORDER_FILL_MODEL = "limit-stop-v1"
ORDER_TYPES = ("market", "limit", "stop")
MAX_EXPIRE_BARS = 100
MONEY = Decimal("0.01")
PRICE = Decimal("0.000001")
RATIO = Decimal("0.000001")
HUNDRED = Decimal(100)
BPS = Decimal(10_000)

METRIC_DEFINITIONS = {
    "net_return": "final equity / initial capital - 1, after fees and slippage",
    "gross_return": "(final equity + fees + slippage cost) / initial capital - 1",
    "max_drawdown": "largest fall from a running equity peak, as a fraction of that peak",
    "trade_count": "closed round trips (positions still open at the end are not counted)",
    "win_rate": "closed trades with positive net P&L / closed trades",
    "average_win": "mean net P&L of winning closed trades",
    "average_loss": "mean net P&L of losing closed trades",
    "profit_factor": "sum of wins / |sum of losses| (null without losses)",
    "exposure": "share of bar times with at least one open position",
    "turnover": "traded notional / mean equity",
    "benchmark_return": "equal-weight buy and hold of every instrument at its first open, no costs",
}


class Cancelled(Exception):
    """The run was cancelled."""


@dataclass(frozen=True)
class EngineConfig:
    initial_capital: Decimal
    currency: str
    fee_rate: Decimal = Decimal(0)
    fee_per_order: Decimal = Decimal(0)
    slippage_bps: Decimal = Decimal(0)


@dataclass
class Order:
    order_id: str
    instrument_id: str
    side: str
    signal_time: int
    quantity: int | None = None  # fixed shares (buy) or shares to sell (None: all)
    cash_target: Decimal | None = None
    order_type: str = "market"
    price: Decimal | None = None  # the limit or stop price
    expire_bars: int = 1  # bars a limit or stop order waits before it expires
    waited: int = 0
    status: str = "queued"
    reason: str | None = None
    fill_time: int | None = None


@dataclass
class Trade:
    instrument_id: str
    entry_order_id: str
    entry_time: int
    entry_price: Decimal
    quantity: int
    cost: Decimal  # shares x price + entry fee
    exits: list[tuple[int, int, Decimal, Decimal, str]] = field(default_factory=list)


@dataclass
class Position:
    quantity: int
    cost: Decimal
    trade: Trade
    stop: Decimal | None
    target: Decimal | None
    # Filled inside a bar (limit or stop): that bar's earlier prices may predate the entry.
    intrabar_entry: bool = False


class SignalSource(Protocol):
    def signals(
        self, instrument_id: str, index: int, book: dict[str, Any]
    ) -> list[dict[str, Any]]: ...

    def close(self) -> None: ...


def _indicator_bars(bars: Sequence[EngineBar]) -> list[Bar]:
    return [
        Bar(
            bar.time,
            float(bar.open),
            float(bar.high),
            float(bar.low),
            float(bar.close),
            float(bar.volume),
        )
        for bar in bars
    ]


def _operands(condition: dict[str, Any] | None) -> Iterator[dict[str, Any]]:
    if condition is None:
        return
    for group in ("all", "any"):
        if group in condition:
            for item in condition[group]:
                yield from _operands(item)
            return
    yield condition["left"]
    yield condition["right"]


def _custom_bars(bars: Sequence[EngineBar]) -> list[dict[str, Any]]:
    return [
        {
            "time": bar.time,
            "open": float(bar.open),
            "high": float(bar.high),
            "low": float(bar.low),
            "close": float(bar.close),
            "volume": float(bar.volume),
        }
        for bar in bars
    ]


class RuleSignals:
    """Rules over each instrument's final bars.

    Python indicator operands (`py:<id>@<version>`, ADR 0023) are computed once per
    instrument and setting, before the run, in the indicator sandbox: `update(ctx)` sees
    one bar at a time, so each value depends only on that bar and earlier ones. The code
    of every version used is pinned by SHA-256 in `custom_pins`.
    """

    def __init__(
        self,
        definition: dict[str, Any],
        inputs: Sequence[InstrumentInput],
        custom: CustomLookup | None = None,
        process: Callable[..., StrategyProcess] = StrategyProcess,
    ) -> None:
        self.definition = definition
        operands = [
            operand
            for rule in (definition["entry"], definition["exit"])
            for operand in _operands(rule)
            if "indicator" in operand and parse_custom(operand["indicator"]) is not None
        ]
        saved = {operand["indicator"]: self._resolve(operand, custom) for operand in operands}
        self.custom_pins = [
            {
                "type": item["type"],
                "indicator_id": item["indicator_id"],
                "version": item["version"],
                "name": item["name"],
                "code_sha256": item["code_sha256"],
            }
            for _type, item in sorted(saved.items())
        ]
        self.evaluators: dict[str, RuleEvaluator] = {}
        for item in inputs:
            final = [b for b in item.bars if b.final]
            self.evaluators[item.instrument_id] = RuleEvaluator(
                definition,
                _indicator_bars(final),
                item.time_zone,
                self._custom_series(operands, saved, final, item, process),
            )

    @staticmethod
    def _resolve(operand: dict[str, Any], custom: CustomLookup | None) -> dict[str, Any]:
        parsed = parse_custom(operand["indicator"])
        assert parsed is not None
        if custom is None:
            raise StrategyError("Python indicators need the indicator store (set SIW_DATA_DIR)")
        try:
            item = custom(*parsed)
        except LookupError as exc:
            raise StrategyError(f"{operand['indicator']} is not a saved version") from exc
        if hashlib.sha256(item["code"].encode()).hexdigest() != item["code_sha256"]:
            raise StrategyError(f"{operand['indicator']} code does not match its SHA-256")
        return item

    @staticmethod
    def _custom_series(
        operands: list[dict[str, Any]],
        saved: dict[str, dict[str, Any]],
        final: Sequence[EngineBar],
        item: InstrumentInput,
        process: Callable[..., StrategyProcess],
    ) -> dict[str, Series]:
        series: dict[str, Series] = {}
        computed: dict[str, dict[str, list[float | None]]] = {}
        bars = _custom_bars(final)
        for operand in operands:
            run_key = canonical({"indicator": operand["indicator"], "params": operand["params"]})
            if run_key not in computed:
                indicator = saved[operand["indicator"]]
                if not bars:
                    computed[run_key] = {name: [] for name in indicator["outputs"]}
                else:
                    try:
                        computed[run_key] = custom_runner.compute(
                            indicator["code"],
                            indicator,
                            operand["params"],
                            bars,
                            item.time_zone,
                            process,
                        )
                    except custom_runner.IndicatorError as exc:
                        raise custom_runner.IndicatorError(
                            f"{operand['indicator']} failed on {item.instrument_id}: {exc}"
                        ) from exc
            series[canonical(operand)] = computed[run_key][operand["output"]]
        return series

    def signals(self, instrument_id: str, index: int, book: dict[str, Any]) -> list[dict[str, Any]]:
        evaluator = self.evaluators[instrument_id]
        exit_rule = self.definition["exit"]
        if book["position"] is not None:
            if exit_rule is not None and evaluator.holds(exit_rule, index):
                return [{"side": "sell", **_order_intent(self.definition.get("exit_order"))}]
            return []
        if evaluator.holds(self.definition["entry"], index):
            sizing = self.definition["sizing"]
            key = {"percent_equity": "percent_equity", "fixed_cash": "cash"}.get(
                sizing["mode"], "quantity"
            )
            return [
                {
                    "side": "buy",
                    key: sizing["value"],
                    **_order_intent(self.definition.get("entry_order")),
                }
            ]
        return []

    def close(self) -> None:
        return None


def _order_intent(order: dict[str, Any] | None) -> dict[str, Any]:
    """A rules strategy's entry or exit order as intent fields (market when absent)."""
    if order is None or order["type"] == "market":
        return {}
    return {
        "order_type": order["type"],
        "offset_pct": order["offset_pct"],
        "expire_bars": order["expire_bars"],
    }


class PythonSignals:
    def __init__(
        self,
        definition: dict[str, Any],
        inputs: Sequence[InstrumentInput],
        process: Callable[..., StrategyProcess] = StrategyProcess,
    ) -> None:
        self.bars = {item.instrument_id: [b for b in item.bars if b.final] for item in inputs}
        zone = inputs[0].time_zone if inputs else "America/New_York"
        self.process = process(definition["code"], definition.get("params", {}), zone)
        # Index of the last bar each instrument's strategy has seen.
        self.seen: dict[str, int] = {}

    @property
    def logs(self) -> list[str]:
        return self.process.logs

    def signals(self, instrument_id: str, index: int, book: dict[str, Any]) -> list[dict[str, Any]]:
        # A run that starts after the first bar (a walk-forward window) first hands the
        # strategy the bars it skipped, as history only: ctx.history is always complete.
        skipped = self.bars[instrument_id][self.seen.get(instrument_id, -1) + 1 : index]
        if skipped:
            self.process.call(
                {
                    "type": "history",
                    "instrument_id": instrument_id,
                    "bars": [_bar_message(item) for item in skipped],
                }
            )
        self.seen[instrument_id] = index
        bar = self.bars[instrument_id][index]
        reply = self.process.call(
            {
                "type": "bar",
                "instrument_id": instrument_id,
                "bar": _bar_message(bar),
                **book,
            }
        )
        intents = reply.get("intents")
        return intents if isinstance(intents, list) else []

    def close(self) -> None:
        self.process.close()


def _bar_message(bar: EngineBar) -> dict[str, Any]:
    return {
        "time": bar.time,
        "open": str(bar.open),
        "high": str(bar.high),
        "low": str(bar.low),
        "close": str(bar.close),
        "volume": str(bar.volume),
    }


def _utc(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def _money(value: Decimal) -> str:
    return format(value.quantize(MONEY, rounding=ROUND_HALF_EVEN), "f")


def _price(value: Decimal) -> str:
    return format(value.quantize(PRICE, rounding=ROUND_HALF_EVEN).normalize(), "f")


def _ratio(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.quantize(RATIO, rounding=ROUND_HALF_EVEN), "f")


def _number(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = Decimal(str(value))
    except ArithmeticError:
        return None
    return number if number.is_finite() else None


class Backtest:
    def __init__(
        self,
        run_id: str,
        definition: dict[str, Any],
        inputs: Sequence[InstrumentInput],
        config: EngineConfig,
        source: SignalSource,
        cancelled: Callable[[], bool] = lambda: False,
        progress: Callable[[float], None] = lambda _fraction: None,
        trade_window: tuple[int, int] | None = None,
    ) -> None:
        """`trade_window` [start, end) in epoch seconds limits the run to those bar times:
        earlier bars only warm indicators up, and the run ends before `end` (walk-forward
        windows, ADR 0032)."""
        self.run_id = run_id
        self.trade_window = trade_window
        self.definition = definition
        self.inputs = sorted(inputs, key=lambda item: item.instrument_id)
        self.config = config
        self.source = source
        self.cancelled = cancelled
        self.progress = progress
        self.cash = config.initial_capital
        self.positions: dict[str, Position] = {}
        self.pending: dict[str, list[Order]] = {}
        self.orders: list[Order] = []
        self.fills: list[dict[str, Any]] = []
        self.trades: list[Trade] = []
        self.last_close: dict[str, Decimal] = {}
        self.fees = Decimal(0)
        self.slippage_cost = Decimal(0)
        self.traded_notional = Decimal(0)
        self.curve: list[dict[str, Any]] = []
        self.slip = config.slippage_bps / BPS
        sl, tp = definition.get("stop_loss_pct"), definition.get("take_profit_pct")
        self.stop_pct = None if sl is None else Decimal(sl) / HUNDRED
        self.target_pct = None if tp is None else Decimal(tp) / HUNDRED
        self.max_positions = int(definition.get("max_positions", 10))

    # --- book keeping -------------------------------------------------------------

    def equity(self) -> Decimal:
        held = sum(
            (Decimal(p.quantity) * self.last_close[i] for i, p in self.positions.items()),
            Decimal(0),
        )
        return self.cash + held

    def _fee(self, notional: Decimal) -> Decimal:
        return notional * self.config.fee_rate + self.config.fee_per_order

    def _new_order(self, instrument_id: str, side: str, time: int) -> Order:
        order = Order(f"{self.run_id}-o{len(self.orders) + 1}", instrument_id, side, time)
        self.orders.append(order)
        return order

    def _reject(self, order: Order, reason: str) -> None:
        order.status = "rejected"
        order.reason = reason

    def _record_fill(
        self,
        order: Order,
        quantity: int,
        price: Decimal,
        fee: Decimal,
        time: int,
        reason: str,
        reference: Decimal,
        notes: list[str],
    ) -> None:
        notional = Decimal(quantity) * price
        self.fees += fee
        self.slippage_cost += abs(price - reference) * Decimal(quantity)
        self.traded_notional += notional
        fill: dict[str, Any] = {
            "kind": "backtest_fill",
            "schema_version": "0.1",
            "fill_id": f"{order.order_id}-f",
            "run_id": self.run_id,
            "order_id": order.order_id,
            "instrument_id": order.instrument_id,
            "side": order.side,
            "quantity": str(quantity),
            "price": _price(price),
            "fee": _money(fee),
            "currency": self.config.currency,
            "filled_at_utc": _utc(time),
            "fill_model_version": FILL_MODEL
            if order.order_type == "market"
            else f"{FILL_MODEL}+{ORDER_FILL_MODEL}",
            "fill_reason": reason,
        }
        if notes:
            fill["notes"] = notes
        self.fills.append(fill)
        order.status = "filled"
        order.fill_time = time

    # --- fills ----------------------------------------------------------------------

    def _sell(
        self,
        order: Order,
        quantity: int,
        reference: Decimal,
        time: int,
        reason: str,
        notes: list[str],
        price: Decimal | None = None,
    ) -> None:
        position = self.positions[order.instrument_id]
        price = reference * (1 - self.slip) if price is None else price
        proceeds = Decimal(quantity) * price
        fee = self._fee(proceeds)
        self.cash += proceeds - fee
        share_cost = position.cost * Decimal(quantity) / Decimal(position.quantity)
        position.cost -= share_cost
        position.quantity -= quantity
        position.trade.exits.append((time, quantity, proceeds, fee, reason))
        self._record_fill(order, quantity, price, fee, time, reason, reference, notes)
        if position.quantity == 0:
            del self.positions[order.instrument_id]
            self.trades.append(position.trade)

    def _trigger(self, order: Order, bar: EngineBar) -> tuple[Decimal, Decimal, str] | None:
        """(reference price, fill price, fill reason) when the order fills on this bar."""
        buy = order.side == "buy"

        def slipped(price: Decimal) -> Decimal:
            return price * (1 + self.slip) if buy else price * (1 - self.slip)

        if order.order_type == "market":
            return bar.open, slipped(bar.open), "signal"
        level = order.price
        assert level is not None
        if order.order_type == "limit":
            # Never worse than the limit; a touch is not a fill, a trade through is.
            if buy and bar.open <= level:
                return bar.open, min(slipped(bar.open), level), "limit_order"
            if not buy and bar.open >= level:
                return bar.open, max(slipped(bar.open), level), "limit_order"
            if (buy and bar.low < level) or (not buy and bar.high > level):
                return level, level, "limit_order"
            return None
        if (buy and bar.open >= level) or (not buy and bar.open <= level):
            return bar.open, slipped(bar.open), "stop_order"  # gapped through the stop
        if (buy and bar.high >= level) or (not buy and bar.low <= level):
            return level, slipped(level), "stop_order"
        return None

    def _fill_sell(self, order: Order, bar: EngineBar) -> bool:
        """False when a limit or stop order did not trigger on this bar."""
        position = self.positions.get(order.instrument_id)
        if position is None:
            self._reject(order, "no_position")
            return True
        triggered = self._trigger(order, bar)
        if triggered is None:
            return False
        reference, price, reason = triggered
        quantity = position.quantity if order.quantity is None else order.quantity
        if quantity > position.quantity:
            quantity = position.quantity
        self._sell(order, quantity, reference, bar.time, reason, [], price)
        return True

    def _fill_buy(self, order: Order, bar: EngineBar) -> bool:
        """False when a limit or stop order did not trigger on this bar."""
        if order.instrument_id in self.positions:
            self._reject(order, "already_in_position")
            return True
        triggered = self._trigger(order, bar)
        if triggered is None:
            return False
        reference, price, reason = triggered
        notes: list[str] = []
        if order.quantity is not None:
            quantity = order.quantity
            if Decimal(quantity) * price + self._fee(Decimal(quantity) * price) > self.cash:
                self._reject(order, "insufficient_cash")
                return True
        else:
            assert order.cash_target is not None
            budget = order.cash_target
            if budget > self.cash:
                budget = self.cash
                notes.append("reduced_to_available_cash")
            per_share = price * (1 + self.config.fee_rate)
            spendable = budget - self.config.fee_per_order
            quantity = int((spendable / per_share).to_integral_value(rounding=ROUND_DOWN))
            if spendable <= 0 or quantity <= 0:
                self._reject(order, "insufficient_cash" if notes else "quantity_rounds_to_zero")
                return True
        notional = Decimal(quantity) * price
        fee = self._fee(notional)
        self.cash -= notional + fee
        trade = Trade(
            order.instrument_id, order.order_id, bar.time, price, quantity, notional + fee
        )
        self.positions[order.instrument_id] = Position(
            quantity,
            notional + fee,
            trade,
            None if self.stop_pct is None else price * (1 - self.stop_pct),
            None if self.target_pct is None else price * (1 + self.target_pct),
            intrabar_entry=reference != bar.open,
        )
        self._record_fill(order, quantity, price, fee, bar.time, reason, reference, notes)
        return True

    def _protect(self, instrument_id: str, bar: EngineBar) -> None:
        position = self.positions.get(instrument_id)
        if position is None:
            return
        stop_hit = position.stop is not None and bar.low <= position.stop
        target_hit = position.target is not None and bar.high >= position.target
        if not stop_hit and not target_hit:
            return
        order = self._new_order(instrument_id, "sell", bar.time)
        notes: list[str] = []
        if position.intrabar_entry and position.trade.entry_time == bar.time:
            # Entered inside this bar: its low or high may have come before the entry.
            notes.append("same_bar_as_entry")
        if stop_hit:
            assert position.stop is not None
            # A gap through the stop fills at the open, not at the stop.
            reference = min(bar.open, position.stop)
            reason = "stop_loss"
            if target_hit:
                # The order of high and low inside a bar is unknown: assume the stop.
                notes.append("ambiguous_stop_target")
        else:
            assert position.target is not None
            reference = max(bar.open, position.target)
            reason = "take_profit"
        self._sell(order, position.quantity, reference, bar.time, reason, notes)

    # --- signals --------------------------------------------------------------------

    def _queue(self, instrument_id: str, intents: list[dict[str, Any]], time: int) -> None:
        for intent in intents[:10]:
            side = intent.get("side") if isinstance(intent, dict) else None
            order = self._new_order(instrument_id, str(side)[:10], time)
            if side not in ("buy", "sell"):
                order.side = "buy"
                self._reject(order, "invalid_intent")
                continue
            target = intent.get("instrument_id", instrument_id)
            if target != instrument_id:
                self._reject(order, "other_instrument")
                continue
            queued = self.pending.setdefault(instrument_id, [])
            if side == "sell":
                if instrument_id not in self.positions:
                    self._reject(order, "no_position")
                elif any(o.side == "sell" for o in queued):
                    self._reject(order, "duplicate")
                else:
                    quantity = intent.get("quantity")
                    if quantity is not None:
                        number = _number(quantity)
                        if number is None or number <= 0 or number != int(number):
                            self._reject(order, "invalid_quantity")
                            continue
                        order.quantity = int(number)
                    if self._order_terms(order, intent):
                        queued.append(order)
                continue
            if instrument_id in self.positions or any(o.side == "buy" for o in queued):
                self._reject(order, "already_in_position")
                continue
            busy = len(self.positions) + sum(
                1 for orders in self.pending.values() for o in orders if o.side == "buy"
            )
            if busy >= self.max_positions:
                self._reject(order, "position_limit")
                continue
            sizes = [k for k in ("quantity", "cash", "percent_equity") if k in intent]
            if len(sizes) != 1:
                self._reject(order, "invalid_size")
                continue
            number = _number(intent[sizes[0]])
            if number is None or number <= 0:
                self._reject(order, "invalid_size")
                continue
            if sizes[0] == "quantity":
                if number != int(number):
                    self._reject(order, "invalid_size")
                    continue
                order.quantity = int(number)
            elif sizes[0] == "cash":
                order.cash_target = number
            else:
                if number > HUNDRED:
                    self._reject(order, "invalid_size")
                    continue
                order.cash_target = self.equity() * number / HUNDRED
            if self._order_terms(order, intent):
                queued.append(order)

    def _order_terms(self, order: Order, intent: dict[str, Any]) -> bool:
        """Order type, price and expiry from an intent; False (and rejected) if invalid.

        A limit or stop price is absolute (`limit_price` / `stop_price`) or relative to
        the signal bar's close (`offset_pct`, e.g. -2 for 2% below it).
        """
        kind = intent.get("order_type", "market")
        if kind not in ORDER_TYPES:
            self._reject(order, "invalid_order_type")
            return False
        order.order_type = str(kind)
        priced = [k for k in ("limit_price", "stop_price", "offset_pct") if k in intent]
        if kind == "market":
            if priced:
                self._reject(order, "invalid_order_price")
                return False
            return True
        allowed = {"limit": ("limit_price", "offset_pct"), "stop": ("stop_price", "offset_pct")}
        if len(priced) != 1 or priced[0] not in allowed[kind]:
            self._reject(order, "invalid_order_price")
            return False
        number = _number(intent[priced[0]])
        if number is None:
            self._reject(order, "invalid_order_price")
            return False
        if priced[0] == "offset_pct":
            if not Decimal(-90) <= number <= Decimal(1000):
                self._reject(order, "invalid_order_price")
                return False
            close = self.last_close[order.instrument_id]
            number = close * (1 + number / HUNDRED)
        if number <= 0:
            self._reject(order, "invalid_order_price")
            return False
        order.price = number.quantize(PRICE)
        expire = intent.get("expire_bars", 1)
        if (
            isinstance(expire, bool)
            or not isinstance(expire, int)
            or not 1 <= expire <= MAX_EXPIRE_BARS
        ):
            self._reject(order, "invalid_expiry")
            return False
        order.expire_bars = expire
        return True

    # --- run ------------------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        bars_at: dict[str, dict[int, EngineBar]] = {
            item.instrument_id: {bar.time: bar for bar in item.bars} for item in self.inputs
        }
        signal_index: dict[str, dict[int, int]] = {
            item.instrument_id: {
                bar.time: k for k, bar in enumerate(b for b in item.bars if b.final)
            }
            for item in self.inputs
        }
        times = sorted({time for bars in bars_at.values() for time in bars})
        if self.trade_window is not None:
            start, end = self.trade_window
            times = [time for time in times if start <= time < end]
        ids = [item.instrument_id for item in self.inputs]
        benchmark_shares: dict[str, Decimal] = {}
        benchmark_cash = self.config.initial_capital
        slice_ = self.config.initial_capital / Decimal(len(ids)) if ids else Decimal(0)
        peak = self.config.initial_capital
        exposed = 0
        try:
            for step, time in enumerate(times):
                if self.cancelled():
                    raise Cancelled
                active = [i for i in ids if time in bars_at[i]]
                for side in ("sell", "buy"):
                    for instrument_id in active:
                        bar = bars_at[instrument_id][time]
                        for order in [
                            o for o in self.pending.get(instrument_id, []) if o.side == side
                        ]:
                            done = (
                                self._fill_sell(order, bar)
                                if side == "sell"
                                else self._fill_buy(order, bar)
                            )
                            if not done:
                                order.waited += 1
                                if order.waited < order.expire_bars:
                                    continue
                                order.status = "expired"
                            self.pending[instrument_id].remove(order)
                for instrument_id in active:
                    self._protect(instrument_id, bars_at[instrument_id][time])
                for instrument_id in active:
                    bar = bars_at[instrument_id][time]
                    self.last_close[instrument_id] = bar.close
                    if instrument_id not in benchmark_shares:
                        benchmark_shares[instrument_id] = slice_ / bar.open
                        benchmark_cash -= slice_
                equity = self.equity()
                peak = max(peak, equity)
                if self.positions:
                    exposed += 1
                benchmark = benchmark_cash + sum(
                    (shares * self.last_close[i] for i, shares in benchmark_shares.items()),
                    Decimal(0),
                )
                self.curve.append(
                    {
                        "time_utc": _utc(time),
                        "equity": _money(equity),
                        "cash": _money(self.cash),
                        "drawdown": _ratio((peak - equity) / peak if peak > 0 else Decimal(0)),
                        "benchmark_equity": _money(benchmark),
                    }
                )
                for instrument_id in active:
                    bar = bars_at[instrument_id][time]
                    if not bar.final:
                        continue
                    position = self.positions.get(instrument_id)
                    book = {
                        "position": None
                        if position is None
                        else {
                            "quantity": str(position.quantity),
                            "average_price": _price(position.trade.entry_price),
                        },
                        "cash": _money(self.cash),
                        "equity": _money(equity),
                        "open_positions": len(self.positions),
                    }
                    pending = self.pending.get(instrument_id, [])
                    if self.definition["type"] == "rules" and pending:
                        continue  # one order at a time per instrument
                    intents = self.source.signals(
                        instrument_id, signal_index[instrument_id][time], book
                    )
                    self._queue(instrument_id, intents, time)
                if step % 50 == 0:
                    self.progress(step / max(1, len(times)))
        finally:
            self.source.close()
        for orders in self.pending.values():
            for order in orders:
                order.status = "unfilled_at_end"
        self.progress(1.0)
        return self._results(exposed, len(times))

    # --- results --------------------------------------------------------------------

    def _trade_document(self, trade: Trade, open_quantity: int) -> dict[str, Any]:
        proceeds = sum((e[2] for e in trade.exits), Decimal(0))
        exit_fees = sum((e[3] for e in trade.exits), Decimal(0))
        sold = sum(e[1] for e in trade.exits)
        mark = self.last_close.get(trade.instrument_id, trade.entry_price)
        unrealized = Decimal(open_quantity) * mark
        pnl = proceeds - exit_fees + unrealized - trade.cost
        return {
            "instrument_id": trade.instrument_id,
            "entry_order_id": trade.entry_order_id,
            "entry_utc": _utc(trade.entry_time),
            "entry_price": _price(trade.entry_price),
            "quantity": str(trade.quantity),
            "exit_utc": _utc(trade.exits[-1][0]) if trade.exits and not open_quantity else None,
            "exit_price": _price(proceeds / Decimal(sold)) if sold else None,
            "exit_reason": "open_at_end" if open_quantity else trade.exits[-1][4],
            "open_quantity": str(open_quantity),
            "net_pnl": _money(pnl),
            "return": _ratio(pnl / trade.cost) if trade.cost else None,
        }

    def _fill_model(self) -> str:
        used = any(o.order_type != "market" for o in self.orders)
        return f"{FILL_MODEL}+{ORDER_FILL_MODEL}" if used else FILL_MODEL

    def _results(self, exposed: int, steps: int) -> dict[str, Any]:
        initial = self.config.initial_capital
        final = self.equity()
        closed = [self._trade_document(t, 0) for t in self.trades]
        still_open = [self._trade_document(p.trade, p.quantity) for p in self.positions.values()]
        pnl = [Decimal(t["net_pnl"]) for t in closed]
        wins = [p for p in pnl if p > 0]
        losses = [p for p in pnl if p < 0]
        mean_equity = (
            sum((Decimal(point["equity"]) for point in self.curve), Decimal(0))
            / Decimal(len(self.curve))
            if self.curve
            else initial
        )
        drawdown = max((Decimal(point["drawdown"]) for point in self.curve), default=Decimal(0))
        benchmark = Decimal(self.curve[-1]["benchmark_equity"]) if self.curve else initial
        metrics = {
            "net_return": _ratio(final / initial - 1),
            "gross_return": _ratio((final + self.fees + self.slippage_cost) / initial - 1),
            "max_drawdown": _ratio(drawdown),
            "trade_count": len(closed),
            "win_rate": _ratio(Decimal(len(wins)) / Decimal(len(closed))) if closed else None,
            "average_win": _money(sum(wins, Decimal(0)) / len(wins)) if wins else None,
            "average_loss": _money(sum(losses, Decimal(0)) / len(losses)) if losses else None,
            "profit_factor": _ratio(sum(wins, Decimal(0)) / -sum(losses, Decimal(0)))
            if losses
            else None,
            "exposure": _ratio(Decimal(exposed) / Decimal(steps)) if steps else None,
            "turnover": _ratio(self.traded_notional / mean_equity) if mean_equity else None,
            "fees": _money(self.fees),
            "slippage_cost": _money(self.slippage_cost),
            "benchmark_return": _ratio(benchmark / initial - 1),
        }
        per_symbol = []
        for item in self.inputs:
            mine = [t for t in closed if t["instrument_id"] == item.instrument_id]
            mine_pnl = [Decimal(t["net_pnl"]) for t in mine]
            open_pnl = sum(
                (
                    Decimal(t["net_pnl"])
                    for t in still_open
                    if t["instrument_id"] == item.instrument_id
                ),
                Decimal(0),
            )
            per_symbol.append(
                {
                    "instrument_id": item.instrument_id,
                    "trade_count": len(mine),
                    "win_rate": _ratio(
                        Decimal(sum(1 for p in mine_pnl if p > 0)) / Decimal(len(mine))
                    )
                    if mine
                    else None,
                    "realized_pnl": _money(sum(mine_pnl, Decimal(0))),
                    "unrealized_pnl": _money(open_pnl),
                }
            )
        return {
            "kind": "backtest_results",
            "schema_version": "0.1",
            "run_id": self.run_id,
            "engine_version": ENGINE_VERSION,
            "fill_model_version": self._fill_model(),
            "currency": self.config.currency,
            "initial_capital": _money(initial),
            "final_equity": _money(final),
            "metrics": metrics,
            "metric_definitions": METRIC_DEFINITIONS,
            "equity_curve": self.curve,
            "orders": [
                {
                    "order_id": o.order_id,
                    "instrument_id": o.instrument_id,
                    "side": o.side,
                    "signal_utc": _utc(o.signal_time),
                    "status": o.status,
                    "reason": o.reason,
                    "filled_at_utc": None if o.fill_time is None else _utc(o.fill_time),
                    **(
                        {}
                        if o.order_type == "market"
                        else {
                            "order_type": o.order_type,
                            "price": None if o.price is None else _price(o.price),
                            "expire_bars": o.expire_bars,
                        }
                    ),
                }
                for o in self.orders
            ],
            "fills": self.fills,
            "trades": closed + still_open,
            "per_symbol": per_symbol,
            "inputs": [item.pin for item in self.inputs],
            "warnings": [w for item in self.inputs for w in item.warnings],
            "warning_details": [d for item in self.inputs for d in item.warning_details],
            "logs": [],
        }
