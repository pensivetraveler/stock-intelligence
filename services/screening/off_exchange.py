"""Off-exchange (TRF-reported) volume by price before a cutoff (ADR 0052 §5, S2).

Reads Alpaca SIP trades and keeps those Alpaca marks with exchange code `D` (named
"FINRA ADF" by `GET /v2/stocks/meta/exchanges`; FINRA TRF reports are carried under
the same code, measured 2026-11-09). These are off-exchange trades: ATS ("dark pool")
trades and broker internalization mixed together, with no buy/sell direction, and the
SIP timestamp is when the trade was reported, which can differ from when it happened.
Every result says so. Nothing is built from 1-minute bars.

The result is the `off_exchange` part of one `screening_check` (ADR 0052 S1).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_FLOOR, Decimal
from typing import Any

from services.market_data.calendar import NEW_YORK, ExchangeCalendar, UsEquityCalendar
from services.market_data.provider import MarketDataError

CHECK_VERSION = "off-exchange-check-1"
CONDITION_POLICY_VERSION = "off-exchange-conditions-1"
LABEL = "장외 체결(TRF 보고)"
OFF_EXCHANGE_CODE = "D"
SOURCE_ID = "alpaca-sip"
ENDPOINT = "GET /v2/stocks/{symbol}/trades"

#: `u` values that mean the trade no longer stands (Alpaca trade schema).
REMOVED_UPDATES = frozenset({"canceled", "incorrect"})
#: Official open/close and corrected-close reports repeat prices, not new volume.
NOT_TRADES = frozenset({"M", "Q", "9"})
#: Real volume whose price does not show the market at that moment, so it is left out
#: of the price levels (it still counts toward volume shares): derivatively priced,
#: average price, prior reference price, qualified contingent, contingent, cash,
#: next day, seller, price variation.
OFF_MARKET_PRICE = frozenset({"4", "W", "P", "7", "V", "C", "N", "R", "H"})
#: `B` is "Average Price Trade" on tapes A/B but "Bunched Trade" on tape C.
AVERAGE_PRICE_ON_TAPES_AB = "B"
#: Reported late or out of sequence: the price is real, the timestamp is the report.
LATE_REPORT = frozenset({"Z", "L", "U"})

LIMITATIONS = (
    "장외 체결(TRF 보고)은 다크풀(ATS) 체결과 증권사 내부 체결이 섞여 있다. "
    "다크풀만의 가격대가 아니다.",
    "매수·매도 방향은 알 수 없다. 거래량이 몰린 가격대는 지지·저항 '후보'일 뿐이다.",
    "체결 시각은 SIP가 받은 보고 시각이다. 실제 체결 시각과 다를 수 있고, 늦게 보고된 체결이 있다.",
    "FINRA 주간 ATS 자료는 2~4주 늦은 주간 합계라 맥락으로만 함께 보인다.",
)
UNVERIFIED = (
    "Alpaca 메타는 코드 D를 'FINRA ADF'라고만 부른다. FINRA TRF 보고분이 D에 모두 들어 있다는 것은 "
    "2026-11-09 측정한 거래량 비중(대형주 36%, 급등 소형주 62~69%)으로 추정했고 "
    "공급자 문서로 확인하지 않았다.",
    "Alpaca 체결의 `t`가 SIP 시각인지 보고 주체 시각인지 문서에 없다.",
)


@dataclass(frozen=True)
class OffExchangeLimits:
    sessions: int = 5
    #: 10,000 trades a page: a large cap session is about 70 pages (AAPL, 2026-11-06).
    max_pages: int = 150
    max_seconds: float = 90.0
    top_levels: int = 5
    #: Peaks holding less than this share of priced volume are noise, not levels.
    min_level_share_pct: Decimal = Decimal(1)
    max_bins: int = 500


TradePages = Callable[[str, datetime, datetime], Iterator[list[dict[str, Any]]]]


@dataclass
class _Session:
    session_date: date
    start: datetime
    end: datetime
    partial: bool
    pages: int = 0
    trades: int = 0
    total_volume: Decimal = Decimal(0)
    off_volume: Decimal = Decimal(0)
    off_trades: int = 0
    priced_off_volume: Decimal = Decimal(0)
    off_market_price_volume: Decimal = Decimal(0)
    late_report_volume: Decimal = Decimal(0)
    removed_trades: int = 0
    by_price: dict[Decimal, Decimal] = field(default_factory=dict)
    last_price: Decimal | None = None
    last_time: str | None = None

    def add(self, trade: dict[str, Any]) -> None:
        if trade.get("u") in REMOVED_UPDATES:
            self.removed_trades += 1
            return
        conditions = {str(item) for item in trade.get("c") or []}
        if conditions & NOT_TRADES:
            return
        price = Decimal(str(trade["p"]))
        size = Decimal(str(trade["s"]))
        if price <= 0 or size <= 0:
            return
        self.trades += 1
        self.total_volume += size
        off_market = bool(conditions & OFF_MARKET_PRICE) or (
            AVERAGE_PRICE_ON_TAPES_AB in conditions and trade.get("z") in ("A", "B")
        )
        stamp = str(trade["t"])
        if not off_market and (self.last_time is None or stamp >= self.last_time):
            self.last_price, self.last_time = price, stamp
        if trade.get("x") != OFF_EXCHANGE_CODE:
            return
        self.off_trades += 1
        self.off_volume += size
        if conditions & LATE_REPORT:
            self.late_report_volume += size
        if off_market:
            self.off_market_price_volume += size
            return
        self.priced_off_volume += size
        self.by_price[price] = self.by_price.get(price, Decimal(0)) + size

    def document(self) -> dict[str, Any]:
        share = None
        if self.total_volume > 0:
            share = _text((self.off_volume * 100 / self.total_volume).quantize(Decimal("0.01")))
        return {
            "session_date": self.session_date.isoformat(),
            "range_utc": [_utc(self.start), _utc(self.end)],
            "partial": self.partial,
            "pages": self.pages,
            "trades": self.trades,
            "total_volume": _text(self.total_volume),
            "off_exchange_volume": _text(self.off_volume),
            "off_exchange_trades": self.off_trades,
            "off_exchange_share_pct": share,
            "priced_volume": _text(self.priced_off_volume),
            "off_market_price_volume": _text(self.off_market_price_volume),
            "late_report_volume": _text(self.late_report_volume),
            "removed_trades": self.removed_trades,
        }


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _text(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return "0" if text in ("-0", "") else text


def bin_width(reference: Decimal) -> Decimal:
    """A 1-2-5 step close to 0.5% of the reference price, at least $0.0001."""
    target = reference * Decimal("0.005")
    if target <= Decimal("0.0001"):
        return Decimal("0.0001")
    exponent = target.adjusted()
    best = Decimal("0.0001")
    for power in (exponent - 1, exponent):
        for step in (1, 2, 5):
            candidate = Decimal(step).scaleb(power)
            if best < candidate <= target:
                best = candidate
    return best


def _bins(by_price: dict[Decimal, Decimal], width: Decimal) -> dict[Decimal, Decimal]:
    bins: dict[Decimal, Decimal] = {}
    for price, volume in by_price.items():
        low = (price / width).to_integral_value(rounding=ROUND_FLOOR) * width
        bins[low] = bins.get(low, Decimal(0)) + volume
    return bins


def _levels(
    bins: dict[Decimal, Decimal],
    width: Decimal,
    reference: Decimal,
    total: Decimal,
    limits: OffExchangeLimits,
) -> list[dict[str, Any]]:
    """Bins whose volume is at least both neighbours' (local peaks) and at least
    `min_level_share_pct` of the priced volume, largest first."""
    floor = total * limits.min_level_share_pct / 100
    peaks = [
        (low, volume)
        for low, volume in bins.items()
        if volume >= floor
        and volume >= bins.get(low - width, Decimal(0))
        and volume >= bins.get(low + width, Decimal(0))
    ]
    peaks.sort(key=lambda item: (-item[1], item[0]))
    levels = []
    for low, volume in peaks[: limits.top_levels]:
        high = low + width
        side = "above" if low > reference else "below" if high <= reference else "at"
        levels.append(
            {
                "price_low": _text(low),
                "price_high": _text(high),
                "volume": _text(volume),
                "share_pct": _text((volume * 100 / total).quantize(Decimal("0.01"))),
                "side": side,
            }
        )
    return levels


def _sessions(
    calendar: ExchangeCalendar, known_until: datetime, count: int
) -> list[tuple[date, datetime, datetime, bool]]:
    """The `count` latest sessions whose extended hours opened before `known_until`,
    newest first; the last one is cut at `known_until` (partial) when still open."""
    found: list[tuple[date, datetime, datetime, bool]] = []
    day = known_until.astimezone(NEW_YORK).date()
    for _ in range(count * 3 + 10):
        window = calendar.window_for_date(day)
        if window is not None and window.pre_open_utc < known_until:
            end = min(window.post_close_utc, known_until)
            found.append((day, window.pre_open_utc, end, end < window.post_close_utc))
            if len(found) == count:
                break
        day -= timedelta(days=1)
    return found


def finra_context(evidence: Any) -> dict[str, Any]:
    """FINRA weekly ATS context from a Dark Pool Agent `Evidence` (ADR 0024), unchanged."""
    wanted = {
        "ats_week",
        "ats_shares",
        "ats_share_of_off_exchange_pct",
        "ats_share_of_total_volume_pct",
    }
    return {
        "cutoff_utc": evidence.cutoff_utc,
        "facts": [fact.document() for fact in evidence.facts if fact.code in wanted],
        "missing": sorted(set(evidence.missing) & wanted),
        "uncertainty": list(evidence.uncertainty),
    }


def off_exchange_check(
    trade_pages: TradePages,
    *,
    symbol: str,
    cutoff: datetime,
    now: datetime,
    publication_delay_minutes: int,
    limits: OffExchangeLimits | None = None,
    finra: dict[str, Any] | None = None,
    calendar: ExchangeCalendar | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Off-exchange volume by price over the latest sessions known at `cutoff`.

    Returns the check-specific part of a `screening_check` document (ADR 0052 S1):
    `status`, `result`, `versions`, `warnings` and `sources`. The caller adds the
    envelope (session date, symbol, instrument, cutoff, creation time).
    """
    limits = limits or OffExchangeLimits()
    if cutoff.tzinfo is None or now.tzinfo is None:
        raise ValueError("cutoff and now must be timezone-aware")
    if cutoff > now:
        raise ValueError("cutoff must not be after now")
    calendar = calendar or UsEquityCalendar("XNAS")
    known_until = min(cutoff, now - timedelta(minutes=publication_delay_minutes))
    known_until = known_until.replace(microsecond=0)
    planned = _sessions(calendar, known_until, limits.sessions)

    missing: list[str] = []
    notes: list[str] = []
    errors: list[str] = []
    done: list[_Session] = []
    pages = 0
    started = monotonic()
    budget_hit = False
    for session_date, start, end, partial in planned:
        session = _Session(session_date, start, end, partial)
        pager = trade_pages(symbol, start, end)
        finished = False
        try:
            while True:
                if pages >= limits.max_pages or monotonic() - started >= limits.max_seconds:
                    budget_hit = True
                    break
                page = next(pager, None)
                if page is None:
                    finished = True
                    break
                pages += 1
                session.pages += 1
                for trade in page:
                    session.add(trade)
        except MarketDataError as exc:
            errors.append(f"{type(exc).__name__}: {str(exc)[:200]}")
        except (KeyError, ValueError, ArithmeticError, TypeError) as exc:
            errors.append(f"malformed trade from Alpaca: {type(exc).__name__}")
        finally:
            close = getattr(pager, "close", None)
            if close is not None:
                close()
        if not finished:
            # A session read only in part would bias the levels: it is dropped whole.
            missing.append(f"session:{session_date.isoformat()}")
            break
        done.append(session)
    for session_date, _start, _end, _partial in planned[len(done) + (1 if missing else 0) :]:
        missing.append(f"session:{session_date.isoformat()}")
    if budget_hit:
        notes.append(
            f"받는 상한(쪽 {limits.max_pages}, {limits.max_seconds:g}초)에 걸려 "
            f"최근 {len(done)}개 세션만 계산했다."
        )
    if len(planned) < limits.sessions:
        notes.append(f"cutoff 전 세션이 {len(planned)}개뿐이다.")

    by_price: dict[Decimal, Decimal] = {}
    for session in done:
        for price, volume in session.by_price.items():
            by_price[price] = by_price.get(price, Decimal(0)) + volume
    reference_session = next((s for s in done if s.last_price is not None), None)
    reference = None if reference_session is None else reference_session.last_price
    priced_total = sum(by_price.values(), Decimal(0))
    total = sum((s.total_volume for s in done), Decimal(0))
    off_total = sum((s.off_volume for s in done), Decimal(0))

    width: Decimal | None = None
    histogram: list[dict[str, str]] = []
    levels: list[dict[str, Any]] = []
    if reference is not None and priced_total > 0:
        width = bin_width(reference)
        bins = _bins(by_price, width)
        while len(bins) > limits.max_bins:
            width *= 2
            bins = _bins(by_price, width)
        histogram = [
            {"price_low": _text(low), "price_high": _text(low + width), "volume": _text(volume)}
            for low, volume in sorted(bins.items())
        ]
        levels = _levels(bins, width, reference, priced_total, limits)
    else:
        missing.append("levels")

    if errors and not done:
        status = "error"
    elif not done:
        status = "incomplete"
    elif priced_total == 0:
        status = "no_data"
    elif missing and any(item.startswith("session:") for item in missing):
        status = "incomplete"
    else:
        status = "ok"

    unverified = list(UNVERIFIED)
    if now - cutoff > timedelta(minutes=publication_delay_minutes + 60):
        unverified.append(
            "지난 cutoff를 나중에 다시 계산했다. 체결의 취소·정정 표시(`u`)는 받은 시점 상태라 "
            "cutoff 때 보였던 것과 다를 수 있다(나중에 받아 재구성)."
        )
    if finra is None:
        missing.append("finra_weekly_ats")

    result: dict[str, Any] = {
        "label": LABEL,
        "data_until_utc": _utc(known_until),
        "sessions_requested": limits.sessions,
        "sessions": [s.document() for s in done],
        "reference_price": None if reference is None else _text(reference),
        "reference_time_utc": None if reference_session is None else reference_session.last_time,
        "bin_width": None if width is None else _text(width),
        "total_volume": _text(total),
        "off_exchange_volume": _text(off_total),
        "off_exchange_share_pct": None
        if total == 0
        else _text((off_total * 100 / total).quantize(Decimal("0.01"))),
        "priced_volume": _text(priced_total),
        "histogram": histogram,
        "levels": levels,
        "context": {"finra_weekly_ats": finra},
        "limits": {
            "pages": pages,
            "seconds": round(monotonic() - started, 1),
            "max_pages": limits.max_pages,
            "max_seconds": limits.max_seconds,
            "min_level_share_pct": _text(limits.min_level_share_pct),
            "max_bins": limits.max_bins,
        },
        "limitations": list(LIMITATIONS),
        "notes": notes,
        "missing": missing,
        "unverified": unverified,
    }
    if status == "error":
        result["reason"] = "; ".join(errors)[:300]
    elif errors:
        notes.extend(errors)
    return {
        "status": status,
        "result": result,
        "versions": {
            "check": CHECK_VERSION,
            "condition_policy": CONDITION_POLICY_VERSION,
            "calendar": calendar.calendar_version,
        },
        # Shown on every card: what these numbers are not.
        "warnings": [*LIMITATIONS, *notes],
        "sources": {
            "trades": {
                "source_id": SOURCE_ID,
                "endpoint": ENDPOINT,
                "feed": "sip",
                "exchange_code": OFF_EXCHANGE_CODE,
                "publication_delay_minutes": publication_delay_minutes,
            },
            "finra_weekly_ats": None if finra is None else "dark_pool_review (ADR 0024)",
        },
    }


