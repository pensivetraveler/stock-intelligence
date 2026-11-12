"""Off-exchange (TRF-reported) volume by price before a cutoff (ADR 0052 §5, S2).

Reads Alpaca SIP trades and keeps those Alpaca marks with exchange code `D` (named
"FINRA ADF" by `GET /v2/stocks/meta/exchanges`; FINRA TRF reports are carried under
the same code, measured 2026-11-09). These are off-exchange trades: ATS ("dark pool")
trades and broker internalization mixed together, with no buy/sell direction, and the
SIP timestamp is when the trade was reported, which can differ from when it happened.
Every result says so. Nothing is built from 1-minute bars.

Closed sessions come from the structure model's session cache (ADR 0053), read once on
background threads and shared by both checks; only the session still open at the cutoff
is read here, newest first, up to the cutoff. The result is the `off_exchange` part of
one `screening_check` (ADR 0052 S1).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_FLOOR, Decimal
from typing import Any

from services.market_data.calendar import NEW_YORK, ExchangeCalendar, UsEquityCalendar
from services.market_data.provider import MarketDataError
from services.screening.structure.sessions import (
    LEVELS_VERSION,
    SessionCache,
    SessionFiller,
    TradePages,
    _Reader,
    closed_sessions,
)
from services.screening.trade_conditions import (
    AVERAGE_PRICE_ON_TAPES_AB,
    CONDITION_POLICY_VERSION,
    LATE_REPORT,
    NOT_TRADES,
    OFF_EXCHANGE_CODE,
    OFF_MARKET_PRICE,
    REMOVED_UPDATES,
    SOURCE_ID,
)

__all__ = [
    "AVERAGE_PRICE_ON_TAPES_AB",
    "CONDITION_POLICY_VERSION",
    "LATE_REPORT",
    "NOT_TRADES",
    "OFF_EXCHANGE_CODE",
    "OFF_MARKET_PRICE",
    "REMOVED_UPDATES",
    "SOURCE_ID",
    "OffExchangeLimits",
    "bin_width",
    "finra_context",
    "off_exchange_check",
]

#: 2: twenty closed sessions from the shared cache plus the open session (2026-11-10).
CHECK_VERSION = "off-exchange-check-2"
LABEL = "장외 체결(TRF 보고)"
ENDPOINT = "GET /v2/stocks/{symbol}/trades"

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
RECONSTRUCTED = (
    "지난 cutoff를 나중에 다시 계산했거나 cutoff 뒤에 받은 세션이 있다. 체결의 취소·정정 "
    "표시(`u`)는 받은 시점 상태라 cutoff 때 보였던 것과 다를 수 있다(나중에 받아 재구성)."
)


@dataclass(frozen=True)
class OffExchangeLimits:
    #: Closed sessions, the structure model's twenty (ADR 0053): one shared cache, so the
    #: two checks never read a session twice.
    sessions: int = 20
    #: The open session is read here, newest first: a large cap's full day is 61~131
    #: pages (AAPL, 2026-10-27~11-09) at about 0.5 s a page.
    max_pages: int = 80
    live_max_seconds: float = 25.0
    #: The whole trade budget: reading the open session, then waiting for the closed
    #: sessions being read in the background. Inside the screen's 60 s timeout.
    max_seconds: float = 40.0
    top_levels: int = 5
    #: Peaks holding less than this share of priced volume are noise, not levels.
    min_level_share_pct: Decimal = Decimal(1)
    max_bins: int = 500


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _text(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return "0" if text in ("-0", "") else text


def _share(part: Decimal, whole: Decimal) -> str | None:
    if whole <= 0:
        return None
    return _text((part * 100 / whole).quantize(Decimal("0.01")))


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


def _live_session(
    reader: _Reader,
    session_date: date,
    start: datetime,
    end: datetime,
    truncated: bool,
    read_at: datetime,
) -> dict[str, Any]:
    if truncated and reader.first_time is not None:
        # Read newest first and stopped: only trades from here to the end were read.
        start = datetime.fromisoformat(reader.first_time[:19] + "+00:00")
    priced = sum(reader.off.values(), Decimal(0))
    return {
        "session_date": session_date.isoformat(),
        "source": "live",
        "read_at_utc": _utc(read_at),
        "range_utc": [_utc(start), _utc(end)],
        "partial": True,
        "truncated": truncated,
        "pages": reader.pages,
        "trades": reader.trades,
        "total_volume": _text(reader.total_volume),
        "off_exchange_volume": _text(reader.off_volume),
        "off_exchange_trades": reader.off_trades,
        "off_exchange_share_pct": _share(reader.off_volume, reader.total_volume),
        "priced_volume": _text(priced),
        "off_market_price_volume": _text(reader.off_exchange_off_market_volume),
        "late_report_volume": _text(reader.late_report_volume),
        "removed_trades": reader.removed_trades,
    }


def _cached_session(document: dict[str, Any]) -> dict[str, Any]:
    total = Decimal(document["total_volume"])
    off = Decimal(document["off_exchange_volume"])
    priced = sum((Decimal(level[1]) for level in document["levels"]), Decimal(0))
    return {
        "session_date": document["session_date"],
        "source": "cache",
        "read_at_utc": document["read_at_utc"],
        "range_utc": list(document["range_utc"]),
        "partial": False,
        "truncated": False,
        "pages": document["pages"],
        "trades": document["trades"],
        "total_volume": document["total_volume"],
        "off_exchange_volume": document["off_exchange_volume"],
        "off_exchange_trades": document["off_exchange_trades"],
        "off_exchange_share_pct": _share(off, total),
        "priced_volume": _text(priced),
        "off_market_price_volume": document["off_exchange_off_market_price_volume"],
        "late_report_volume": document["late_report_volume"],
        "removed_trades": document["removed_trades"],
    }


def off_exchange_check(
    trade_pages: TradePages,
    *,
    symbol: str,
    cutoff: datetime,
    now: datetime,
    publication_delay_minutes: int,
    cache: SessionCache,
    filler: SessionFiller,
    limits: OffExchangeLimits | None = None,
    finra: dict[str, Any] | None = None,
    calendar: ExchangeCalendar | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Off-exchange volume by price over the closed sessions and the open one at `cutoff`.

    The closed sessions not cached yet are queued on `filler` first; then the open
    session is read here; then the check waits for the queue up to `max_seconds` in all.
    Sessions still being read leave the check `pending`: posting again shows the rest.

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
    started = monotonic()
    windows = closed_sessions(calendar, known_until, limits.sessions)
    filler.request(symbol, windows)

    missing: list[str] = []
    notes: list[str] = []
    errors: list[str] = []
    live: dict[str, Any] | None = None
    live_reader: _Reader | None = None
    live_pages = 0
    current = calendar.window_for_date(known_until.astimezone(NEW_YORK).date())
    if current is not None and current.pre_open_utc < known_until < current.post_close_utc:
        reader = _Reader()
        pager = trade_pages(symbol, current.pre_open_utc, known_until, sort="desc")
        finished = False
        try:
            while True:
                elapsed = monotonic() - started
                if reader.pages >= limits.max_pages or elapsed >= limits.live_max_seconds:
                    break
                page = next(pager, None)
                if page is None:
                    finished = True
                    break
                reader.pages += 1
                for trade in page:
                    reader.add(trade)
        except MarketDataError as exc:
            errors.append(f"{type(exc).__name__}: {str(exc)[:200]}")
        except (KeyError, ValueError, ArithmeticError, TypeError) as exc:
            errors.append(f"malformed trade from Alpaca: {type(exc).__name__}")
        finally:
            close = getattr(pager, "close", None)
            if close is not None:
                close()
        live_pages = reader.pages
        session_date = current.session_date.isoformat()
        if errors:
            missing.append(f"session:{session_date}")
        elif not finished:
            missing.append(f"session:{session_date}")
            if reader.trades:
                live_reader = reader
                live = _live_session(
                    reader, current.session_date, current.pre_open_utc, known_until, True, now
                )
                notes.append(
                    f"받는 상한(쪽 {limits.max_pages}, {limits.live_max_seconds:g}초)에 걸려 "
                    f"열려 있는 세션의 일부({live['range_utc'][0]}부터 cutoff까지)만 계산했다."
                )
        else:
            live_reader = reader
            live = _live_session(
                reader, current.session_date, current.pre_open_utc, known_until, False, now
            )

    filler.wait(symbol, max(0.0, limits.max_seconds - (monotonic() - started)), sleep)

    cached: list[dict[str, Any]] = []
    progress_missing: list[dict[str, Any]] = []
    for window in windows:
        try:
            document = cache.get(symbol, window.session_date)
        except ValueError as exc:
            notes.append(str(exc)[:200])
            document = None
        if document is None:
            progress_missing.append(
                {
                    "session_date": window.session_date.isoformat(),
                    "reason": filler.error(symbol, window.session_date),
                }
            )
            missing.append(f"session:{window.session_date.isoformat()}")
        else:
            cached.append(document)
    reading = filler.pending(symbol) if progress_missing else 0
    if reading:
        notes.append(
            f"닫힌 세션 {len(cached)}/{len(windows)}개를 받았다. 나머지는 뒤에서 받는 중이다. "
            "같은 검사를 다시 하면 이어서 보인다."
        )
    if len(windows) < limits.sessions:
        notes.append(f"cutoff 전 닫힌 세션이 {len(windows)}개뿐이다.")

    # Newest first: the open session, then the closed ones.
    newest = sorted(cached, key=lambda d: d["session_date"], reverse=True)
    sessions = ([live] if live is not None else []) + [_cached_session(d) for d in newest]
    by_price: dict[Decimal, Decimal] = {}
    if live_reader is not None:
        for price, volume in live_reader.off.items():
            by_price[price] = by_price.get(price, Decimal(0)) + volume
    for document in cached:
        for price_text, off_text, _lit in document["levels"]:
            off_volume = Decimal(off_text)
            if off_volume > 0:
                price = Decimal(price_text)
                by_price[price] = by_price.get(price, Decimal(0)) + off_volume
    reference: Decimal | None = None
    reference_time: str | None = None
    if live_reader is not None and live_reader.last_price is not None:
        reference, reference_time = live_reader.last_price, live_reader.last_time
    else:
        for document in newest:
            if document["last_price"] is not None:
                reference = Decimal(document["last_price"])
                reference_time = document["last_time_utc"]
                break
    priced_total = sum(by_price.values(), Decimal(0))
    total = sum((Decimal(s["total_volume"]) for s in sessions), Decimal(0))
    off_total = sum((Decimal(s["off_exchange_volume"]) for s in sessions), Decimal(0))

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

    if reading:
        status = "pending"
    elif errors and not sessions:
        status = "error"
    elif not sessions:
        status = "incomplete"
    elif priced_total == 0:
        status = "no_data"
    elif any(item.startswith("session:") for item in missing):
        status = "incomplete"
    else:
        status = "ok"

    unverified = list(UNVERIFIED)
    late_run = now - cutoff > timedelta(minutes=publication_delay_minutes + 60)
    # The structure model's rule too (S09): read more than an hour past data_until.
    read_late = _utc(known_until + timedelta(hours=1))
    if late_run or any(d["read_at_utc"] > read_late for d in cached):
        unverified.append(RECONSTRUCTED)
    if finra is None:
        missing.append("finra_weekly_ats")

    result: dict[str, Any] = {
        "label": LABEL,
        "data_until_utc": _utc(known_until),
        "sessions_requested": limits.sessions,
        "sessions": sessions,
        "progress": {
            "total_sessions": len(windows),
            "cached_sessions": len(cached),
            "reading_sessions": reading,
            "missing": progress_missing,
        },
        "reference_price": None if reference is None else _text(reference),
        "reference_time_utc": reference_time,
        "bin_width": None if width is None else _text(width),
        "total_volume": _text(total),
        "off_exchange_volume": _text(off_total),
        "off_exchange_share_pct": _share(off_total, total),
        "priced_volume": _text(priced_total),
        "histogram": histogram,
        "levels": levels,
        "context": {"finra_weekly_ats": finra},
        "limits": {
            "pages": live_pages,
            "seconds": round(monotonic() - started, 1),
            "max_pages": limits.max_pages,
            "live_max_seconds": limits.live_max_seconds,
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
            "session_levels": LEVELS_VERSION,
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
                "closed_sessions": "screening_structure.sqlite3 session_levels (ADR 0053)",
            },
            "finra_weekly_ats": None if finra is None else "dark_pool_review (ADR 0024)",
        },
    }


def _main(argv: list[str]) -> int:
    """Run with the registered Alpaca key; prints the check (the check is not stored, the
    closed sessions read go into `SIW_DATA_DIR/screening_structure.sqlite3`)."""
    parser = argparse.ArgumentParser(prog="python -m services.screening.off_exchange")
    parser.add_argument("symbol")
    parser.add_argument("--cutoff", help="UTC, e.g. 2026-11-06T14:00:00Z (default: now)")
    parser.add_argument("--sessions", type=int, default=OffExchangeLimits.sessions)
    parser.add_argument("--histogram", action="store_true", help="include every price bin")
    args = parser.parse_args(argv)
    import os
    from pathlib import Path

    from services.api.main import _LazyBoats, _LazyCredentials
    from services.screening.structure.service import DATABASE, open_structure

    root = os.getenv("SIW_DATA_DIR", "").strip()
    adapter = _LazyBoats(_LazyCredentials(None).get).get()
    if adapter is None or not root:
        print(json.dumps({"status": "error", "error": "no enabled Alpaca key (SIW_DATA_DIR)"}))
        return 2
    now = datetime.now(timezone.utc)
    store = open_structure(Path(root) / DATABASE, lambda: now)
    # Read every missing session before answering (no 60 s screen to stay inside).
    filler = SessionFiller(store.cache, lambda: adapter.trade_pages, lambda: now, inline=True)
    cutoff = datetime.fromisoformat(args.cutoff.replace("Z", "+00:00")) if args.cutoff else now
    document = off_exchange_check(
        adapter.trade_pages,
        symbol=args.symbol.upper(),
        cutoff=cutoff,
        now=now,
        publication_delay_minutes=adapter.config.publication_delay_minutes,
        cache=store.cache,
        filler=filler,
        limits=OffExchangeLimits(sessions=args.sessions),
    )
    if not args.histogram:
        bins = document["result"]["histogram"]
        document["result"]["histogram"] = f"{len(bins)} bins (use --histogram)"
    print(json.dumps(document, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
