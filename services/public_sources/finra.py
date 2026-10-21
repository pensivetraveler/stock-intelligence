"""FINRA OTC Transparency collector (ADR 0024): weekly ATS and non-ATS volume by symbol.

This is delayed weekly aggregate volume: FINRA publishes NMS Tier 1 weeks two weeks
late and Tier 2 and OTC weeks four weeks late. It has no direction, prices or intraday
timing, so it is never a bar or a live dark-pool print. FINRA's API needs an OAuth client
credential (a free Public credential); the token is kept in memory only.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from services.public_sources.cache import PublicSourceCache, SourceRecord, utc_text
from services.public_sources.http import (
    TIMEOUT_SECONDS,
    RateLimiter,
    SourceConfigError,
    SourceError,
    needs_fetch,
)
from services.public_sources.sec import eastern_day_start

TOKEN_URL = (
    "https://ews.fip.finra.org/fip/rest/ews/oauth2/access_token?grant_type=client_credentials"
)
DATA_URL = "https://api.finra.org/data/group/otcMarket/name/weeklySummary"
FINRA_WEEKLY = "finra-otc-weekly"
PARSER_VERSION = "finra-1"
REQUEST_INTERVAL = 0.2
ATS = "ATS_W_SMBL"
NON_ATS = "OTC_W_SMBL"
PAGE_LIMIT = 5000
FIELDS = (
    "issueSymbolIdentifier",
    "issueName",
    "tierIdentifier",
    "summaryTypeCode",
    "weekStartDate",
    "totalWeeklyShareQuantity",
    "totalWeeklyTradeCount",
    "initialPublishedDate",
    "lastUpdateDate",
    "lastReportedDate",
)


@dataclass(frozen=True)
class FinraCredential:
    client_id: str
    client_secret: str


@dataclass(frozen=True)
class Week:
    summary_type: str
    week_start: date
    tier: str
    shares: Decimal
    trades: Decimal
    published: date
    last_update: date | None
    record: SourceRecord


def _week(record: SourceRecord) -> Week:
    doc = record.document
    return Week(
        doc["summary_type"],
        date.fromisoformat(doc["week_start"]),
        doc.get("tier") or "",
        Decimal(str(doc["shares"])),
        Decimal(str(doc["trades"])),
        date.fromisoformat(doc["initial_published"]),
        date.fromisoformat(doc["last_update"]) if doc.get("last_update") else None,
        record,
    )


class TokenHolder:
    """The current bearer token, shared by requests, never written to disk."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.token: str | None = None
        self.expires: datetime | None = None
        self.owner: str | None = None  # the client ID it was issued to


