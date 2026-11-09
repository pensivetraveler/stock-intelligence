"""Finnhub company news feed (ADR 0052 §3).

`GET https://finnhub.io/api/v1/company-news?symbol=&from=&to=` with the key in the
`X-Finnhub-Token` header (never in the URL), from the first enabled Finnhub key on the
Keys screen. Finnhub documents the endpoint for North American companies; the free tier
covers one year of news at 60 calls a minute.

- `from` and `to` are dates only, so the request asks for a day either side of the
  window and the cutoff is applied to `datetime` (a UNIX time, UTC) afterwards.
- No paging is documented, so a large window may come back cut (unverified).
- There is no update time: `updated_at` is null and a changed headline or summary simply
  becomes a new revision.
- Kept: headline, summary, publisher (`source`), link, related tickers. The thumbnail is
  not kept.

Finnhub's terms: personal use only, no redistribution of the data or of results derived
from it, and all data must be deleted when the subscription to it ends
(`PublicSourceCache.delete_source("finnhub-news")`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from services.public_sources.cache import PublicSourceCache
from services.public_sources.http import RateLimiter, SourceError
from services.public_sources.news_common import NewsFeed, stored_document

NEWS_URL = "https://finnhub.io/api/v1/company-news"
FINNHUB_NEWS = "finnhub-news"
PARSER_VERSION = "finnhub-news-1"
REQUEST_INTERVAL = 1.0  # the free plan allows 60 calls a minute


@dataclass(frozen=True)
class FinnhubCredential:
    api_key: str = field(repr=False)

    def __repr__(self) -> str:  # never print secrets, even by accident
        return "FinnhubCredential(<hidden>)"


def parse_article(raw: dict[str, Any], ticker: str) -> dict[str, Any]:
    related = [s.strip() for s in str(raw.get("related") or "").split(",") if s.strip()]
    return stored_document(
        article_id=str(raw["id"]),
        headline=str(raw.get("headline") or ""),
        summary=str(raw.get("summary") or ""),
        created_at=datetime.fromtimestamp(int(raw["datetime"]), tz=timezone.utc),
        updated_at=None,
        url=raw.get("url") or None,
        source=str(raw.get("source") or ""),
        symbols=related or [ticker],
        extra={"category": str(raw.get("category") or "")},
    )


class FinnhubNews(NewsFeed):
    source_id = FINNHUB_NEWS
    parser_version = PARSER_VERSION
    display_name = "Finnhub"
    missing_code = "finnhub_credential_missing"

    def __init__(
        self,
        cache: PublicSourceCache,
        credential: FinnhubCredential | None,
        client: httpx.Client,
        limiter: RateLimiter,
    ) -> None:
        super().__init__(cache, client, limiter)
        self._credential = credential

    def configured(self) -> bool:
        return self._credential is not None

    def _fetch(self, ticker: str, start: datetime, key: str) -> None:
        credential = self._credential
        if credential is None:
            raise self._missing()
        now = self.now().astimezone(timezone.utc)
        params: dict[str, str | int] = {
            "symbol": ticker,
            "from": (start.astimezone(timezone.utc) - timedelta(days=1)).date().isoformat(),
            "to": (now + timedelta(days=1)).date().isoformat(),
        }
        body = self._get(NEWS_URL, params, {"X-Finnhub-Token": credential.api_key})
        try:
            payload = json.loads(body)
            if not isinstance(payload, list):
                raise TypeError("not a list")
            documents = [parse_article(raw, ticker) for raw in payload]
        except (ValueError, KeyError, TypeError) as exc:
            raise SourceError("Finnhub answered with an unexpected body") from exc
        self._store(ticker, documents)
        self._cache.mark_fetched(FINNHUB_NEWS, key, body)
