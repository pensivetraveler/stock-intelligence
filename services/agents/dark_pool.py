"""Dark pool evidence (ADR 0024): FINRA weekly ATS volume known at a cutoff.

A FINRA week counts as known only if its initial publication date is before the
cutoff's US/Eastern date (FINRA gives a date, not a time, so the same day is not
assumed). The data is delayed weekly aggregate volume, with no direction, price or
intraday timing; every analysis says so.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from services.agents.evidence import Evidence, Fact
from services.agents.filings import us_symbol
from services.backtest.data import GetBars, load_instrument
from services.market_data.provider import MarketDataError
from services.public_sources.cache import utc_text
from services.public_sources.finra import ATS, NON_ATS, Finra, Week
from services.public_sources.sec import EASTERN

AGENT_ID = "dark_pool_review"
AGENT_VERSION = "dark-pool-review-1"
SOURCE_VERSIONS = {"finra-otc-parser": "finra-1"}
MIN_HISTORY = 4
#: Feeds whose daily volume covers the whole consolidated tape.
CONSOLIDATED_FEED_SCOPES = frozenset({"alpaca-sip"})
NATURE = (
    "FINRA weekly OTC/ATS data is delayed aggregate volume. It shows neither direction "
    "(buy or sell), nor prices, nor intraday timing, and it is not live dark-pool prints."
)


def _mean(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal(0)) / len(values)


def _percentile(value: Decimal, history: list[Decimal]) -> float:
    """Share of history weeks at or below `value`."""
    return sum(1 for item in history if item <= value) / len(history)


def _compare(
    evidence: Evidence, code: str, label: str, value: Decimal, history: list[Decimal]
) -> None:
    if len(history) < MIN_HISTORY or _mean(history) == 0:
        evidence.missing.append(f"{code}_vs_mean")
        return
    ratio = value / _mean(history)
    rank = _percentile(value, history)
    evidence.facts.append(
        Fact(
            f"{code}_vs_mean",
            f"{float(ratio):.2f}",
            "ratio",
            f"{label} is {float(ratio):.2f}x its mean over the previous {len(history)} "
            f"published weeks (at or above {rank:.0%} of them)",
        )
    )


def dark_pool_review(
    finra: Finra,
    get_bars: GetBars,
    instrument_id: str,
    cutoff: datetime,
    weeks: int,
    consolidated: frozenset[str] = CONSOLIDATED_FEED_SCOPES,
) -> Evidence:
    symbol = us_symbol(instrument_id)
    evidence = Evidence(
        {"type": "dark_pool", "instrument_id": instrument_id, "weeks": weeks},
        utc_text(cutoff),
        agent_id=AGENT_ID,
        agent_version=AGENT_VERSION,
        indicator_versions=dict(SOURCE_VERSIONS),
    )
    evidence.uncertainty.append(NATURE)
    day = cutoff.astimezone(EASTERN).date()
    # FINRA's lag is up to four weeks; read that much more than the history asked for.
    start = day - timedelta(weeks=weeks + 5)
    found = finra.weeks(symbol, start, day, cutoff)
    known = [w for w in found if w.published < day]
    ats = {w.week_start: w for w in known if w.summary_type == ATS}
    non_ats = {w.week_start: w for w in known if w.summary_type == NON_ATS}
    unpublished = sum(1 for w in found if w.published >= day and w.summary_type == ATS)

    last = finra.last_download(symbol, start, day)
    if last is not None:
        evidence.dataset_revisions.add(f"finra-otc-weekly@{utc_text(last)}")
    evidence.uncertainty.extend(finra.notes)
    if not ats:
        evidence.missing.extend(["ats_shares", "ats_share_of_off_exchange_pct"])
        evidence.uncertainty.append(
            f"No FINRA ATS week for {symbol} had been published before the cutoff."
        )
        return evidence

    ordered = sorted(ats)
    latest_start = ordered[-1]
    latest = ats[latest_start]
    history_starts = ordered[:-1][-weeks:]
    history = [ats[s] for s in history_starts]
    facts = evidence.facts
    tier = {"T1": "NMS Tier 1", "T2": "NMS Tier 2"}.get(latest.tier, latest.tier or "unknown")
    facts.append(
        Fact(
            "ats_week",
            latest_start.isoformat(),
            "text",
            f"Latest FINRA ATS week known at the cutoff starts {latest_start.isoformat()}, "
            f"first published {latest.published.isoformat()} ({tier})",
        )
    )
    facts.append(
        Fact(
            "ats_shares",
            str(latest.shares),
            "count",
            f"ATS volume that week: {latest.shares:,} shares",
        )
    )
    facts.append(
        Fact("ats_trades", str(latest.trades), "count", f"ATS trades that week: {latest.trades:,}")
    )
    evidence.source_refs.append(latest.record.record_id)
    other = non_ats.get(latest_start)
    if other is None:
        evidence.missing.append("ats_share_of_off_exchange_pct")
    else:
        evidence.source_refs.append(other.record.record_id)
        facts.append(
            Fact(
                "otc_non_ats_shares",
                str(other.shares),
                "count",
                f"Non-ATS OTC volume that week: {other.shares:,} shares",
            )
        )
        total = latest.shares + other.shares
        if total > 0:
            share = latest.shares / total
            facts.append(
                Fact(
                    "ats_share_of_off_exchange_pct",
                    f"{float(share) * 100:.2f}",
                    "percent",
                    f"ATS was {float(share):.2%} of the week's off-exchange (OTC) volume",
                )
            )
            shares_history = [
                w.shares / (w.shares + non_ats[w.week_start].shares)
                for w in history
                if w.week_start in non_ats and w.shares + non_ats[w.week_start].shares > 0
            ]
            _compare(
                evidence,
                "ats_share_of_off_exchange",
                "The ATS share of off-exchange volume",
                share,
                shares_history,
            )
    facts.append(
        Fact(
            "ats_history_weeks",
            str(len(history)),
            "count",
            f"{len(history)} earlier published ATS weeks are used for comparison",
        )
    )
    _compare(evidence, "ats_shares", "ATS volume", latest.shares, [w.shares for w in history])
    if latest.trades > 0:
        size = latest.shares / latest.trades
        facts.append(
            Fact(
                "ats_avg_trade_size",
                f"{float(size):.1f}",
                "count",
                f"Average ATS trade size that week: {float(size):.1f} shares",
            )
        )
        _compare(
            evidence,
            "ats_avg_trade_size",
            "Average ATS trade size",
            size,
            [w.shares / w.trades for w in history if w.trades > 0],
        )
    _share_of_total(evidence, get_bars, instrument_id, latest, cutoff, consolidated)

    if latest.last_update is not None and latest.last_update >= day:
        evidence.uncertainty.append(
            f"FINRA updated the {latest_start.isoformat()} week on "
            f"{latest.last_update.isoformat()}, after the cutoff; the value shown may "
            "include corrections not available at the cutoff."
        )
    if latest.record.observed_after_publication():
        evidence.uncertainty.append(
            "These FINRA weeks were first downloaded by this system after publication; "
            "their availability at the cutoff is reconstructed from FINRA's publication "
            "dates, not observed."
        )
    if unpublished:
        evidence.uncertainty.append(
            f"{unpublished} later weeks were not yet published at the cutoff and are excluded."
        )
    return evidence


def _share_of_total(
    evidence: Evidence,
    get_bars: GetBars,
    instrument_id: str,
    week: Week,
    cutoff: datetime,
    consolidated: frozenset[str],
) -> None:
    """ATS volume over the week's consolidated volume, only from a consolidated feed."""
    code = "ats_share_of_total_volume_pct"
    try:
        loaded = load_instrument(
            get_bars,
            instrument_id,
            "1d",
            week.week_start,
            week.week_start + timedelta(days=4),
            as_of_utc=cutoff,
        )
    except MarketDataError:
        evidence.missing.append(code)
        return
    scopes = set(loaded.pin["feed_scopes"])
    if not scopes or not scopes <= consolidated:
        evidence.missing.append(code)
        evidence.uncertainty.append(
            "The share of total volume needs consolidated daily volume; the local bars come "
            f"from {', '.join(sorted(scopes)) or 'no feed'}, so it was not computed."
        )
        return
    total = sum((bar.volume for bar in loaded.bars), Decimal(0))
    if total <= 0:
        evidence.missing.append(code)
        return
    share = week.shares / total
    evidence.dataset_revisions.update(loaded.pin["dataset_revisions"])
    evidence.facts.append(
        Fact(
            code,
            f"{float(share) * 100:.2f}",
            "percent",
            f"ATS volume was {float(share):.2%} of the week's consolidated volume "
            f"({total:,} shares over {len(loaded.bars)} sessions)",
        )
    )
