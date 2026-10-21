"""SEC EDGAR collector (ADR 0024): ticker map, Submissions and CompanyFacts JSON.

SEC's fair-access rules ask for a declared User-Agent with a contact and at most 10
requests per second; this client sends at most 5 per second and refuses to send anything
without `SIW_SEC_USER_AGENT`. Only structured metadata is kept: no filing text.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from services.public_sources.cache import PublicSourceCache, SourceRecord, utc_text
from services.public_sources.http import (
    TIMEOUT_SECONDS,
    RateLimiter,
    SourceConfigError,
    SourceError,
    needs_fetch,
)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/{name}"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/{accession}-index.htm"

SEC_TICKERS = "sec-edgar-tickers"
SEC_SUBMISSIONS = "sec-edgar-submissions"
SEC_FACTS = "sec-edgar-companyfacts"
PARSER_VERSION = "sec-1"
REQUEST_INTERVAL = 0.2  # 5 requests per second; SEC allows 10
TICKER_MAX_AGE = timedelta(days=1)
EASTERN = ZoneInfo("America/New_York")

#: XBRL concepts read from CompanyFacts, in order of preference, with their unit.
REVENUE_CONCEPTS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
)
EPS_CONCEPT = "EarningsPerShareDiluted"
FACT_CONCEPTS = (*((c, "USD") for c in REVENUE_CONCEPTS), (EPS_CONCEPT, "USD/shares"))


def sec_ticker(symbol: str) -> str:
    """SEC writes share classes with a dash (BRK-B) where listings use a dot."""
    return symbol.upper().replace(".", "-")


def accepted_utc(text: str) -> datetime:
    """EDGAR's `acceptanceDateTime` as the true UTC acceptance time.

    The Submissions JSON writes a `Z`, but compared with each filing's own header
    (`ACCEPTANCE-DATETIME`, US/Eastern) the value is late by the Eastern UTC offset: 4
    hours in summer, 5 in winter (checked against AAPL filings on 2026-10-21, ADR 0024).
    The true time is the one whose Eastern offset explains that shift.
    """
    shown = datetime.fromisoformat(text.replace("Z", "")).replace(tzinfo=timezone.utc)
    for hours in (4, 5):
        candidate = shown - timedelta(hours=hours)
        offset = candidate.astimezone(EASTERN).utcoffset()
        if offset is not None and -offset == timedelta(hours=hours):
            return candidate
    return shown - timedelta(hours=4)  # around a DST change: the later of the two


def index_url(cik: int, accession: str) -> str:
    return INDEX_URL.format(cik=cik, folder=accession.replace("-", ""), accession=accession)


@dataclass(frozen=True)
class Filing:
    accession: str
    form: str
    accepted: datetime
    filing_date: date
    report_date: date | None
    items: tuple[str, ...]
    record: SourceRecord


def _filing(record: SourceRecord) -> Filing:
    doc = record.document
    return Filing(
        doc["accession"],
        doc["form"],
        accepted_utc(doc["accepted"]),
        date.fromisoformat(doc["filing_date"]),
        date.fromisoformat(doc["report_date"]) if doc.get("report_date") else None,
        tuple(doc.get("items", [])),
        record,
    )


class SecEdgar:
    """One request's view of EDGAR; `notes` collects what the evidence must disclose."""

    def __init__(
        self,
        cache: PublicSourceCache,
        user_agent: str | None,
        client: httpx.Client,
        limiter: RateLimiter,
    ) -> None:
        self._cache = cache
        self._user_agent = (user_agent or "").strip()
        self._client = client
        self._limiter = limiter
        self.notes: list[str] = []
        self.requests = 0

    def _get(self, url: str) -> bytes:
        if not self._user_agent:
            raise SourceConfigError(
                "sec_user_agent_missing",
                "Set SIW_SEC_USER_AGENT (a name and a contact e-mail) before SEC requests",
            )
        self._limiter.wait()
        self.requests += 1
        try:
            response = self._client.get(
                url,
                headers={"User-Agent": self._user_agent, "Accept-Encoding": "gzip, deflate"},
                timeout=TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise SourceError(f"SEC EDGAR could not be reached: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise SourceError(f"SEC EDGAR answered HTTP {response.status_code} for {url}")
        return response.content

    def _refresh(self, source_id: str, key: str, cutoff: datetime, fetch: Any) -> None:
        """Run `fetch` when the cache needs it; on failure keep the cache and say so."""
        last = self._cache.last_fetch(source_id, key)
        if not needs_fetch(last, cutoff, self._cache.now()):
            return
        try:
            fetch()
        except SourceConfigError:
            if last is None:
                raise
            self.notes.append(
                f"SEC data from {utc_text(last)} was used; SIW_SEC_USER_AGENT is not set."
            )
        except SourceError as exc:
            if last is None:
                raise
            self.notes.append(f"SEC data from {utc_text(last)} was used: {exc}")

    # --- ticker map --------------------------------------------------------------------

    def company(self, symbol: str) -> tuple[int, str] | None:
        """(CIK, company name) of a US ticker, from SEC's own ticker map."""
        last = self._cache.last_fetch(SEC_TICKERS, "company_tickers")
        now = self._cache.now()
        if last is None or now - last > TICKER_MAX_AGE:
            self._refresh(SEC_TICKERS, "company_tickers", now, self._fetch_tickers)
        found = self._cache.records(SEC_TICKERS, "company_tickers")
        if not found:
            return None
        entry = found[0].document["tickers"].get(sec_ticker(symbol))
        return None if entry is None else (int(entry[0]), str(entry[1]))

    def _fetch_tickers(self) -> None:
        payload = self._get(TICKERS_URL)
        rows = json.loads(payload)
        tickers = {
            str(row["ticker"]).upper(): [int(row["cik_str"]), str(row["title"])]
            for row in rows.values()
        }
        self._cache.put(SEC_TICKERS, "company_tickers", {"tickers": tickers}, None, PARSER_VERSION)
        self._cache.mark_fetched(SEC_TICKERS, "company_tickers", payload)

    # --- submissions -------------------------------------------------------------------

    def filings(self, cik: int, since: date, cutoff: datetime) -> list[Filing]:
        """Filings accepted on or after `since`, as known to the cache after a refresh."""
        key = f"CIK{cik:010d}"
        self._refresh(SEC_SUBMISSIONS, key, cutoff, lambda: self._fetch_submissions(cik, since))
        records = self._cache.records(SEC_SUBMISSIONS, f"{key}:", as_of=cutoff)
        found = [_filing(record) for record in records]
        return sorted(
            (f for f in found if f.accepted.astimezone(EASTERN).date() >= since),
            key=lambda f: f.accepted,
        )

    def last_download(self, cik: int) -> datetime | None:
        return self._cache.last_fetch(SEC_SUBMISSIONS, f"CIK{cik:010d}")

    def _store_columns(self, cik: int, columns: dict[str, list[Any]]) -> int:
        count = 0
        with self._cache.batch():
            count = self._store_rows(cik, columns)
        return count

    def _store_rows(self, cik: int, columns: dict[str, list[Any]]) -> int:
        count = 0
        for i, accession in enumerate(columns.get("accessionNumber", [])):
            accepted = columns["acceptanceDateTime"][i]
            if not accepted:
                continue
            items = str(columns.get("items", [""] * (i + 1))[i] or "")
            document = {
                "cik": cik,
                "accession": accession,
                "form": columns["form"][i],
                "filing_date": columns["filingDate"][i],
                "report_date": columns.get("reportDate", [None] * (i + 1))[i] or None,
                "accepted": accepted,
                "items": [item.strip() for item in items.split(",") if item.strip()],
                "primary_document": columns.get("primaryDocument", [""] * (i + 1))[i] or "",
            }
            self._cache.put(
                SEC_SUBMISSIONS,
                f"CIK{cik:010d}:{accession}",
                document,
                utc_text(accepted_utc(accepted)),
                PARSER_VERSION,
            )
            count += 1
        return count

    def _fetch_submissions(self, cik: int, since: date) -> None:
        name = f"CIK{cik:010d}.json"
        payload = self._get(SUBMISSIONS_URL.format(name=name))
        data = json.loads(payload)
        self._store_columns(cik, data["filings"]["recent"])
        self._cache.mark_fetched(SEC_SUBMISSIONS, f"CIK{cik:010d}", payload)
        # Older filings live in extra pages; those pages do not change, so each is read once.
        for page in data["filings"].get("files", []):
            if date.fromisoformat(page["filingTo"]) < since:
                continue
            if self._cache.last_fetch(SEC_SUBMISSIONS, page["name"]) is not None:
                continue
            older = self._get(SUBMISSIONS_URL.format(name=page["name"]))
            self._store_columns(cik, json.loads(older))
            self._cache.mark_fetched(SEC_SUBMISSIONS, page["name"], older)

    # --- company facts -----------------------------------------------------------------

    def facts(self, cik: int, cutoff: datetime) -> dict[str, dict[str, Any]]:
        """XBRL entries of the concepts in FACT_CONCEPTS, keyed by concept."""
        key = f"CIK{cik:010d}"
        self._refresh(SEC_FACTS, key, cutoff, lambda: self._fetch_facts(cik))
        return {
            record.document["concept"]: record.document
            for record in self._cache.records(SEC_FACTS, f"{key}:", as_of=cutoff)
        }

    def _fetch_facts(self, cik: int) -> None:
        try:
            payload = self._get(FACTS_URL.format(cik=cik))
        except SourceError as exc:
            if "HTTP 404" in str(exc):  # a filer without XBRL financial data
                self._cache.mark_fetched(SEC_FACTS, f"CIK{cik:010d}", b"")
                return
            raise
        gaap = json.loads(payload).get("facts", {}).get("us-gaap", {})
        for concept, unit in FACT_CONCEPTS:
            entries = gaap.get(concept, {}).get("units", {}).get(unit)
            if not entries:
                continue
            kept = [
                {name: entry.get(name) for name in ("start", "end", "val", "accn", "fp", "form")}
                | {"filed": entry.get("filed")}
                for entry in entries
                if entry.get("start") and entry.get("end") and entry.get("filed")
            ]
            self._cache.put(
                SEC_FACTS,
                f"CIK{cik:010d}:{concept}",
                {"concept": concept, "unit": unit, "entries": kept},
                None,
                PARSER_VERSION,
            )
        self._cache.mark_fetched(SEC_FACTS, f"CIK{cik:010d}", payload)


def eastern_day_start(day: date) -> datetime:
    return datetime.combine(day, time(0), EASTERN).astimezone(timezone.utc)