class Finra:
    """One request's view of FINRA weekly summaries; `notes` lists what to disclose."""

    def __init__(
        self,
        cache: PublicSourceCache,
        credential: FinraCredential | None,
        client: httpx.Client,
        limiter: RateLimiter,
        tokens: TokenHolder,
    ) -> None:
        self._cache = cache
        self._credential = credential
        self._client = client
        self._limiter = limiter
        self._tokens = tokens
        self.notes: list[str] = []
        self.requests = 0

    def _token(self) -> str:
        credential = self._credential
        if credential is None:
            raise SourceConfigError(
                "finra_credential_missing",
                "Register a FINRA API credential (client ID and secret) on the Keys screen",
            )
        holder, now = self._tokens, self._cache.now()
        with holder.lock:
            fresh = holder.expires is not None and now < holder.expires
            if holder.token and fresh and holder.owner == credential.client_id:
                return holder.token
            self._limiter.wait()
            self.requests += 1
            try:
                response = self._client.post(
                    TOKEN_URL,
                    auth=(credential.client_id, credential.client_secret),
                    timeout=TIMEOUT_SECONDS,
                )
            except httpx.HTTPError as exc:
                raise SourceError(f"FINRA could not be reached: {type(exc).__name__}") from exc
            if response.status_code != 200:
                raise SourceError(
                    f"FINRA refused the credential (HTTP {response.status_code}); "
                    "check the key on the Keys screen"
                )
            body = response.json()
            lifetime = int(body.get("expires_in", 1800))
            holder.token = str(body["access_token"])
            # Renew well before FINRA's expiry.
            holder.expires = now + timedelta(seconds=min(lifetime, 1800) - 60)
            holder.owner = credential.client_id
            return holder.token

    def _query(
        self, symbol: str, summary_type: str, start: date, end: date
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            token = self._token()
            self._limiter.wait()
            self.requests += 1
            body = {
                "fields": list(FIELDS),
                "compareFilters": [
                    {
                        "fieldName": "issueSymbolIdentifier",
                        "fieldValue": symbol,
                        "compareType": "EQUAL",
                    },
                    {
                        "fieldName": "summaryTypeCode",
                        "fieldValue": summary_type,
                        "compareType": "EQUAL",
                    },
                ],
                "dateRangeFilters": [
                    {
                        "fieldName": "weekStartDate",
                        "startDate": start.isoformat(),
                        "endDate": end.isoformat(),
                    }
                ],
                "limit": PAGE_LIMIT,
                "offset": offset,
            }
            try:
                response = self._client.post(
                    DATA_URL,
                    json=body,
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                    timeout=TIMEOUT_SECONDS,
                )
            except httpx.HTTPError as exc:
                raise SourceError(f"FINRA could not be reached: {type(exc).__name__}") from exc
            if response.status_code == 204:
                break
            if response.status_code != 200:
                raise SourceError(f"FINRA answered HTTP {response.status_code}")
            page = response.json() if response.content else []
            rows.extend(page)
            if len(page) < PAGE_LIMIT:
                break
            offset += PAGE_LIMIT
        return rows

    def _fetch(self, symbol: str, start: date, end: date) -> None:
        fetched = {kind: self._query(symbol, kind, start, end) for kind in (ATS, NON_ATS)}
        with self._cache.batch():
            for kind, rows in fetched.items():
                for row in rows:
                    week = str(row["weekStartDate"])
                    published = str(row.get("initialPublishedDate") or "")
                    if not published:
                        continue
                    document = {
                        "symbol": symbol,
                        "summary_type": kind,
                        "week_start": week,
                        "tier": row.get("tierIdentifier"),
                        "issue_name": row.get("issueName"),
                        "shares": str(row.get("totalWeeklyShareQuantity") or 0),
                        "trades": str(row.get("totalWeeklyTradeCount") or 0),
                        "initial_published": published,
                        "last_update": row.get("lastUpdateDate"),
                        "last_reported": row.get("lastReportedDate"),
                    }
                    self._cache.put(
                        FINRA_WEEKLY,
                        f"{symbol}:{kind}:{week}",
                        document,
                        utc_text(eastern_day_start(date.fromisoformat(published))),
                        PARSER_VERSION,
                    )
        payload = repr(sorted((k, len(v)) for k, v in fetched.items())).encode()
        self._cache.mark_fetched(FINRA_WEEKLY, f"{symbol}:{start}:{end}", payload)

    def weeks(self, symbol: str, start: date, end: date, cutoff: datetime) -> list[Week]:
        """ATS and non-ATS symbol weeks starting in [start, end], after a refresh if due."""
        key = f"{symbol}:{start}:{end}"
        last = self._cache.last_fetch(FINRA_WEEKLY, key)
        if needs_fetch(last, cutoff, self._cache.now()):
            try:
                self._fetch(symbol, start, end)
            except SourceError as exc:
                if last is None:
                    raise
                self.notes.append(f"FINRA data from {utc_text(last)} was used: {exc}")
        found = [_week(r) for r in self._cache.records(FINRA_WEEKLY, f"{symbol}:", as_of=cutoff)]
        return sorted(
            (w for w in found if start <= w.week_start <= end),
            key=lambda w: (w.week_start, w.summary_type),
        )

    def last_download(self, symbol: str, start: date, end: date) -> datetime | None:
        return self._cache.last_fetch(FINRA_WEEKLY, f"{symbol}:{start}:{end}")
