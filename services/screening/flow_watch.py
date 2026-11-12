"""Stage 3 realtime flow watch (ADR 0057 §3, U-a): is a candidate tradable right now?

Each time a 1-minute bar closes, the windows are counted backwards from that minute
(only closed minutes; nothing after the evaluated minute is read):

- liquidity: at least 4 of the last 5 minutes traded at least $5,000 and 1,000 shares;
- volatility: at least 3 of the last 20 minutes moved |close - open| / open >= 1% and
  |close - open| >= 2 ticks, either direction. A tick is $0.01 at $1 or more and $0.0001
  below $1, judged on the minute's open.

A minute with no trade counts as a minute that fails both. A minute with no trade while
the symbol was halted is left out of the windows, which then reach further back. A
minute whose data the account cannot see yet (`outside_entitlement`, a cutoff earlier
than its publication) is unknown, and a window holding one cannot be judged.

Falling short only excludes the symbol at that minute; it can come back until the
session ends. Being excluded blocks new buys and keeps resting orders and positions as
they are (ADR 0057 §6). This module only reports that as a state; it places, changes and
cancels nothing. Order book depth is not used.

Dollar volume is estimated from the bar as (high + low + close) / 3 * volume, as in the
momentum check: the canonical bar carries no VWAP.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal

from services.market_data.provider import BarsQuery, InvalidBarsQueryError
from services.screening.halts import HaltRead, HaltSpan

CHECK_VERSION = "flow-watch-1"
RULES_VERSION = "adr-0057-stage3-1"
LABEL = "실시간 수급(3단계)"
DOLLAR_VOLUME_METHOD = "(high + low + close) / 3 * volume per 1m bar"

MIN_DOLLARS = Decimal(5000)
MIN_SHARES = Decimal(1000)
LIQUIDITY_WINDOW = 5
LIQUIDITY_REQUIRED = 4
VOLATILITY_WINDOW = 20
VOLATILITY_REQUIRED = 3
MIN_MOVE_RATIO = Decimal("0.01")
MIN_MOVE_TICKS = 2

#: Missing-minute reasons of the bars provider that mean "closed, nothing traded".
#: `not_yet_final` is a closed minute with no print inside the finality delay: a late
#: print could still add a bar, so the result is marked provisional.
EMPTY_REASONS = frozenset({"provider_no_bar", "not_yet_final"})

MinuteKind = Literal["bar", "empty", "unknown"]
State = Literal["eligible", "excluded", "warming_up", "unknown", "halted"]
Stage2 = Literal["passed", "failed", "not_judged", "not_implemented"]


def tick_size(price: Decimal) -> Decimal:
    """The quoting increment: $0.01 at $1.00 or more, $0.0001 below."""
    return Decimal("0.01") if price >= 1 else Decimal("0.0001")


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse(text: str) -> datetime:
    return datetime.fromisoformat(str(text).replace("Z", "+00:00"))


def _text(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return "0" if text in ("-0", "") else text


@dataclass(frozen=True)
class Minute:
    start_utc: datetime
    kind: MinuteKind
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    close: Decimal | None = None
    volume: Decimal | None = None
    final: bool = True
    session: str = "unknown"
    #: Whether a halt covered any part of the minute (as known at the evaluation).
    halted: bool = False
    #: Why a minute is empty or unknown (the provider's missing reason).
    reason: str | None = None

    @property
    def end_utc(self) -> datetime:
        return self.start_utc + timedelta(minutes=1)

    @property
    def dollars(self) -> Decimal | None:
        if self.kind != "bar" or None in (self.high, self.low, self.close, self.volume):
            return None
        assert self.high is not None and self.low is not None and self.close is not None
        assert self.volume is not None
        return (self.high + self.low + self.close) / 3 * self.volume

    @property
    def liquid(self) -> bool:
        dollars = self.dollars
        return (
            dollars is not None
            and self.volume is not None
            and dollars >= MIN_DOLLARS
            and self.volume >= MIN_SHARES
        )

    @property
    def move(self) -> Decimal | None:
        if self.kind != "bar" or self.open is None or self.close is None:
            return None
        return abs(self.close - self.open)

    @property
    def volatile(self) -> bool:
        move = self.move
        if move is None or self.open is None or self.open <= 0:
            return False
        return move / self.open >= MIN_MOVE_RATIO and move >= MIN_MOVE_TICKS * tick_size(self.open)

    @property
    def left_out(self) -> bool:
        """A halted minute without a trade: not part of any window."""
        return self.halted and self.kind != "bar"


@dataclass(frozen=True)
class Evaluation:
    """The state at the end of one closed minute."""

    as_of_utc: datetime
    state: State
    liquidity_passing: int
    liquidity_minutes: tuple[Minute, ...]
    volatility_passing: int
    volatility_minutes: tuple[Minute, ...]
    halted_now: bool
    provisional: bool

    @property
    def eligible(self) -> bool:
        return self.state == "eligible"


def minutes_from_bars(
    bars: Sequence[Mapping[str, Any]],
    missing: Sequence[Mapping[str, Any]],
    halts: Sequence[HaltSpan] = (),
    as_of: datetime | None = None,
) -> list[Minute]:
    """Every expected minute, from the provider's bars and its missing intervals.

    The bars provider returns each expected minute either as a bar or as a missing
    interval with a reason, so their union is the session's minute grid up to the end
    asked for.
    """
    minutes: dict[datetime, Minute] = {}
    for bar in bars:
        if bar.get("volume_unit", "shares") != "shares":
            continue
        start = _parse(str(bar["interval_start_utc"]))
        minutes[start] = Minute(
            start,
            "bar",
            open=Decimal(str(bar["open"])),
            high=Decimal(str(bar["high"])),
            low=Decimal(str(bar["low"])),
            close=Decimal(str(bar["close"])),
            volume=Decimal(str(bar["volume"])),
            final=bool(bar.get("is_final", True)),
            session=str(bar.get("session", "unknown")),
        )
    for gap in missing:
        start = _parse(str(gap["start_utc"]))
        end = _parse(str(gap["end_utc"]))
        reason = str(gap.get("reason", ""))
        while start < end:
            if start not in minutes:
                kind: MinuteKind = "empty" if reason in EMPTY_REASONS else "unknown"
                minutes[start] = Minute(start, kind, final=reason != "not_yet_final", reason=reason)
            start += timedelta(minutes=1)
    ordered = [minutes[key] for key in sorted(minutes)]
    if not halts:
        return ordered
    cutoff = as_of or (ordered[-1].end_utc if ordered else datetime.now(timezone.utc))
    return [
        _with_halt(minute, any(h.overlaps(minute.start_utc, minute.end_utc, cutoff) for h in halts))
        for minute in ordered
    ]


def _with_halt(minute: Minute, halted: bool) -> Minute:
    if minute.halted == halted:
        return minute
    return Minute(
        minute.start_utc,
        minute.kind,
        minute.open,
        minute.high,
        minute.low,
        minute.close,
        minute.volume,
        minute.final,
        minute.session,
        halted,
        minute.reason,
    )


def evaluate(
    minutes: Sequence[Minute],
    index: int,
    halts: Sequence[HaltSpan] = (),
) -> Evaluation:
    """The state at the end of `minutes[index]`, from that minute and the ones before it.

    `minutes` must be one session's grid in time order; nothing after `index` is read.
    """
    as_of = minutes[index].end_utc
    counted = [m for m in minutes[: index + 1] if not m.left_out]
    liquidity = tuple(counted[-LIQUIDITY_WINDOW:])
    volatility = tuple(counted[-VOLATILITY_WINDOW:])
    halted_now = any(h.open_at(as_of) for h in halts)
    liquid = sum(1 for m in liquidity if m.liquid)
    volatile = sum(1 for m in volatility if m.volatile)
    provisional = any(not m.final for m in volatility)
    state: State
    if halted_now:
        state = "halted"
    elif any(m.kind == "unknown" for m in volatility):
        state = "unknown"
    elif len(liquidity) < LIQUIDITY_WINDOW or len(volatility) < VOLATILITY_WINDOW:
        state = "warming_up"
    elif liquid >= LIQUIDITY_REQUIRED and volatile >= VOLATILITY_REQUIRED:
        state = "eligible"
    else:
        state = "excluded"
    return Evaluation(
        as_of, state, liquid, liquidity, volatile, volatility, halted_now, provisional
    )


def timeline(minutes: Sequence[Minute], halts: Sequence[HaltSpan] = ()) -> list[Evaluation]:
    """The state at the end of every minute of the grid, each from its own past only."""
    return [evaluate(minutes, index, halts) for index in range(len(minutes))]


def _minute_document(minute: Minute) -> dict[str, Any]:
    dollars = minute.dollars
    return {
        "start_utc": _utc(minute.start_utc),
        "kind": minute.kind,
        "open": None if minute.open is None else _text(minute.open),
        "close": None if minute.close is None else _text(minute.close),
        "volume": None if minute.volume is None else _text(minute.volume),
        "dollar_volume_estimate": None
        if dollars is None
        else _text(dollars.quantize(Decimal("0.01"))),
        "liquid": minute.liquid,
        "volatile": minute.volatile,
        "final": minute.final,
        "halted": minute.halted,
        "reason": minute.reason,
    }


def flow_document(
    evaluations: Sequence[Evaluation],
    halt_read: HaltRead,
    stage2: Stage2,
) -> dict[str, Any]:
    """One symbol's current state, its state changes through the session and the rules."""
    rules = {
        "version": RULES_VERSION,
        "liquidity": {
            "window": LIQUIDITY_WINDOW,
            "required": LIQUIDITY_REQUIRED,
            "min_dollar_volume": _text(MIN_DOLLARS),
            "min_volume": _text(MIN_SHARES),
            "dollar_volume_method": DOLLAR_VOLUME_METHOD,
        },
        "volatility": {
            "window": VOLATILITY_WINDOW,
            "required": VOLATILITY_REQUIRED,
            "min_move_percent": _text(MIN_MOVE_RATIO * 100),
            "min_move_ticks": MIN_MOVE_TICKS,
            "tick_rule": "$0.01 at an open of $1 or more, $0.0001 below",
        },
    }
    changes: list[dict[str, Any]] = []
    previous: State | None = None
    for item in evaluations:
        if item.state != previous:
            changes.append({"at_utc": _utc(item.as_of_utc), "state": item.state})
            previous = item.state
    current = evaluations[-1] if evaluations else None
    document: dict[str, Any] = {
        "stage2": stage2,
        "rules": rules,
        "halts": {
            "available": halt_read.available,
            "source": halt_read.source,
            "note": halt_read.note,
            "spans": [
                {
                    "start_utc": _utc(span.start_utc),
                    "end_utc": None if span.end_utc is None else _utc(span.end_utc),
                    "code": span.code,
                    "reason": span.reason,
                }
                for span in halt_read.spans
            ],
        },
        "changes": changes,
        "eligible_minutes": sum(1 for item in evaluations if item.eligible),
        "evaluated_minutes": len(evaluations),
    }
    if current is None:
        document.update(
            {
                "state": "warming_up",
                "as_of_utc": None,
                "new_buys_allowed": False,
                "resting_orders": "keep",
                "liquidity": None,
                "volatility": None,
                "halted_now": False,
                "provisional": False,
            }
        )
        return document
    document.update(
        {
            "state": current.state,
            "as_of_utc": _utc(current.as_of_utc),
            # ADR 0057 §6: excluded blocks new buys only; resting orders and positions stay.
            "new_buys_allowed": current.eligible and stage2 != "failed",
            "resting_orders": "keep",
            "liquidity": {
                "passing": current.liquidity_passing,
                "of": len(current.liquidity_minutes),
                "minutes": [_minute_document(m) for m in current.liquidity_minutes],
            },
            "volatility": {
                "passing": current.volatility_passing,
                "of": len(current.volatility_minutes),
                "minutes": [_minute_document(m) for m in current.volatility_minutes],
            },
            "halted_now": current.halted_now,
            "provisional": current.provisional,
        }
    )
    return document


