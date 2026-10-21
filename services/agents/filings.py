"""Filings evidence (ADR 0024): SEC filings and XBRL facts known at a cutoff.

A filing is known at the cutoff only if EDGAR accepted it at or before the cutoff. An
XBRL value is known only if the filing that reported it was. Filings after the cutoff
appear only when asked for, flagged as hindsight. Nothing here reads filing text: the
facts are SEC's own form types, item codes, dates and reported values.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from services.agents.evidence import Evidence, Fact
from services.market_data.instruments import parse_instrument
from services.public_sources.cache import utc_text
from services.public_sources.sec import (
    EASTERN,
    EPS_CONCEPT,
    REVENUE_CONCEPTS,
    Filing,
    SecEdgar,
)

AGENT_ID = "filings_review"
AGENT_VERSION = "filings-review-1"
SOURCE_VERSIONS = {"sec-edgar-parser": "sec-1"}
MAX_LISTED = 40
COUNTED_FORMS = ("8-K", "10-Q", "10-K", "S-1", "S-3", "4", "SC 13D", "SC 13G", "DEF 14A")
PERIODIC = ("10-Q", "10-K")
QUARTER_DAYS = (80, 100)

#: Form 8-K item names (SEC Form 8-K General Instructions).
ITEMS_8K = {
    "1.01": "Entry into a material definitive agreement",
    "1.02": "Termination of a material definitive agreement",
    "1.03": "Bankruptcy or receivership",
    "1.04": "Mine safety",
    "1.05": "Material cybersecurity incidents",
    "2.01": "Completion of acquisition or disposition of assets",
    "2.02": "Results of operations and financial condition",
    "2.03": "Creation of a direct financial obligation",
    "2.04": "Triggering events that accelerate a financial obligation",
    "2.05": "Costs of exit or disposal activities",
    "2.06": "Material impairments",
    "3.01": "Notice of delisting or failure to meet listing rules",
    "3.02": "Unregistered sales of equity securities",
    "3.03": "Material modification to rights of security holders",
    "4.01": "Change in the registrant's certifying accountant",
    "4.02": "Non-reliance on previously issued financial statements",
    "5.01": "Changes in control of the registrant",
    "5.02": "Departure or appointment of directors or officers; compensation",
    "5.03": "Amendments to articles of incorporation or bylaws; fiscal year change",
    "5.04": "Temporary suspension of trading under employee benefit plans",
    "5.05": "Amendments to the code of ethics",
    "5.06": "Change in shell company status",
    "5.07": "Submission of matters to a vote of security holders",
    "5.08": "Shareholder director nominations",
    "6.01": "ABS informational and computational material",
    "6.02": "Change of servicer or trustee",
    "6.03": "Change in credit enhancement",
    "6.04": "Failure to make a required distribution",
    "6.05": "Securities Act updating disclosure",
    "7.01": "Regulation FD disclosure",
    "8.01": "Other events",
    "9.01": "Financial statements and exhibits",
}


class UnsupportedTargetError(ValueError):
    """The instrument is outside the source's coverage (for example a Korean listing)."""


def us_symbol(instrument_id: str) -> str:
    parsed = parse_instrument(instrument_id)
    if parsed is None or parsed.market != "US":
        raise UnsupportedTargetError(
            "SEC EDGAR and FINRA cover US listings only; this instrument is not one"
        )
    return parsed.symbol


def _eastern(moment: datetime) -> str:
    return moment.astimezone(EASTERN).strftime("%Y-%m-%d %H:%M ET")


def _days(later: datetime | date, earlier: datetime | date) -> int:
    def day(value: datetime | date) -> date:
        return value.astimezone(EASTERN).date() if isinstance(value, datetime) else value

    return (day(later) - day(earlier)).days


def _describe_items(items: tuple[str, ...]) -> str:
    return "; ".join(f"{item} {ITEMS_8K.get(item, 'item')}" for item in items)


