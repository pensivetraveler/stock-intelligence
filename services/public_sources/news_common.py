"""What every news feed shares (ADR 0052 §3): the stored article shape and the refresh rule.

Each feed (Alpaca News, Finnhub, MarketNewsAPI) keeps its own `source_id` in
`public_sources.sqlite3`, one record per ticker and article (`<TICKER>:<id>`). Feeds are
never merged into one another: the same story from two feeds stays two records, each
saying where it came from. A changed article is stored as a new revision next to the
old one (ADR 0024 cache).

Stored document (all feeds): `id`, `headline`, `summary`, `author`, `symbols`,
`created_at` (the feed's publication time, UTC), `updated_at` (UTC or null when the feed
has none), `url`, `source` (the feed's publisher or provider name), `type` (`news`,
`press_release`, `legal` or null) and `body` (a press release's own text where the feed
supplies and allows it, otherwise null).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx

from services.public_sources.cache import PublicSourceCache, SourceRecord, parse_utc, utc_text
from services.public_sources.http import (
    TIMEOUT_SECONDS,
    RateLimiter,
    SourceConfigError,
    SourceError,
    needs_fetch,
)

MAX_LOOKBACK = timedelta(days=30)


@dataclass(frozen=True)
class NewsArticle:
    source_id: str
    article_id: str
    headline: str
    summary: str
    author: str
    symbols: tuple[str, ...]
    published_at: datetime
    updated_at: datetime | None
    url: str | None
    publisher: str
    article_type: str | None
    has_body: bool
    record: SourceRecord

    @property
    def first_observed(self) -> datetime:
        return parse_utc(self.record.first_observed_utc)


def article_from(record: SourceRecord) -> NewsArticle:
    doc = record.document
    updated = doc.get("updated_at")
    return NewsArticle(
        record.source_id,
        str(doc["id"]),
        doc.get("headline") or "",
        doc.get("summary") or "",
        doc.get("author") or "",
        tuple(doc.get("symbols") or ()),
        parse_utc(doc["created_at"]),
        parse_utc(updated) if updated else None,
        doc.get("url") or None,
        doc.get("source") or "",
        doc.get("type") or None,
        bool(doc.get("body")),
        record,
    )


def stored_document(
    *,
    article_id: str,
    headline: str,
    summary: str,
    created_at: datetime,
    updated_at: datetime | None,
    url: str | None,
    source: str,
    symbols: list[str] | tuple[str, ...],
    author: str = "",
    article_type: str | None = None,
    body: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "id": article_id,
        "headline": headline,
        "summary": summary,
        "author": author,
        "symbols": sorted({str(s).upper() for s in symbols if str(s).strip()}),
        "created_at": utc_text(created_at),
        "updated_at": utc_text(updated_at) if updated_at else None,
        "url": url or None,
        "source": source,
        "type": article_type,
        "body": body or None,
    }
    if extra:
        document.update(extra)
    return document


class NewsFeed:
    """One request's view of a news feed; `notes` lists what the result must disclose.

    A subclass sets `source_id`, `parser_version`, `missing_code` and `display_name` and
    implements `_fetch`, which stores every article it receives with `_store`.
    """

    source_id = ""
    parser_version = ""
    display_name = ""
    missing_code = ""

    def __init__(
        self, cache: PublicSourceCache, client: httpx.Client, limiter: RateLimiter
    ) -> None:
        self._cache = cache
        self._client = client
        self._limiter = limiter
        self.notes: list[str] = []
        self.requests = 0
        self.truncated = False

    def now(self) -> datetime:
        return self._cache.now()

    def configured(self) -> bool:
        raise NotImplementedError

    def ticker(self, symbol: str) -> str:
        """Share classes are written with a dot (BRK.B), as listings do."""
        return symbol.upper()

    def articles(self, symbol: str, start: datetime, cutoff: datetime) -> list[NewsArticle]:
        """Articles on `symbol` created from `start` on, as cached after a refresh.

        All revisions are kept; each article is returned at the revision this system had
        observed by `cutoff` when there is one, otherwise its latest. Articles created
        after `cutoff` are returned too: the caller decides what counts as known.
        """
        if cutoff - start > MAX_LOOKBACK:
            raise ValueError("the news window is limited to 30 days")
        ticker = self.ticker(symbol)
        key = f"{ticker}:{utc_text(start)}"
        last = self._cache.last_fetch(self.source_id, key)
        if needs_fetch(last, cutoff, self.now()):
            try:
                self._fetch(ticker, start, key)
            except SourceConfigError:
                if last is None and not self._cached(ticker):
                    raise
                self.notes.append(
                    f"Cached {self.display_name} news was used: no key is registered."
                )
            except SourceError as exc:
                if last is None and not self._cached(ticker):
                    raise
                since = f" from {utc_text(last)}" if last else ""
                self.notes.append(f"Cached {self.display_name} news{since} was used: {exc}")
        records = self._cache.records(self.source_id, f"{ticker}:", as_of=cutoff)
        found = [article_from(record) for record in records]
        return sorted(
            (a for a in found if a.published_at >= start),
            key=lambda a: (a.published_at, a.article_id),
        )

    def last_download(self, symbol: str, start: datetime) -> datetime | None:
        return self._cache.last_fetch(self.source_id, f"{self.ticker(symbol)}:{utc_text(start)}")

    def _cached(self, ticker: str) -> bool:
        return bool(self._cache.records(self.source_id, f"{ticker}:"))

    def _missing(self) -> SourceConfigError:
        return SourceConfigError(
            self.missing_code,
            f"Register a {self.display_name} key on the Keys screen to read its news",
        )

    def _fetch(self, ticker: str, start: datetime, key: str) -> None:
        raise NotImplementedError

    def _store(self, ticker: str, documents: list[dict[str, Any]]) -> None:
        with self._cache.batch():
            for document in documents:
                self._cache.put(
                    self.source_id,
                    f"{ticker}:{document['id']}",
                    document,
                    document["created_at"],
                    self.parser_version,
                )

    def _get(self, url: str, params: dict[str, str | int], headers: dict[str, str]) -> bytes:
        self._limiter.wait()
        self.requests += 1
        try:
            response = self._client.get(
                url, params=params, headers=headers, timeout=TIMEOUT_SECONDS
            )
        except httpx.HTTPError as exc:
            raise SourceError(
                f"{self.display_name} could not be reached: {type(exc).__name__}"
            ) from exc
        if response.status_code in (401, 403):
            raise SourceError(
                f"{self.display_name} refused the key (HTTP {response.status_code}); "
                "the key's plan may not include this news"
            )
        if response.status_code == 429:
            raise SourceError(f"{self.display_name} rate limit reached (HTTP 429)")
        if response.status_code != 200:
            raise SourceError(f"{self.display_name} answered HTTP {response.status_code}")
        return response.content
