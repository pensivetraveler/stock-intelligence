"""The paper-order runner's core loop (ADR 0037 §2, §5, §6), with every outside part injected.

One `step(now)`:
  1. writes a heartbeat;
  2. settles submissions whose outcome is unknown against the broker's list of today's
     orders, before anything new is sent;
  3. stores the fills of closed paper orders;
  4. unless halted, shows each approved instrument's latest final bar to the strategy and
     turns its signals into intents → limit check → kill-switch check → paper order.

The bar feed, the broker adapter and the clock come from outside, so the loop runs
against fixtures and a fake paper server in tests. Wiring it to real bars, the keychain
and launchd is a later step (P3); P2 sends nothing anywhere by itself.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

from services.backtest.data import EngineBar
from services.paper_trading.brokers.base import OrderNotSupportedError, PaperBroker
from services.paper_trading.intent import (
    FxRate,
    OrderSpec,
    SignalData,
    Sizing,
    StrategyRef,
    build_intent,
    market_of,
)
from services.paper_trading.limits import PaperLimits, RunnerFacts
from services.paper_trading.signals import rule_signals
from services.paper_trading.store import PaperOrderStore, _utc

SizingMethod = Literal["shares", "cash", "percent_equity"]
HUNDRED = Decimal(100)
CENT = Decimal("0.01")
SUB_CENT = Decimal("0.0001")


@dataclass(frozen=True)
class InstrumentPlan:
    instrument_id: str
    symbol: str
    time_zone: str


@dataclass(frozen=True)
class RunPlan:
    """What the user approves; its content hash is the approval ID (ADR 0037 §3)."""

    strategy: StrategyRef
    definition: dict[str, Any]
    instruments: tuple[InstrumentPlan, ...]
    limits: PaperLimits
    broker_id: Literal["kiwoom", "kis"]
    broker_account_ref: str
    market_buffer_bps: Decimal = Decimal("50")
    fee_rate: Decimal = Decimal("0")
    fx: FxRate | None = None
    # A fixed equity for percent-of-equity sizing, confirmed at approval; None refuses it.
    equity_basis: Decimal | None = None
    max_signal_age_seconds: int = 300

    def approval_content(self) -> dict[str, Any]:
        return {
            "strategy": {
                "strategy_id": self.strategy.strategy_id,
                "strategy_version": self.strategy.strategy_version,
                "definition_sha256": self.strategy.definition_sha256,
            },
            "instruments": [
                [item.instrument_id, item.symbol, item.time_zone] for item in self.instruments
            ],
            "limits": self.limits.document(),
            "broker_id": self.broker_id,
            "broker_account_ref": self.broker_account_ref,
            "market_buffer_bps": str(self.market_buffer_bps),
            "fee_rate": str(self.fee_rate),
            "fx": None
            if self.fx is None
            else [str(self.fx.krw_per_usd), self.fx.source, _utc(self.fx.as_of_utc)],
            "equity_basis": None if self.equity_basis is None else str(self.equity_basis),
            "max_signal_age_seconds": self.max_signal_age_seconds,
        }


@dataclass(frozen=True)
class FeedSnapshot:
    """An instrument's bars up to `data_as_of_utc`, with where they came from."""

    bars: Sequence[EngineBar]
    data_as_of_utc: datetime
    timeframe: str
    source_id: str
    feed_scope: str
    dataset_revision: str
    adjustment: str
    session: str
    calendar_id: str
    calendar_version: str


class BarFeed(Protocol):
    def snapshot(self, instrument_id: str, now: datetime) -> FeedSnapshot | None: ...


@dataclass
class StepReport:
    halted: bool = False
    intents: int = 0
    submitted: int = 0
    blocked: int = 0
    expired: int = 0
    skipped: list[str] = field(default_factory=list)
    reconciled: int = 0
    fills: int = 0


def bars_sha256(bars: Sequence[EngineBar]) -> str:
    rows = [
        [b.time, b.end, str(b.open), str(b.high), str(b.low), str(b.close), str(b.volume), b.final]
        for b in bars
    ]
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()


def _tick(price: Decimal) -> Decimal:
    return CENT if price >= 1 else SUB_CENT


def _epoch(value: int) -> datetime:
    return datetime.fromtimestamp(value, timezone.utc)


