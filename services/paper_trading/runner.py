"""The paper-order runner's core loop (ADR 0037 §2, §5, §6), with every outside part injected.

One `step(now)`:
  1. writes a heartbeat;
  2. settles submissions whose outcome is unknown against the broker's list of today's
     orders, before anything new is sent;
  3. stores the fills of closed paper orders;
  4. judges each held position's protection on the final bars not judged yet, even under
     the kill switch, and sells what reached a level (ADR 0044);
  5. unless halted, shows each approved instrument's latest final bar to the strategy and
     turns its signals into intents → limit check → kill-switch check → paper order.

The bar feed, the broker adapter and the clock come from outside, so the loop runs
against fixtures and a fake paper server in tests. Wiring it to real bars, the keychain
and launchd is a later step (P3); P2 sends nothing anywhere by itself.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

from services.backtest.data import EngineBar
from services.backtest.protection import AtrHistory, ExitHit, ProtectionRules, entry_levels
from services.backtest.strategy import fingerprint, validate_definition
from services.paper_trading.brokers.base import BrokerOrder, OrderNotSupportedError, PaperBroker
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
from services.paper_trading.protection import ProtectionState, advance, open_state
from services.paper_trading.signals import rule_signals
from services.paper_trading.store import PaperOrderStore, PaperStoreError, _utc

SizingMethod = Literal["shares", "cash", "percent_equity"]
# An unknown order placed this long before or after its request can still be it: clocks
# differ, and a slow reply may be slow to reach the server too.
RECONCILE_BEFORE_SECONDS = 60
RECONCILE_AFTER_SECONDS = 300
SECONDS_PER_DAY = 86400
HUNDRED = Decimal(100)
CENT = Decimal("0.01")
MAX_CANCEL_ATTEMPTS = 3
SUB_CENT = Decimal("0.0001")
PROTECTIVE_FIELDS = ("stop_loss_pct", "take_profit_pct", "stop_loss_atr", "trailing_stop")
# A protective exit goes out past limits and is recorded (ADR 0044, decision 2), but these say
# the order itself cannot be made or reach the broker.
EXIT_STOPPING_REASONS = frozenset(
    {"symbol_not_allowed", "market_not_supported", "invalid_quantity", "price_unavailable"}
)


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
    # "fixed": `fx` above, confirmed at approval. "bok_ecos": the Bank of Korea's latest
    # published USD/KRW rate, read by the runner each day (ADR 0037).
    fx_source: Literal["fixed", "bok_ecos"] = "fixed"
    # A fixed equity for percent-of-equity sizing, confirmed at approval; None refuses it.
    equity_basis: Decimal | None = None
    max_signal_age_seconds: int = 300
    # Seconds after a bar's end after which the runner treats it as final for signals even
    # when the source has not finalized it yet (realtime SIP bars settle quickly; the
    # source's own finality, 15 minutes for Alpaca, stays for charts and caches). None:
    # only the source's finality. A choice per strategy and plan (ADR 0037).
    bar_settle_seconds: int | None = None
    # Seconds an accepted order may stay unfilled before the runner cancels it; the next
    # bar then decides again. None: left to the user (ADR 0044, G1).
    unfilled_cancel_seconds: int | None = None

    def approval_content(self) -> dict[str, Any]:
        content = {
            "strategy": {
                "strategy_id": self.strategy.strategy_id,
                "strategy_version": self.strategy.strategy_version,
                "definition_sha256": self.strategy.definition_sha256,
            },
            "instruments": [
                {"instrument_id": i.instrument_id, "symbol": i.symbol, "time_zone": i.time_zone}
                for i in self.instruments
            ],
            "limits": self.limits.document(),
            "broker_id": self.broker_id,
            "broker_account_ref": self.broker_account_ref,
            "market_buffer_bps": str(self.market_buffer_bps),
            "fee_rate": str(self.fee_rate),
            "fx": None
            if self.fx is None
            else {
                "krw_per_usd": str(self.fx.krw_per_usd),
                "source": self.fx.source,
                "as_of_utc": _utc(self.fx.as_of_utc),
            },
            "fx_source": self.fx_source,
            "equity_basis": None if self.equity_basis is None else str(self.equity_basis),
            "max_signal_age_seconds": self.max_signal_age_seconds,
            "bar_settle_seconds": self.bar_settle_seconds,
        }
        # Only when set, so that plans approved before this existed keep their approval ID.
        if self.unfilled_cancel_seconds is not None:
            content["unfilled_cancel_seconds"] = self.unfilled_cancel_seconds
        # The same: only a plan with protective exits has this, and they ask to be seen.
        if any(self.definition.get(name) is not None for name in PROTECTIVE_FIELDS):
            clean = validate_definition(self.definition)
            content["protection"] = {name: clean.get(name) for name in PROTECTIVE_FIELDS}
        return content


def plan_from_approval(content: dict[str, Any], definition: dict[str, Any]) -> RunPlan:
    """Rebuild an approved plan; the definition must hash to the approved SHA-256."""
    strategy = content["strategy"]
    if fingerprint(definition) != strategy["definition_sha256"]:
        raise ValueError("the strategy definition does not match the approved SHA-256")
    limits = content["limits"]

    def number(value: str | None) -> Decimal | None:
        return None if value is None else Decimal(value)

    fx = content["fx"]
    return RunPlan(
        strategy=StrategyRef(
            strategy["strategy_id"],
            int(strategy["strategy_version"]),
            strategy["definition_sha256"],
            "rules",
        ),
        definition=definition,
        instruments=tuple(
            InstrumentPlan(item["instrument_id"], item["symbol"], item["time_zone"])
            for item in content["instruments"]
        ),
        limits=PaperLimits(
            allowed_instrument_ids=tuple(limits["allowed_instrument_ids"]),
            max_order_amount_krw=number(limits["max_order_amount_krw"]),
            max_daily_orders=limits["max_daily_orders"],
            max_daily_loss_rate=number(limits["max_daily_loss_rate"]),
            max_position_amount_krw=number(limits["max_position_amount_krw"]),
        ),
        broker_id=content["broker_id"],
        broker_account_ref=content["broker_account_ref"],
        market_buffer_bps=Decimal(content["market_buffer_bps"]),
        fee_rate=Decimal(content["fee_rate"]),
        fx=None
        if fx is None
        else FxRate(
            Decimal(fx["krw_per_usd"]),
            fx["source"],
            datetime.fromisoformat(fx["as_of_utc"].replace("Z", "+00:00")),
        ),
        fx_source=content.get("fx_source", "fixed"),
        equity_basis=number(content["equity_basis"]),
        max_signal_age_seconds=int(content["max_signal_age_seconds"]),
        bar_settle_seconds=content.get("bar_settle_seconds"),
        unfilled_cancel_seconds=content.get("unfilled_cancel_seconds"),
    )


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

    def outside(self, instrument_id: str, since: int, now: datetime) -> Sequence[EngineBar]:
        """Bars outside the regular session from `since`, for a position's protection."""
        ...


