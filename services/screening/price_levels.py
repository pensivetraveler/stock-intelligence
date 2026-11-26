"""Stage 5 — price levels per strategy frame (ADR 0057 §5, rules as of 2026-11-23).

A pure calculation: from one candidate's stage 4 facts (dilution supply prices, off-exchange
volume peaks), its session 1m bars and the stage 2 strategy, at a given time, it proposes
each entry frame's buy price, first take-profit, stop and size, with the reason behind
every value or the reason there is none. It places, changes or approves no order; stage 6
(scripts) and stage 7 (SIW Agent) are separate.

Rules (newest decision wins, ADR 0057 5단계·6단계 "2026-11-23 사용자 결정"):

- Tick: $0.01 at $1 or more, $0.0001 below (stage 3). "2 ticks" steps the grid twice.
- Sell pressure: the dilution supply prices and the off-exchange peaks above the current
  price. Off-exchange peaks below it are the pullback supports.
- Pullback buy: the nearest peak below the current price + 2 ticks; no peak, no pullback.
- Breakout buy: the nearest sell pressure above the current price + 2 ticks; with none,
  the session high so far + 2 ticks.
- Stop: buy basis x 0.92 (the peak, or the breakout reference price). Only when the basis
  cannot be read (a buy price the user set by hand) the buy price -7%.
- First take-profit: buy +25% selling 80% (conservative +12.5% selling 90%); the rest
  trails 8% below the high. The old "target <= buy" rule and the stage 5 target row
  (pressure - 2 ticks, +20%, 40%) are gone.
- Size: $1,000 per frame (conservative $500), whole shares. Pullback and breakout do not
  cancel each other. The conservative strategy keeps the breakout frame: the 11-15 "chase
  off" belonged to the chase frame the 11-16 change replaced (see the ADR).

No future data: bars ending after `as_of`, and stage 4 checks created or reaching data
after it (or a dilution analysis dated after the session), are not used, and say so.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from typing import Any, Literal

VERSION = "stage5-levels-1"
RULES_VERSION = "adr-0057-stage5-2026-11-23"
LABEL = "가격대(5단계)"

Strategy = Literal["normal", "conservative"]
Frame = Literal["pullback", "breakout"]

MINUTE = timedelta(minutes=1)
STOP_RATIO = Decimal("0.92")
FALLBACK_STOP_RATIO = Decimal("0.93")
TRAILING_FROM_HIGH_PCT = Decimal("8")
BUDGET: dict[str, Decimal] = {"normal": Decimal("1000"), "conservative": Decimal("500")}
FIRST_TARGET: dict[str, tuple[Decimal, Decimal]] = {
    # (gain over the buy price, share of the position sold there)
    "normal": (Decimal("0.25"), Decimal("0.80")),
    "conservative": (Decimal("0.125"), Decimal("0.90")),
}
DILUTION_UNIT = "usd_per_share"
FACT_STATUSES = frozenset({"confirmed", "computed", "inferred"})

NOTE_FACTS = "가격대는 제안이다. 주문은 6·7단계에서 따로 한다. 사용자가 장중에 고칠 수 있다."
NOTE_PEAK_PRICE = "장외 체결 봉우리 가격은 가격 칸(price_low~price_high)의 가운데로 쓴다."
NOTE_PEAK_DIRECTION = "장외 체결 봉우리는 방향(매수·매도)을 모른다: 위치만 쓴다."
NOTE_NO_DILUTION_PRICE = (
    "희석 공급 가격이 하나도 없다(규칙만 분석, 증권 조건이 비어 있을 수 있음): "
    "매도 압력은 장외 체결 봉우리뿐이다."
)
NOTE_SPLITS = "희석 공급 가격은 공시 당시 값이다: 그 뒤 주식 분할·병합을 보정하지 않았다."


def tick_size(price: Decimal) -> Decimal:
    """The quoting increment: $0.01 at $1.00 or more, $0.0001 below (stage 3)."""
    return Decimal("0.01") if price >= 1 else Decimal("0.0001")


def ceil_tick(price: Decimal) -> Decimal:
    """The lowest grid price at or above `price`."""
    tick = tick_size(price)
    return (price / tick).to_integral_value(ROUND_CEILING) * tick


def floor_tick(price: Decimal) -> Decimal:
    """The highest grid price at or below `price`."""
    tick = tick_size(price)
    return (price / tick).to_integral_value(ROUND_FLOOR) * tick


def ticks_above(price: Decimal, count: int) -> Decimal:
    """`count` grid steps above `price` (on the grid first); each step uses the tick of the
    price it starts from, so $0.9999 + 2 ticks is $1.01."""
    current = ceil_tick(price)
    for _ in range(count):
        current = current + tick_size(current)
    return current


def _utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse(text: Any) -> datetime:
    return datetime.fromisoformat(str(text).replace("Z", "+00:00"))


def _text(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return "0" if text in ("-0", "") else text


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        made = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return made if made.is_finite() else None


@dataclass(frozen=True)
class Level:
    """One price the rules can use, with where it came from."""

    price: Decimal
    source: Literal["dilution", "off_exchange"]
    detail: dict[str, Any] = field(default_factory=dict)

    def document(self) -> dict[str, Any]:
        return {"price": _text(self.price), "source": self.source, **self.detail}


@dataclass(frozen=True)
class Market:
    """The current price and the session high, both from bars ending by `as_of`."""

    price: Decimal | None
    price_bar_utc: str | None
    price_final: bool | None
    day_high: Decimal | None
    day_high_bar_utc: str | None
    bars_used: int
    bars_after_as_of: int


def market_at(bars: Iterable[Mapping[str, Any]], as_of: datetime) -> Market:
    """The last close and the high so far from 1m bars whose minute ended by `as_of`."""
    used, later = 0, 0
    last: tuple[datetime, Mapping[str, Any]] | None = None
    high: tuple[Decimal, datetime] | None = None
    for bar in bars:
        start = _parse(bar["interval_start_utc"])
        if start + MINUTE > as_of:
            later += 1
            continue
        used += 1
        if last is None or start > last[0]:
            last = (start, bar)
        bar_high = _decimal(bar.get("high"))
        if bar_high is not None and bar_high > 0 and (high is None or bar_high > high[0]):
            high = (bar_high, start)
    close = None if last is None else _decimal(last[1].get("close"))
    if close is not None and close <= 0:
        close = None
    return Market(
        price=close,
        price_bar_utc=None if last is None else _utc(last[0]),
        price_final=None if last is None else bool(last[1].get("is_final", False)),
        day_high=None if high is None else high[0],
        day_high_bar_utc=None if high is None else _utc(high[1]),
        bars_used=used,
        bars_after_as_of=later,
    )


def _after(text: Any, as_of: datetime) -> bool:
    return text is not None and _parse(text) > as_of


def dilution_levels(
    dilution: Mapping[str, Any] | None, session_date: date, as_of: datetime
) -> tuple[list[Level], dict[str, Any]]:
    """Fact supply prices (price, price range ends, floor, condition thresholds) of the
    stage 4 dilution part, or none with the reason."""
    if dilution is None:
        return [], {"state": "missing", "reason": "희석 검사가 없다(4단계 미실행 또는 대기)."}
    report: dict[str, Any] = {
        "check_id": dilution.get("check_id"),
        "status": dilution.get("status"),
        "analysis_as_of": dilution.get("analysis_as_of"),
        "incomplete_reasons": list(dilution.get("incomplete_reasons") or []),
    }
    if dilution.get("status") == "pending":
        return [], {**report, "state": "pending", "reason": "희석 검사가 아직 끝나지 않았다."}
    if _after(dilution.get("created_at_utc"), as_of):
        return [], {**report, "state": "future", "reason": "기준 시각 뒤에 만든 희석 검사다."}
    if str(dilution.get("analysis_as_of") or "") > session_date.isoformat():
        return [], {
            **report,
            "state": "future",
            "reason": "희석 분석 기준일이 세션 날짜보다 늦다(미래 자료).",
        }
    levels: list[Level] = []
    skipped = 0
    securities = list(dilution.get("securities") or [])
    for security in securities:
        values: list[tuple[str, Any]] = [("price", security.get("price"))]
        values += [("price_range", item) for item in security.get("price_range") or []]
        values.append(("floor", security.get("floor")))
        values += [
            (f"condition:{item.get('type')}", item.get("threshold"))
            for item in security.get("conditions") or []
        ]
        for kind, value in values:
            if not isinstance(value, Mapping) or value.get("amount") is None:
                continue
            amount = _decimal(value.get("amount"))
            fact = value.get("status") in FACT_STATUSES
            if amount is None or amount <= 0 or value.get("unit") != DILUTION_UNIT or not fact:
                skipped += 1
                continue
            levels.append(
                Level(
                    amount,
                    "dilution",
                    {
                        "kind": kind,
                        "security_id": security.get("id"),
                        "security_kind": security.get("kind"),
                        "status": value.get("status"),
                        "value_as_of": value.get("as_of"),
                        "after_base_point": bool(security.get("after_base_point")),
                        "approval_unknown": bool(security.get("approval_unknown")),
                    },
                )
            )
    report.update(
        state="used" if levels else "no_prices",
        securities=len(securities),
        prices_used=len(levels),
        values_skipped=skipped,
        reason=None if levels else NOTE_NO_DILUTION_PRICE,
    )
    return levels, report


def off_exchange_levels(
    off_exchange: Mapping[str, Any] | None, as_of: datetime
) -> tuple[list[Level], dict[str, Any]]:
    """The stage 4 off-exchange peaks, each at the middle of its price bin."""
    if off_exchange is None:
        return [], {"state": "missing", "reason": "장외 체결 검사가 없다(4단계 미실행 또는 대기)."}
    report: dict[str, Any] = {
        "check_id": off_exchange.get("check_id"),
        "status": off_exchange.get("status"),
        "data_until_utc": off_exchange.get("data_until_utc"),
    }
    if off_exchange.get("status") == "pending":
        return [], {**report, "state": "pending", "reason": "장외 체결 검사가 아직 끝나지 않았다."}
    if _after(off_exchange.get("created_at_utc"), as_of) or _after(
        off_exchange.get("data_until_utc"), as_of
    ):
        return [], {
            **report,
            "state": "future",
            "reason": "기준 시각 뒤의 체결을 읽은 장외 체결 검사다.",
        }
    levels: list[Level] = []
    for item in off_exchange.get("levels") or []:
        low, high = _decimal(item.get("price_low")), _decimal(item.get("price_high"))
        if low is None or high is None or high <= low or low < 0:
            continue
        levels.append(
            Level(
                (low + high) / 2,
                "off_exchange",
                {
                    "price_low": _text(low),
                    "price_high": _text(high),
                    "share_pct": item.get("share_pct"),
                },
            )
        )
    report.update(
        state="used" if levels else "no_peaks",
        peaks_used=len(levels),
        reason=None if levels else "장외 체결 봉우리가 없다.",
    )
    return levels, report


def _quantity(strategy: Strategy, buy: Decimal) -> dict[str, Any]:
    budget = BUDGET[strategy]
    shares = int((budget / buy).to_integral_value(ROUND_FLOOR))
    return {
        "budget_usd": _text(budget),
        "shares": shares,
        "cost_usd": _text(buy * shares),
        "reason": None if shares > 0 else "매수가가 틀 금액보다 높아 한 주도 살 수 없다.",
    }


def _exits(strategy: Strategy, buy: Decimal, basis: Decimal | None) -> dict[str, Any]:
    gain, sell_fraction = FIRST_TARGET[strategy]
    if basis is not None and basis > 0:
        stop: dict[str, Any] = {
            "price": _text(floor_tick(basis * STOP_RATIO)),
            "rule": "basis_x_0.92",
            "basis_price": _text(basis),
            "reason": None,
        }
    else:
        stop = {
            "price": _text(floor_tick(buy * FALLBACK_STOP_RATIO)),
            "rule": "buy_minus_7pct",
            "basis_price": None,
            "reason": "매수 근거 가격을 읽지 못해 매수가 −7%를 쓴다.",
        }
    return {
        "stop": stop,
        "first_target": {
            "price": _text(ceil_tick(buy * (1 + gain))),
            "gain_pct": _text(gain * 100),
            "sell_pct": _text(sell_fraction * 100),
            "rule": f"buy_plus_{_text(gain * 100)}pct",
        },
        "trailing": {
            "from_high_pct": _text(TRAILING_FROM_HIGH_PCT),
            "starts": "after_first_target",
            "replaces_stop": True,
        },
    }


def _frame(
    strategy: Strategy,
    buy: Decimal | None,
    basis: Decimal | None,
    rule: str | None,
    basis_level: dict[str, Any] | None,
    reason: str | None,
    current: Decimal | None,
) -> dict[str, Any]:
    if buy is None:
        return {"state": "none", "reason": reason, "buy": None, "notes": []}
    made: dict[str, Any] = {
        "state": "set",
        "reason": None,
        "buy": {"price": _text(buy), "rule": rule, "basis": basis_level},
        **_exits(strategy, buy, basis),
        "quantity": _quantity(strategy, buy),
        "notes": [],
    }
    if current is not None and rule == "nearest_peak_below_plus_2_ticks" and buy >= current:
        made["notes"].append("눌림 매수가가 현재가 이상이다: 지정가가 바로 체결될 수 있다.")
    return made


def price_levels(
    *,
    strategy: Strategy,
    session_date: date,
    as_of: datetime,
    bars: Sequence[Mapping[str, Any]],
    dilution: Mapping[str, Any] | None,
    off_exchange: Mapping[str, Any] | None,
    stage3_pass_utc: str | None = None,
    user_buy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """One candidate's stage 5 price levels at `as_of`.

    `dilution` and `off_exchange` are the parts of a `ScreeningStage4` document; `bars`
    the session's 1m bars (only those ended by `as_of` are read). `user_buy` holds buy
    prices the user set by hand per frame: those have no readable basis, so their stop is
    the buy price -7%. `stage3_pass_utc` is kept as context only (the 11-15 chase frame
    that priced off the stage 3 pass bar was replaced on 11-16)."""
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    market = market_at(bars, as_of)
    dilution_found, dilution_report = dilution_levels(dilution, session_date, as_of)
    peaks, off_report = off_exchange_levels(off_exchange, as_of)
    current = market.price
    notes = [NOTE_FACTS, NOTE_PEAK_DIRECTION]
    if peaks:
        notes.append(NOTE_PEAK_PRICE)
    if dilution_found:
        notes.append(NOTE_SPLITS)
    if dilution_report["state"] in ("no_prices", "missing", "pending", "future"):
        notes.append(NOTE_NO_DILUTION_PRICE)

    pressure: list[Level] = []
    supports: list[Level] = []
    if current is not None:
        pressure = sorted(
            (level for level in dilution_found + peaks if level.price > current),
            key=lambda level: (level.price, level.source),
        )
        supports = sorted(
            (level for level in peaks if level.price < current),
            key=lambda level: -level.price,
        )

    frames: dict[str, dict[str, Any]] = {}
    hand = {key: _decimal(value) for key, value in (user_buy or {}).items()}
    if current is None:
        no_price = "기준 시각까지 끝난 1분봉이 없어 현재가를 모른다."
        for frame in ("pullback", "breakout"):
            frames[frame] = _frame(strategy, None, None, None, None, no_price, None)
    else:
        # Pullback: the nearest off-exchange peak below the current price + 2 ticks.
        if supports:
            nearest = supports[0]
            frames["pullback"] = _frame(
                strategy,
                ticks_above(nearest.price, 2),
                nearest.price,
                "nearest_peak_below_plus_2_ticks",
                nearest.document(),
                None,
                current,
            )
        else:
            frames["pullback"] = _frame(
                strategy, None, None, None, None, "현재가 아래 장외 체결 봉우리가 없다.", current
            )
        # Breakout: the nearest sell pressure above + 2 ticks, else the session high + 2.
        if pressure:
            nearest = pressure[0]
            frames["breakout"] = _frame(
                strategy,
                ticks_above(nearest.price, 2),
                nearest.price,
                "nearest_pressure_above_plus_2_ticks",
                nearest.document(),
                None,
                current,
            )
        elif market.day_high is not None:
            frames["breakout"] = _frame(
                strategy,
                ticks_above(market.day_high, 2),
                market.day_high,
                "day_high_plus_2_ticks",
                {
                    "price": _text(market.day_high),
                    "source": "day_high",
                    "bar_utc": market.day_high_bar_utc,
                    "reason": "현재가 위 매도 압력이 없다.",
                },
                None,
                current,
            )
        else:
            frames["breakout"] = _frame(
                strategy,
                None,
                None,
                None,
                None,
                "현재가 위 매도 압력이 없고 당일 고가를 읽지 못했다.",
                current,
            )
    for frame in ("pullback", "breakout"):
        price = hand.get(frame)
        if price is None or price <= 0:
            continue
        frames[frame] = _frame(
            strategy,
            ceil_tick(price),
            None,
            "user_set",
            {"price": None, "source": "user", "reason": "사용자가 매수가를 직접 정했다."},
            None,
            current,
        )

    breakout_source = (frames["breakout"].get("buy") or {}).get("basis") or {}
    if breakout_source.get("source") == "off_exchange" and not dilution_found:
        frames["breakout"]["notes"].append(
            "돌파 근거가 장외 체결 봉우리다: 희석 공급 가격이 없어 확인하지 못한 매도 압력이 "
            "있을 수 있다."
        )

    return {
        "label": LABEL,
        "version": VERSION,
        "rules": RULES_VERSION,
        "as_of_utc": _utc(as_of),
        "session_date": session_date.isoformat(),
        "strategy": strategy,
        "stage3_pass_utc": stage3_pass_utc,
        "market": {
            "price": None if current is None else _text(current),
            "price_bar_utc": market.price_bar_utc,
            "price_final": market.price_final,
            "day_high": None if market.day_high is None else _text(market.day_high),
            "day_high_bar_utc": market.day_high_bar_utc,
            "bars_used": market.bars_used,
            "bars_after_as_of_ignored": market.bars_after_as_of,
        },
        "pressure_above": [level.document() for level in pressure],
        "supports_below": [level.document() for level in supports],
        "sources": {"dilution": dilution_report, "off_exchange": off_report},
        "frames": frames,
        "notes": notes,
    }