class PaperRunner:
    def __init__(
        self,
        store: PaperOrderStore,
        broker: PaperBroker,
        feed: BarFeed,
        plan: RunPlan,
    ) -> None:
        if plan.broker_id != broker.broker_id or plan.broker_account_ref != broker.account_ref:
            raise ValueError("the plan was approved for another broker account")
        self.store = store
        self.broker = broker
        self.feed = feed
        self.plan = plan
        self.approval_id = store.record_approval(plan.approval_content())
        self._seen: dict[str, int] = {}  # last signal bar shown per instrument (epoch)

    # -- one step --------------------------------------------------------------------

    def step(self, now: datetime) -> StepReport:
        report = StepReport()
        self.store.heartbeat()
        if self.store.submissions("pending_response", "unknown"):
            report.reconciled = self._reconcile(now)
        report.fills = self._sync_fills(now)
        status = self.store.status()
        if status["halted"]:
            report.halted = True
            return report
        for item in self.plan.instruments:
            snapshot = self.feed.snapshot(item.instrument_id, now)
            if snapshot is None or not snapshot.bars or not snapshot.bars[-1].final:
                continue
            last = snapshot.bars[-1]
            if self._seen.get(item.instrument_id, -1) >= last.time:
                continue
            self._seen[item.instrument_id] = last.time
            holding = self._position(item.instrument_id) > 0
            for signal in rule_signals(
                self.plan.definition, item.instrument_id, item.time_zone, snapshot.bars, holding
            ):
                self._act(item, snapshot, signal, now, report)
        return report

    # -- signals → orders ------------------------------------------------------------

    def _position(self, instrument_id: str) -> Decimal:
        return self.store.position_quantity(self.plan.broker_account_ref, instrument_id)

    def _order(self, signal: dict[str, Any], close: Decimal, quantity: Decimal) -> OrderSpec | str:
        kind = signal.get("order_type", "market")
        if kind == "market":
            return OrderSpec("market", quantity, market_buffer_bps=self.plan.market_buffer_bps)
        if kind != "limit" or "offset_pct" not in signal:
            return "order_type_not_supported"
        raw = close * (1 + Decimal(str(signal["offset_pct"])) / HUNDRED)
        rounding = ROUND_DOWN if signal["side"] == "buy" else ROUND_UP
        price = raw.quantize(_tick(raw), rounding=rounding)
        if price <= 0:
            return "invalid_order_price"
        return OrderSpec("limit", quantity, limit_price=price)

    def _size(
        self, signal: dict[str, Any], instrument_id: str, price: Decimal
    ) -> tuple[SizingMethod, Decimal, Decimal] | str:
        """(method, its input, whole-share quantity), or why the signal cannot be sized."""
        plan = self.plan
        unit = price * (1 + plan.fee_rate)
        method: SizingMethod
        if signal["side"] == "sell":
            held = self._position(instrument_id)
            if held <= 0:
                return "no_position"
            return "shares", held, held
        if "quantity" in signal:
            method, value = "shares", Decimal(str(signal["quantity"]))
            quantity = value
        elif "cash" in signal:
            method, value = "cash", Decimal(str(signal["cash"]))
            quantity = value / unit
        else:
            method, value = "percent_equity", Decimal(str(signal["percent_equity"]))
            if plan.equity_basis is None:
                return "equity_unknown"
            quantity = plan.equity_basis * value / HUNDRED / unit
        quantity = quantity.to_integral_value(rounding=ROUND_DOWN)
        if quantity <= 0:
            return "quantity_rounds_to_zero"
        return method, value, quantity

    def _act(
        self,
        item: InstrumentPlan,
        snapshot: FeedSnapshot,
        signal: dict[str, Any],
        now: datetime,
        report: StepReport,
    ) -> None:
        plan = self.plan
        bar = snapshot.bars[-1]
        bar_end = _epoch(bar.end)
        sized = self._size(signal, item.instrument_id, bar.close)
        if isinstance(sized, str):
            report.skipped.append(f"{item.instrument_id}:{sized}")
            return
        method, value, quantity = sized
        sizing = Sizing(
            method=method,
            input=value,
            reference_price=bar.close,
            reference_price_source=f"signal bar close ({snapshot.dataset_revision})",
            reference_price_as_of_utc=bar_end,
            fee_rate=plan.fee_rate,
            equity_basis=plan.equity_basis if method == "percent_equity" else None,
            basis_as_of_utc=None,
        )
        order = self._order(signal, bar.close, quantity)
        if isinstance(order, str):
            report.skipped.append(f"{item.instrument_id}:{order}")
            return
        local_day = now.astimezone(ZoneInfo(item.time_zone)).date()
        day_start = datetime.combine(local_day, datetime.min.time(), ZoneInfo(item.time_zone))
        facts = RunnerFacts(
            supported_markets=self.broker.capabilities().markets,
            unknown_submissions=self.store.unknown_submission_count(),
            orders_today=self.store.submissions_between(day_start, day_start + timedelta(days=1)),
            daily_loss_rate=None,
            position_amount_krw=None,
        )
        data = SignalData(
            data_as_of_utc=snapshot.data_as_of_utc,
            signal_bar_start_utc=_epoch(bar.time),
            signal_bar_end_utc=bar_end,
            signal_bar_final=bar.final,
            timeframe=snapshot.timeframe,
            source_id=snapshot.source_id,
            feed_scope=snapshot.feed_scope,
            dataset_revision=snapshot.dataset_revision,
            adjustment=snapshot.adjustment,
            session=snapshot.session,
            input_bar_count=len(snapshot.bars),
            input_bars_sha256=bars_sha256(snapshot.bars),
            calendar_id=snapshot.calendar_id,
            calendar_version=snapshot.calendar_version,
        )
        intent, result = build_intent(
            broker_id=plan.broker_id,
            broker_account_ref=plan.broker_account_ref,
            approval_id=self.approval_id,
            strategy=plan.strategy,
            instrument_id=item.instrument_id,
            symbol=item.symbol,
            side=signal["side"],
            data=data,
            signal_summary=f"rules {signal['side']} signal at the bar close",
            operands=[],
            sizing=sizing,
            order=order,
            fx=plan.fx if market_of(item.instrument_id) == "us" else None,
            limits=plan.limits,
            facts=facts,
            created_at=now,
        )
        if not self.store.record_intent(intent, result):
            return  # this signal was already handled (for example before a restart)
        report.intents += 1
        if not result.passed:
            report.blocked += 1
            return
        if (now - bar_end).total_seconds() > plan.max_signal_age_seconds:
            self.store.set_outcome(intent["intent_id"], "expired", "signal_too_old")
            report.expired += 1
            return
        allowed, reason = self.store.may_submit(self.approval_id)
        if not allowed:
            self.store.set_outcome(intent["intent_id"], "blocked", reason)
            report.blocked += 1
            return
        if order.order_type not in self.broker.capabilities().order_types:
            self.store.set_outcome(intent["intent_id"], "blocked", "order_type_not_supported")
            report.blocked += 1
            return
        self._submit(intent, now)
        report.submitted += 1

    def _submission(
        self,
        intent: dict[str, Any],
        now: datetime,
        status: str,
        operation: str,
        code: str,
        reference: str | None = None,
        response_code: str | None = None,
        message: str | None = None,
    ) -> dict[str, Any]:
        return {
            "kind": "paper_order_submission",
            "schema_version": "0.1",
            "submission_id": f"sub-{intent['intent_id'][:24]}",
            "intent_id": intent["intent_id"],
            "broker_id": intent["broker_id"],
            "broker_account_ref": intent["broker_account_ref"],
            "environment": "demo",
            "operation": operation,
            "broker_order_type_code": code,
            "requested_at_utc": _utc(now),
            "responded_at_utc": None if status in ("pending_response", "unknown") else _utc(now),
            "broker_order_ref": reference,
            "response_code": response_code,
            "response_message": message,
            "status": status,
        }

    def _submit(self, intent: dict[str, Any], now: datetime) -> None:
        operation = "buy" if intent["side"] == "buy" else "sell"
        code = self.broker.order_type_code(intent["order"]["order_type"])
        # Recorded before sending: a crash mid-request leaves it pending, which blocks new
        # orders until it is reconciled.
        pending = self._submission(intent, now, "pending_response", operation, code)
        self.store.record_submission(pending)
        self.store.set_outcome(intent["intent_id"], "submitted")
        try:
            result = self.broker.submit(intent)
        except OrderNotSupportedError as exc:
            rejected = self._submission(
                intent, now, "rejected", operation, code, response_code="not_supported"
            )
            rejected["response_message"] = str(exc)[:200]
            self.store.record_submission(rejected)
            return
        self.store.record_submission(
            self._submission(
                intent,
                now,
                result.status,
                result.operation,
                result.order_type_code,
                reference=result.broker_order_ref,
                response_code=result.response_code,
                message=result.response_message,
            )
        )

    # -- reconciliation and fills ----------------------------------------------------

    def _broker_day(self, moment: datetime) -> Any:
        return moment.astimezone(ZoneInfo(self.broker.time_zone)).date()

    def _reconcile(self, now: datetime) -> int:
        """Settle pending or unknown submissions from today's order list, or halt.

        A submission matches an order nobody else claimed with the same symbol, side,
        quantity and order type. None found: the order was never placed. More than one,
        or a submission from an earlier broker day (today's list cannot show it): the
        kill switch goes on and the user decides.
        """
        orders = self.broker.today_orders()
        claimed = self.store.known_order_refs()
        settled = 0
        for submission in self.store.submissions("pending_response", "unknown"):
            intent = self.store.intent(submission["intent_id"])
            assert intent is not None
            requested = datetime.fromisoformat(
                submission["requested_at_utc"].replace("Z", "+00:00")
            )
            if self._broker_day(requested) != self._broker_day(now):
                self.store.halt("unknown paper order from an earlier day", source="runner")
                continue
            code = self.broker.order_type_code(intent["order"]["order_type"])
            matches = [
                order
                for order in orders
                if order.broker_order_ref not in claimed
                and order.symbol == intent["symbol"]
                and order.side == intent["side"]
                and order.order_quantity == Decimal(intent["order"]["quantity"])
                and order.order_type_code == code
            ]
            if len(matches) > 1:
                self.store.halt("ambiguous unknown paper order", source="runner")
                continue
            base = {**submission, "responded_at_utc": _utc(now)}
            if matches:
                claimed.add(matches[0].broker_order_ref)
                base.update(
                    status="accepted",
                    broker_order_ref=matches[0].broker_order_ref,
                    response_code="reconciled",
                    response_message="found in today's orders",
                )
            else:
                base.update(
                    status="rejected",
                    response_code="not_found_on_reconcile",
                    response_message="not in today's orders: never placed",
                )
            self.store.record_submission(base)
            settled += 1
        return settled

    def _fill_time(self, local: str | None, now: datetime) -> datetime | None:
        """A reported HH:MM:SS on the broker's clock, placed on its latest past date.

        Unverified on the paper server: the time's meaning and its date. A time that
        cannot be read stays unresolved rather than guessed.
        """
        if not local:
            return None
        try:
            clock = datetime.strptime(local, "%H:%M:%S").time()
        except ValueError:
            return None
        zone = ZoneInfo(self.broker.time_zone)
        moment = datetime.combine(self._broker_day(now), clock, zone)
        if moment > now:
            moment -= timedelta(days=1)
        return moment.astimezone(timezone.utc)

    def _sync_fills(self, now: datetime) -> int:
        """Store each closed paper order with fills as one `order_aggregate` execution."""
        waiting = self.store.accepted_without_execution()
        if not waiting:
            return 0
        by_ref = {order.broker_order_ref: order for order in self.broker.today_orders()}
        stored = 0
        for submission in waiting:
            order = by_ref.get(str(submission["broker_order_ref"]))
            if order is None or order.remaining_quantity > 0 or order.filled_quantity <= 0:
                continue
            if order.fill_price is None or order.fill_price <= 0:
                continue
            intent = self.store.intent(submission["intent_id"])
            assert intent is not None
            executed = self._fill_time(order.filled_at_local, now)
            document = {
                "kind": "paper_execution",
                "schema_version": "0.1",
                "execution_id": f"{submission['broker_id']}-paper-{intent['intent_id'][:24]}",
                "broker_id": submission["broker_id"],
                "broker_account_ref": submission["broker_account_ref"],
                "environment": "demo",
                "ledger": "broker_paper",
                "fill_origin": "broker_demo_simulated",
                "intent_id": intent["intent_id"],
                "broker_order_ref": order.broker_order_ref,
                "instrument_id": intent["instrument_id"],
                "symbol": intent["symbol"],
                "side": intent["side"],
                "quantity": format(order.filled_quantity.normalize(), "f"),
                "price": format(order.fill_price.normalize(), "f"),
                "currency": intent["currency"],
                "fees": None,  # ust21510 reports no fees
                "granularity": "order_aggregate",
                "time_meaning": "reported_fill_time" if executed else "time_unresolved",
                "executed_at_utc": None if executed is None else _utc(executed),
                "synced_at_utc": _utc(now),
            }
            if self.store.record_execution(document):
                stored += 1
        return stored
