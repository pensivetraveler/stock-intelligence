"""Alpaca News collector (ADR 0052 §3): headlines and summaries for US tickers.

`GET https://data.alpaca.markets/v1beta1/news`, with the first enabled Alpaca key on the
Keys screen. Articles come from more than one feed (`source`: Benzinga, fiscal.ai and
others). Only the headline, summary, author, tickers, times, link and source are kept:
the request never asks for article bodies (`include_content=false`) and nothing from a
body is stored, because the rights to bodies were not checked. Alpaca's terms allow
personal, non-commercial use and forbid republishing or distributing the content, so
the records stay in the local cache and are never exported or committed.

Point in time: `published_at_utc` is Alpaca's `created_at`. When Alpaca changes an
article (a new `updated_at`, headline or summary), the new revision is stored next to
the old one, never over it (ADR 0024 cache).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
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

NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
ALPACA_NEWS = "alpaca-news"
PARSER_VERSION = "alpaca-news-1"
REQUEST_INTERVAL = 0.1  # 10 per second; the key's limit header said 10,000 per minute
PAGE_LIMIT = 50  # Alpaca's maximum
MAX_PAGES = 10  # 500 articles per ticker and window; more is reported as truncated
MAX_LOOKBACK = timedelta(days=30)
#: Fields kept from an article. `content` and `images` are never stored.
KEPT_FIELDS = (
    "id",
    "headline",
    "summary",
    "author",
    "symbols",
    "created_at",
    "updated_at",
    "url",
    "source",
)


@dataclass(frozen=True)
class AlpacaNewsCredential:
    key_id: str
    secret_key: str = field(repr=False)

    def __repr__(self) -> str:  # never print secrets, even by accident
        return "AlpacaNewsCredential(<hidden>)"


@dataclass(frozen=True)
class NewsArticle:
    article_id: str
    headline: str
    summary: str
    author: str
    symbols: tuple[str, ...]
    published_at: datetime
    updated_at: datetime
    url: str | None
    source: str
    record: SourceRecord

    @property
    def first_observed(self) -> datetime:
        return parse_utc(self.record.first_observed_utc)


def _article(record: SourceRecord) -> NewsArticle:
    doc = record.document
    return NewsArticle(
        str(doc["id"]),
        doc.get("headline") or "",
        doc.get("summary") or "",
        doc.get("author") or "",
        tuple(doc.get("symbols") or ()),
        parse_utc(doc["created_at"]),
        parse_utc(doc["updated_at"]),
        doc.get("url") or None,
        doc.get("source") or "",
        record,
    )


def parse_article(raw: dict[str, Any]) -> dict[str, Any]:
    """The stored document of one API article: kept fields only, times as UTC text."""
    document = {name: raw.get(name) for name in KEPT_FIELDS}
    document["id"] = str(raw["id"])
    document["symbols"] = sorted({str(s).upper() for s in raw.get("symbols") or ()})
    for name in ("created_at", "updated_at"):
        document[name] = utc_text(parse_utc(str(raw[name])))
    for name in ("headline", "summary", "author", "source"):
        document[name] = str(document[name] or "")
    return document


def news_ticker(symbol: str) -> str:
    """Alpaca writes share classes with a dot (BRK.B), as listings do."""
    return symbol.upper()


class AlpacaNews:
    """One request's view of Alpaca News; `notes` lists what the result must disclose."""

    def __init__(
        self,
        cache: PublicSourceCache,
        credential: AlpacaNewsCredential | None,
        client: httpx.Client,
        limiter: RateLimiter,
    ) -> None:
        self._cache = cache
        self._credential = credential
        self._client = client
        self._limiter = limiter
        self.notes: list[str] = []
        self.requests = 0
        self.truncated = False

    def now(self) -> datetime:
        return self._cache.now()

    def articles(self, symbol: str, start: datetime, cutoff: datetime) -> list[NewsArticle]:
        """Articles on `symbol` created from `start` on, as cached after a refresh.

        All revisions are kept; each article is returned at the revision this system had
        observed by `cutoff` when there is one, otherwise its latest. Articles created
        after `cutoff` are returned too: the caller decides what counts as known.
        """
        if cutoff - start > MAX_LOOKBACK:
            raise ValueError("the news window is limited to 30 days")
        ticker = news_ticker(symbol)
        key = f"{ticker}:{utc_text(start)}"
        last = self._cache.last_fetch(ALPACA_NEWS, key)
        if needs_fetch(last, cutoff, self.now()):
            try:
                self._fetch(ticker, start, key)
            except SourceConfigError:
                if last is None and not self._cached(ticker):
                    raise
                self.notes.append("Cached news was used: no Alpaca key is registered.")
            except SourceError as exc:
                if last is None and not self._cached(ticker):
                    raise
                since = f" from {utc_text(last)}" if last else ""
                self.notes.append(f"Cached news{since} was used: {exc}")
        records = self._cache.records(ALPACA_NEWS, f"{ticker}:", as_of=cutoff)
        found = [_article(record) for record in records]
        return sorted(
            (a for a in found if a.published_at >= start),
            key=lambda a: (a.published_at, a.article_id),
        )

    def last_download(self, symbol: str, start: datetime) -> datetime | None:
        return self._cache.last_fetch(ALPACA_NEWS, f"{news_ticker(symbol)}:{utc_text(start)}")

    def _cached(self, ticker: str) -> bool:
        return bool(self._cache.records(ALPACA_NEWS, f"{ticker}:"))

    def _headers(self) -> dict[str, str]:
        credential = self._credential
        if credential is None:
            raise SourceConfigError(
                "alpaca_credential_missing",
                "Register an Alpaca key on the Keys screen to read Alpaca News",
            )
        return {
            "APCA-API-KEY-ID": credential.key_id,
            "APCA-API-SECRET-KEY": credential.secret_key,
            "Accept": "application/json",
        }

    def _fetch(self, ticker: str, start: datetime, key: str) -> None:
        headers = self._headers()
        end = self.now()
        params: dict[str, str | int] = {
            "symbols": ticker,
            "start": utc_text(start),
            "end": utc_text(end),
            "limit": PAGE_LIMIT,
            "sort": "desc",
            "include_content": "false",
        }
        pages: list[bytes] = []
        token: str | None = None
        for _ in range(MAX_PAGES):
            query = dict(params) if token is None else {**params, "page_token": token}
            body = self._download(query, headers)
            pages.append(body)
            try:
                payload = json.loads(body)
                articles = payload["news"]
                token = payload.get("next_page_token") or None
            except (ValueError, KeyError, TypeError) as exc:
                raise SourceError("Alpaca News answered with an unexpected body") from exc
            try:
                with self._cache.batch():
                    for raw in articles:
                        document = parse_article(raw)
                        self._cache.put(
                            ALPACA_NEWS,
                            f"{ticker}:{document['id']}",
                            document,
                            document["created_at"],
                            PARSER_VERSION,
                        )
            except (ValueError, KeyError, TypeError) as exc:
                raise SourceError("Alpaca News answered with an unexpected article") from exc
            if token is None:
                break
        else:
            self.truncated = True
            self.notes.append(
                f"Alpaca News had more than {MAX_PAGES * PAGE_LIMIT} articles for {ticker}; "
                "the oldest in the window were not read."
            )
        self._cache.mark_fetched(ALPACA_NEWS, key, b"".join(pages))

    def _download(self, params: dict[str, str | int], headers: dict[str, str]) -> bytes:
        self._limiter.wait()
        self.requests += 1
        try:
            response = self._client.get(
                NEWS_URL, params=params, headers=headers, timeout=TIMEOUT_SECONDS
            )
        except httpx.HTTPError as exc:
            raise SourceError(f"Alpaca News could not be reached: {type(exc).__name__}") from exc
        if response.status_code in (401, 403):
            raise SourceError(
                f"Alpaca News refused the key (HTTP {response.status_code}); "
                "the key's plan may not include news"
            )
        if response.status_code == 429:
            raise SourceError("Alpaca News rate limit reached (HTTP 429)")
        if response.status_code != 200:
            raise SourceError(f"Alpaca News answered HTTP {response.status_code}")
        return response.content