def _filing_fact(filing: Filing, cutoff: datetime, hindsight: bool) -> Fact:
    if hindsight:
        when = f"{_days(filing.accepted, cutoff)} days after the cutoff"
    else:
        when = f"{_days(cutoff, filing.accepted)} days before the cutoff"
    text = f"{filing.form} accepted {_eastern(filing.accepted)} ({when})"
    value = filing.form
    if filing.items:
        text += f": items {_describe_items(filing.items)}"
        value = ", ".join(filing.items)
    elif filing.report_date is not None:
        text += f", period of report {filing.report_date.isoformat()}"
        value = filing.report_date.isoformat()
    return Fact("filing", value, "text", text[:1000], subject=filing.form, hindsight=hindsight)


def _known_entry(entry: dict[str, Any], accepted: dict[str, datetime], cutoff: datetime) -> bool:
    """Was this XBRL value public at the cutoff? By its filing's acceptance time if
    known, otherwise by its filed date, which must be before the cutoff's day."""
    when = accepted.get(str(entry.get("accn")))
    if when is not None:
        return when <= cutoff
    return date.fromisoformat(str(entry["filed"])) < cutoff.astimezone(EASTERN).date()


def _quarterly(
    entries: list[dict[str, Any]], accepted: dict[str, datetime], cutoff: datetime
) -> dict[date, dict[str, Any]]:
    """Per period end, the latest value known at the cutoff, for quarter-long periods."""
    latest: dict[date, dict[str, Any]] = {}
    for entry in entries:
        start, end = date.fromisoformat(entry["start"]), date.fromisoformat(entry["end"])
        if not QUARTER_DAYS[0] <= (end - start).days <= QUARTER_DAYS[1]:
            continue
        if not _known_entry(entry, accepted, cutoff):
            continue
        held = latest.get(end)
        if held is None or str(entry["filed"]) >= str(held["filed"]):
            latest[end] = entry
    return latest


def _xbrl_facts(
    evidence: Evidence,
    facts: dict[str, dict[str, Any]],
    accepted: dict[str, datetime],
    cutoff: datetime,
) -> None:
    revenue = next((facts[c] for c in REVENUE_CONCEPTS if c in facts), None)
    for code, label, concept in (
        ("revenue_q", "quarterly revenue", revenue),
        ("eps_diluted_q", "quarterly diluted EPS", facts.get(EPS_CONCEPT)),
    ):
        if concept is None:
            evidence.missing.append(code)
            continue
        periods = _quarterly(concept["entries"], accepted, cutoff)
        if not periods:
            evidence.missing.append(code)
            continue
        end = max(periods)
        entry = periods[end]
        value = Decimal(str(entry["val"]))
        unit = concept["unit"]
        evidence.facts.append(
            Fact(
                code,
                str(value),
                "currency",
                f"Latest known {label}: {value:,} {unit} for the quarter ending "
                f"{end.isoformat()} ({entry.get('form') or 'filing'} filed {entry['filed']}, "
                f"XBRL concept {concept['concept']})",
            )
        )
        evidence.source_refs.append(str(entry["accn"]))
        year_ago = next(
            (
                periods[other]
                for other in sorted(periods, reverse=True)
                if 355 <= (end - other).days <= 375
            ),
            None,
        )
        base = None if year_ago is None else Decimal(str(year_ago["val"]))
        if base is None or base == 0:
            evidence.missing.append(f"{code}_yoy_pct")
            continue
        change = float((value - base) / abs(base))
        evidence.facts.append(
            Fact(
                f"{code}_yoy_pct",
                f"{change * 100:.2f}",
                "percent",
                f"{label.capitalize()} changed {change:+.2%} from the same quarter a year "
                f"earlier ({base:,} {unit})",
            )
        )