@dataclass(frozen=True)
class ExitOrder:
    """A protective exit for `_act`: what it reached, in which bar, on what bars known."""

    hit: ExitHit
    signal_bar: EngineBar
    latest_bar: EngineBar  # the order's reference price: `signal_bar` may be old by now
    session: str
    bars: Sequence[EngineBar]
    final_by: Literal["source", "settle"]


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
    cancel_requests: int = 0
    cancelled: int = 0


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
        fx_rate: Callable[[], FxRate | None] | None = None,
    ) -> None:
        if plan.broker_id != broker.broker_id or plan.broker_account_ref != broker.account_ref:
            raise ValueError("the plan was approved for another broker account")
        self.store = store
        self.broker = broker
        self.feed = feed
        self.plan = plan
        self._fx_rate = fx_rate
        self.approval_id = store.record_approval(plan.approval_content())
        self._seen: dict[str, int] = {}  # last signal bar shown per instrument (epoch)
        self._rules: ProtectionRules | None = None

    def _fx(self) -> FxRate | None:
        """The plan's fixed rate, or today's ECOS rate; None (orders blocked) if unknown."""
        if self.plan.fx_source == "fixed":
            return self.plan.fx
        return None if self._fx_rate is None else self._fx_rate()

    def _protection_rules(self) -> ProtectionRules:
        if self._rules is None:
            self._rules = ProtectionRules.of(validate_definition(self.plan.definition))
        return self._rules

    def protects(self) -> bool:
        """This plan's strategy has a stop, a target or a trailing stop."""
        return self._protection_rules().active

    # -- one step --------------------------------------------------------------------

    def step(self, now: datetime) -> StepReport:
        report = StepReport()
        self.store.heartbeat()
        # One read of today's orders per step: the paper server allows about one call
        # per second per api-id.
        today: list[BrokerOrder] | None = None

        def orders() -> list[BrokerOrder]:
            nonlocal today
            if today is None:
                today = self.broker.today_orders()
            return today

        if self.store.submissions("pending_response", "unknown"):
            report.reconciled = self._reconcile(now, orders())
        if self.store.accepted_without_execution():
            report.fills = self._sync_fills(now, orders())
        if self.store.accepted_without_execution():
            report.cancelled = self._settle_cancelled(now, orders())
        protecting = self.protects()
        if protecting:
            self._close_sold()
        report.halted = self.store.status()["halted"]
        if report.halted and not protecting:
            return report
        # After the halt check: the kill switch leaves unfilled orders as they are (ADR 0037).
        if not report.halted and self.plan.unfilled_cancel_seconds is not None:
            report.cancel_requests = self._cancel_stale(now, orders)
        for item in self.plan.instruments:
            if report.halted and self._position(item.instrument_id) <= 0:
                continue  # no bars are read under the kill switch without a position to protect
            snapshot = self.feed.snapshot(item.instrument_id, now)
            if snapshot is None or not snapshot.bars:
                continue
            settled = self._settled(snapshot.bars, now)
            # Bars not final yet (the latest minutes) are left out: the strategy sees the
            # bars up to the latest final one, as in a backtest.
            while settled and not settled[-1].final:
                settled.pop()
            if not settled:
                continue
            final_by: Literal["source", "settle"] = (
                "source" if snapshot.bars[len(settled) - 1].final else "settle"
            )
            source_final = {bar.time for bar in snapshot.bars if bar.final}
            snapshot = replace(snapshot, bars=settled)
            last = snapshot.bars[-1]
            if protecting and self._protect(item, snapshot, source_final, now, report, orders):
                self._seen[item.instrument_id] = last.time
                continue
            if report.halted:
                continue
            if self._seen.get(item.instrument_id, -1) >= last.time:
                continue
            self._seen[item.instrument_id] = last.time
            holding = self._position(item.instrument_id) > 0
            for signal in rule_signals(
                self.plan.definition, item.instrument_id, item.time_zone, snapshot.bars, holding
            ):
                if self._has_open_order(item.instrument_id, signal["side"], orders):
                    # Not recorded as an intent, so a later bar decides again.
                    report.skipped.append(f"{item.instrument_id}:open_order")
                    continue
                if signal["side"] == "buy" and not self._levels_known(item, snapshot):
                    # As in a backtest: no entry while its stop cannot be placed.
                    report.skipped.append(f"{item.instrument_id}:protection_atr_unknown")
                    continue
                self._act(item, snapshot, signal, now, report, final_by)
        return report

    def _settled(self, bars: Sequence[EngineBar], now: datetime) -> list[EngineBar]:
        """Bars with those past the plan's settle time marked final; the source's own
        final bars are kept as they are. A bar still in progress is never final."""
        settle = self.plan.bar_settle_seconds
        if settle is None:
            return list(bars)
        cutoff = now - timedelta(seconds=settle)
        return [
            bar if bar.final or _epoch(bar.end) > cutoff else replace(bar, final=True)
            for bar in bars
        ]

    # -- protective exits (ADR 0044) -------------------------------------------------

    def _levels_known(self, item: InstrumentPlan, snapshot: FeedSnapshot) -> bool:
        rules = self._protection_rules()
        if not rules.active:
            return True
        last = snapshot.bars[-1]
        atr = AtrHistory(snapshot.bars, item.time_zone)
        return entry_levels(rules, last.close, last.end, atr) is not None

    def _close_sold(self) -> None:
        for item in self.plan.instruments:
            account = self.plan.broker_account_ref
            state = self.store.active_protection(account, item.instrument_id)
            if state is not None and self._position(item.instrument_id) <= 0:
                self.store.close_protection(state.intent_id, "position_sold")

    def _entry_state(
        self, item: InstrumentPlan, snapshot: FeedSnapshot, report: StepReport
    ) -> ProtectionState | None:
        """Open the protection of a position this strategy bought. Holdings from elsewhere
        are left alone (ADR 0044, decision 4), and the Agent screen warns about them."""
        account = self.plan.broker_account_ref
        fill = self.store.latest_buy(account, item.instrument_id)
        entry = None if fill is None else self.store.intent(fill["intent_id"])
        if (
            fill is None
            or entry is None
            or entry["strategy"]["definition_sha256"] != self.plan.strategy.definition_sha256
        ):
            report.skipped.append(f"{item.instrument_id}:holding_without_protection")
            return None

        # The paper server's fill time is unverified (ADR 0037): keep it between the order
        # and the moment the fill was seen, and use that moment when there is none.
        def epoch(text: str) -> int:
            return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())

        seen = epoch(fill["synced_at_utc"])
        reported = fill["executed_at_utc"]
        fill_time = epoch(reported) if reported else seen
        fill_time = max(epoch(entry["created_at_utc"]), min(fill_time, seen))
        state = open_state(
            self._protection_rules(),
            fill["intent_id"],
            item.instrument_id,
            Decimal(fill["price"]),
            fill_time,
            snapshot.bars,
            item.time_zone,
        )
        if state is None:
            report.skipped.append(f"{item.instrument_id}:protection_atr_unknown")
            return None
        try:
            self.store.open_protection(account, state)
        except PaperStoreError:
            report.skipped.append(f"{item.instrument_id}:protection_not_opened")
            return None
        return state

    def _protect(
        self,
        item: InstrumentPlan,
        snapshot: FeedSnapshot,
        source_final: set[int],
        now: datetime,
        report: StepReport,
        orders: Callable[[], list[BrokerOrder]],
    ) -> bool:
        """Judge a held position's protection. True when a level was reached in this pass."""
        instrument = item.instrument_id
        if self._position(instrument) <= 0:
            return False
        account = self.plan.broker_account_ref
        state = self.store.active_protection(account, instrument)
        if state is None:
            state = self._entry_state(item, snapshot, report)
            if state is None:
                return False
        else:
            entry = self.store.intent(state.intent_id)
            if (
                entry is None
                or entry["strategy"]["definition_sha256"] != self.plan.strategy.definition_sha256
            ):
                # Opened by another strategy: its rules are not this plan's to apply.
                report.skipped.append(f"{instrument}:protection_of_another_strategy")
                return False
        since = state.entry_bar_time if state.last_bar_time is None else state.last_bar_time
        raw = self.feed.outside(instrument, since, now)
        source_final = source_final | {bar.time for bar in raw if bar.final}
        outside = [bar for bar in self._settled(raw, now) if bar.final]
        after, found = advance(
            self._protection_rules(), state, snapshot.bars, item.time_zone, outside
        )
        if found is None:
            if after != state:
                self.store.save_protection(after)
            return False
        # Sent before the state is saved: after a crash the same bar is judged again, and
        # its intent ID stops a second order. An order already out or unsettled is not
        # joined by another; the next bar judges again.
        if self._exit_is_clear(instrument, orders):
            known = sorted((*snapshot.bars, *outside), key=lambda bar: bar.time)
            hit_bar = next(bar for bar in known if bar.time == found.bar_time)
            exit_order = ExitOrder(
                found.hit,
                hit_bar,
                known[-1],
                "extended" if hit_bar in outside else snapshot.session,
                known,
                "source" if hit_bar.time in source_final else "settle",
            )
            self._act(
                item, snapshot, {"side": "sell"}, now, report, exit_order.final_by, exit_order
            )
        else:
            report.skipped.append(f"{instrument}:exit_waits_for_open_order")
        self.store.save_protection(after)
        return True

    def _exit_is_clear(self, instrument_id: str, orders: Callable[[], list[BrokerOrder]]) -> bool:
        for submission in self.store.submissions("pending_response", "unknown"):
            intent = self.store.intent(submission["intent_id"])
            if intent is not None and intent["instrument_id"] == instrument_id:
                return False
        return not self._has_open_order(instrument_id, "sell", orders)

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
        final_by: Literal["source", "settle"] = "source",
        exit_order: ExitOrder | None = None,
    ) -> None:
        plan = self.plan
        bar = snapshot.bars[-1] if exit_order is None else exit_order.signal_bar
        priced = bar if exit_order is None else exit_order.latest_bar
        bar_end = _epoch(bar.end)
        sized = self._size(signal, item.instrument_id, priced.close)
        if isinstance(sized, str):
            report.skipped.append(f"{item.instrument_id}:{sized}")
            return
        method, value, quantity = sized
        sizing = Sizing(
            method=method,
            input=value,
            reference_price=priced.close,
            reference_price_source=(
                f"{'signal' if exit_order is None else 'latest'} bar close "
                f"({snapshot.dataset_revision})"
            ),
            reference_price_as_of_utc=_epoch(priced.end),
            fee_rate=plan.fee_rate,
            equity_basis=plan.equity_basis if method == "percent_equity" else None,
            basis_as_of_utc=None,
        )
        order = self._order(signal, priced.close, quantity)
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
            session=snapshot.session if exit_order is None else exit_order.session,
            input_bar_count=len(snapshot.bars) if exit_order is None else len(exit_order.bars),
            input_bars_sha256=bars_sha256(snapshot.bars if exit_order is None else exit_order.bars),
            calendar_id=snapshot.calendar_id,
            calendar_version=snapshot.calendar_version,
            final_by=final_by,
            settle_seconds=plan.bar_settle_seconds if final_by == "settle" else None,
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
            signal_summary=(
                f"rules {signal['side']} signal at the bar close"
                if exit_order is None
                else f"protective exit: {exit_order.hit.reason}"
            ),
            operands=[]
            if exit_order is None
            else [("protective_level_reached_at", exit_order.hit.reference)],
            sizing=sizing,
            order=order,
            fx=self._fx() if market_of(item.instrument_id) == "us" else None,
            limits=plan.limits,
            facts=facts,
            created_at=now,
        )
        blocking = not result.passed and (
            exit_order is None or bool(EXIT_STOPPING_REASONS.intersection(result.reasons))
        )
        if not self.store.record_intent(intent, result, blocking=blocking):
            return  # this signal was already handled (for example before a restart)
        report.intents += 1
        if blocking:
            report.blocked += 1
            return
        # A protective exit does not expire: the position is still there after a restart.
        if exit_order is None and (now - bar_end).total_seconds() > plan.max_signal_age_seconds:
            self.store.set_outcome(intent["intent_id"], "expired", "signal_too_old")
            report.expired += 1
            return
        allowed, reason = (
            self.store.may_submit(self.approval_id)
            if exit_order is None
            else self.store.may_exit(self.approval_id)
        )
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

    def _placed_near(self, order: BrokerOrder, requested: datetime) -> bool:
        """The order's time fits a request sent at `requested`; true when it cannot be read."""
        try:
            placed = datetime.strptime(order.ordered_at_local or "", "%H:%M:%S")
        except ValueError:
            return True
        sent = requested.astimezone(ZoneInfo(self.broker.time_zone))
        seconds = placed.hour * 3600 + placed.minute * 60 + placed.second
        sent_seconds = sent.hour * 3600 + sent.minute * 60 + sent.second
        # Across midnight the times of day wrap around.
        apart = (seconds - sent_seconds + SECONDS_PER_DAY // 2) % SECONDS_PER_DAY
        return -RECONCILE_BEFORE_SECONDS <= apart - SECONDS_PER_DAY // 2 <= RECONCILE_AFTER_SECONDS

    def _reconcile(self, now: datetime, orders: list[BrokerOrder]) -> int:
        """Settle pending or unknown submissions from today's order list, or halt.

        A submission matches an order nobody else claimed with the same symbol, side,
        quantity, order type and price, placed around the time it was sent. None with that
        price: the order was never placed. More than one, the right price at the wrong time,
        or a submission from an earlier broker day (today's list cannot show it): the kill
        switch goes on and the user decides.
        """
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
            price = self.broker.order_price(intent)
            same_price = [
                order
                for order in orders
                if order.broker_order_ref not in claimed
                and order.original_order_ref is None
                and order.symbol == intent["symbol"]
                and order.side == intent["side"]
                and order.order_quantity == Decimal(intent["order"]["quantity"])
                and order.order_type_code == code
                and (order.order_price is None or order.order_price == price)
            ]
            matches = [order for order in same_price if self._placed_near(order, requested)]
            if len(matches) > 1 or (same_price and not matches):
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

    def _has_open_order(
        self, instrument_id: str, side: str, orders: Callable[[], list[BrokerOrder]]
    ) -> bool:
        """An earlier order of this instrument and side is still working at the broker."""
        waiting = [
            submission
            for submission in self.store.accepted_without_execution()
            if (intent := self.store.intent(submission["intent_id"])) is not None
            and intent["instrument_id"] == instrument_id
            and intent["side"] == side
        ]
        if not waiting:
            return False
        open_refs = {o.broker_order_ref for o in orders() if o.remaining_quantity > 0}
        return any(str(s["broker_order_ref"]) in open_refs for s in waiting)

    def _cancel_stale(self, now: datetime, orders: Callable[[], list[BrokerOrder]]) -> int:
        """Cancel this plan's oldest order left unfilled too long, one per step.

        Only orders the broker lists as still open today are touched. A cancel that fails
        waits the same time again; after MAX_CANCEL_ATTEMPTS the kill switch goes on.
        """
        limit = timedelta(seconds=self.plan.unfilled_cancel_seconds or 0)
        waiting = self.store.accepted_without_execution()
        if not waiting:
            return 0
        by_ref = {order.broker_order_ref: order for order in orders()}
        for submission in waiting:
            reference = str(submission["broker_order_ref"])
            order = by_ref.get(reference)
            if order is None or order.remaining_quantity <= 0:
                continue
            intent = self.store.intent(submission["intent_id"])
            if intent is None or intent["approval_id"] != self.approval_id:
                continue
            attempts = self.store.cancel_attempts(submission["submission_id"])
            since = attempts[-1] if attempts else submission["requested_at_utc"]
            if now - datetime.fromisoformat(since.replace("Z", "+00:00")) < limit:
                continue
            if len(attempts) >= MAX_CANCEL_ATTEMPTS:
                self.store.halt(f"paper order {reference} cannot be cancelled", source="runner")
                return 0
            result = self.broker.cancel(reference, intent["instrument_id"], intent["symbol"])
            self.store.record_cancel_attempt(submission["submission_id"], now, result)
            return 1
        return 0

    def _settle_cancelled(self, now: datetime, orders: list[BrokerOrder]) -> int:
        """Close orders the runner asked to cancel and the broker shows with nothing filled.

        By quantities: the paper server's status text still said "accepted" after a cancel
        (ADR 0037, P4). An order with fills is stored by `_sync_fills` instead.
        """
        by_ref = {order.broker_order_ref: order for order in orders}
        settled = 0
        for submission in self.store.accepted_without_execution():
            order = by_ref.get(str(submission["broker_order_ref"]))
            if (
                order is None
                or order.remaining_quantity > 0
                or order.filled_quantity > 0
                or not self.store.cancel_attempts(submission["submission_id"])
            ):
                continue
            self.store.record_submission(
                {**submission, "status": "cancelled", "responded_at_utc": _utc(now)}
            )
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

    def _sync_fills(self, now: datetime, orders: list[BrokerOrder]) -> int:
        """Store each closed paper order with fills as one `order_aggregate` execution."""
        waiting = self.store.accepted_without_execution()
        if not waiting:
            return 0
        by_ref = {order.broker_order_ref: order for order in orders}
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