GetStage2 = Callable[[str], Stage2]


def stage2_not_implemented(_symbol: str) -> Stage2:
    """U-b (stage 2, material impact) does not exist yet: every candidate is watched."""
    return "not_implemented"


#: Seconds after a minute's end before it is read: the provider aggregates a minute's
#: trades shortly after it closes, and a minute read too early would look empty. How long
#: Alpaca takes is not measured (unverified); 20 s is a margin, not a measurement.
SETTLE_SECONDS = 20.0


def last_closed_minute(now: datetime, settle_seconds: float = SETTLE_SECONDS) -> datetime:
    """The end of the newest minute that closed at least `settle_seconds` ago."""
    moment = now - timedelta(seconds=settle_seconds)
    return moment.replace(second=0, microsecond=0)


def flow_check(
    instrument_id: str,
    window: Any,
    now: datetime,
    get_bars: Callable[[Any], Any],
    halt_read: HaltRead,
    stage2: Stage2,
    settle_seconds: float = SETTLE_SECONDS,
) -> dict[str, Any]:
    """`status`, `result`, `versions`, `warnings` and `sources` of one symbol's flow watch."""
    end = min(last_closed_minute(now, settle_seconds), window.post_close_utc)
    warnings: list[str] = []
    if not halt_read.available:
        warnings.append(
            "halt 기록이 없다: halt 중의 빈 분도 미달 봉으로 셌다(ADR 0057은 창에서 빼라고 정했다)."
        )
    outcome: dict[str, Any] = {
        "status": "ok",
        "result": {},
        "versions": {"check": CHECK_VERSION, "rules": RULES_VERSION},
        "warnings": warnings,
        "sources": {},
    }
    bars: tuple[dict[str, Any], ...] = ()
    missing: list[dict[str, Any]] = []
    if end > window.pre_open_utc:
        try:
            result = get_bars(BarsQuery(instrument_id, "1m", window.pre_open_utc, end))
        except InvalidBarsQueryError as exc:
            outcome["status"] = "error"
            outcome["result"] = {"reason": f"1m bars: {str(exc)[:200]}"}
            return outcome
        bars, coverage = result.bars, result.coverage
        missing = list(coverage.get("missing_intervals", []))
        outcome["sources"]["bars"] = {
            key: coverage.get(key)
            for key in ("source_id", "source_feed_scope", "adjustment", "dataset_revision")
        }
    minutes = [
        m for m in minutes_from_bars(bars, missing, halt_read.spans, end) if m.end_utc <= end
    ]
    document = flow_document(timeline(minutes, halt_read.spans), halt_read, stage2)
    document["label"] = LABEL
    document["evaluated_until_utc"] = _utc(end)
    outcome["result"] = document
    if document["state"] == "unknown":
        outcome["status"] = "incomplete"
    return outcome