def filings_review(
    sec: SecEdgar,
    instrument_id: str,
    cutoff: datetime,
    window_days: int,
    after_days: int,
    now: datetime,
) -> Evidence:
    symbol = us_symbol(instrument_id)
    evidence = Evidence(
        {
            "type": "filings",
            "instrument_id": instrument_id,
            "window_days": window_days,
            "after_days": after_days,
        },
        utc_text(cutoff),
        agent_id=AGENT_ID,
        agent_version=AGENT_VERSION,
        indicator_versions=dict(SOURCE_VERSIONS),
    )
    company = sec.company(symbol)
    if company is None:
        evidence.missing.append("sec_cik")
        evidence.uncertainty.append(
            f"{symbol} is not in SEC's ticker map, so no filings were read."
        )
        evidence.uncertainty.extend(sec.notes)
        return evidence
    cik, name = company
    window_start = cutoff - timedelta(days=window_days)
    # Look back far enough to find the latest 10-K, which is filed once a year.
    since = (min(window_start, cutoff - timedelta(days=400))).astimezone(EASTERN).date()
    horizon = min(cutoff + timedelta(days=after_days), now)
    filings = sec.filings(cik, since, horizon)
    known = [f for f in filings if f.accepted <= cutoff]
    in_window = [f for f in known if f.accepted > window_start]
    after = [f for f in filings if cutoff < f.accepted <= horizon] if after_days else []

    facts = evidence.facts
    facts.append(Fact("sec_company", f"{cik:010d}", "text", f"SEC registrant: {name} (CIK {cik})"))
    facts.append(
        Fact(
            "filings_in_window",
            str(len(in_window)),
            "count",
            f"{len(in_window)} filings accepted in the {window_days} days before the cutoff",
        )
    )
    counts = Counter(f.form for f in in_window)
    for form in COUNTED_FORMS:
        if counts.get(form):
            facts.append(
                Fact(
                    "form_count",
                    str(counts[form]),
                    "count",
                    f"{counts[form]} {form} filings in the window",
                    subject=form,
                )
            )
    for form in PERIODIC:
        latest = next((f for f in reversed(known) if f.form == form), None)
        code = f"latest_{form.replace('-', '').lower()}"
        if latest is None:
            evidence.missing.append(code)
            continue
        period = latest.report_date.isoformat() if latest.report_date else "unknown"
        facts.append(
            Fact(
                code,
                str(_days(cutoff, latest.accepted)),
                "days",
                f"Latest {form} accepted {_eastern(latest.accepted)} for the period ending "
                f"{period}, {_days(cutoff, latest.accepted)} days before the cutoff",
            )
        )
        evidence.source_refs.append(latest.accession)
    listed = list(reversed(in_window))[:MAX_LISTED]
    for filing in listed:
        facts.append(_filing_fact(filing, cutoff, hindsight=False))
        evidence.source_refs.append(filing.accession)
    if len(in_window) > MAX_LISTED:
        evidence.warnings.append(
            f"Only the latest {MAX_LISTED} of {len(in_window)} filings in the window are listed."
        )
    for filing in after[:MAX_LISTED]:
        facts.append(_filing_fact(filing, cutoff, hindsight=True))
        evidence.source_refs.append(filing.accession)

    accepted = {f.accession: f.accepted for f in filings}
    _xbrl_facts(evidence, sec.facts(cik, horizon), accepted, cutoff)

    used = [f.record for f in in_window] + [f.record for f in after]
    late = sum(1 for record in used if record.observed_after_publication())
    if late:
        evidence.uncertainty.append(
            f"{late} of {len(used)} filings were first downloaded by this system after they "
            "were published; that they were available at the cutoff is reconstructed from "
            "EDGAR acceptance times, not observed."
        )
    last = sec.last_download(cik)
    if last is not None and last < horizon:
        evidence.uncertainty.append(
            f"SEC data was last downloaded at {utc_text(last)}; anything accepted after "
            "that is not included."
        )
    evidence.uncertainty.append(
        "Only SEC form types, 8-K item codes, dates and XBRL values are used; filing text "
        "was not read, and news outside EDGAR is not covered."
    )
    evidence.uncertainty.extend(sec.notes)
    if last is not None:
        evidence.dataset_revisions.add(f"sec-edgar-submissions@{utc_text(last)}")
    evidence.source_refs = list(dict.fromkeys(evidence.source_refs))
    return evidence