def _main(argv: list[str]) -> int:
    """Read-only run with the registered Alpaca key; prints the check (not stored)."""
    parser = argparse.ArgumentParser(prog="python -m services.screening.off_exchange")
    parser.add_argument("symbol")
    parser.add_argument("--cutoff", help="UTC, e.g. 2026-11-06T14:00:00Z (default: now)")
    parser.add_argument("--sessions", type=int, default=OffExchangeLimits.sessions)
    parser.add_argument("--max-pages", type=int, default=OffExchangeLimits.max_pages)
    parser.add_argument("--max-seconds", type=float, default=OffExchangeLimits.max_seconds)
    parser.add_argument("--histogram", action="store_true", help="include every price bin")
    args = parser.parse_args(argv)
    from services.api.main import _LazyBoats, _LazyCredentials

    adapter = _LazyBoats(_LazyCredentials(None).get).get()
    if adapter is None:
        print(json.dumps({"status": "error", "error": "no enabled Alpaca key (SIW_DATA_DIR)"}))
        return 2
    now = datetime.now(timezone.utc)
    cutoff = datetime.fromisoformat(args.cutoff.replace("Z", "+00:00")) if args.cutoff else now
    symbol = args.symbol.upper()
    document = off_exchange_check(
        adapter.trade_pages,
        symbol=symbol,
        cutoff=cutoff,
        now=now,
        publication_delay_minutes=adapter.config.publication_delay_minutes,
        limits=OffExchangeLimits(args.sessions, args.max_pages, args.max_seconds),
    )
    if not args.histogram:
        bins = document["result"]["histogram"]
        document["result"]["histogram"] = f"{len(bins)} bins (use --histogram)"
    print(json.dumps(document, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
