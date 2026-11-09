"""Alpaca News feed (ADR 0052 §3): headlines and summaries for US tickers.

`GET https://data.alpaca.markets/v1beta1/news`, with the first enabled Alpaca key on the
Keys screen. Articles come from more than one provider (`source`: Benzinga, fiscal.ai and
others). The request never asks for article bodies (`include_content=false`) and nothing
from a body or image is stored, because the rights to bodies were not checked. Alpaca's
terms allow personal, non-commercial use and forbid republishing or distributing the
content, so the records stay in the local cache and are never exported or committed.

`created_at` is stored as the publication time (Alpaca documents it only as the creation
time); `updated_at` is kept, and a changed article becomes a new revision.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from services.public_sources.cache import PublicSourceCache, parse_utc, utc_text
from services.public_sources.http import RateLimiter, SourceError
from services.public_sources.news_common import NewsFeed, stored_document

NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
ALPACA_NEWS = "alpaca-news"
PARSER_VERSION = "alpaca-news-1"
REQUEST_INTERVAL = 0.1  # 10 per second; the key's limit header said 10,000 per minute
PAGE_LIMIT = 50  # Alpaca's maximum
MAX_PAGES = 10  # 500 articles per ticker and window; more is reported as truncated


@dataclass(frozen=True)
class AlpacaNewsCredential:
    key_id: str
    secret_key: str = field(repr=False)

    def __repr__(self) -> str:  # never print secrets, even by accident
        return "AlpacaNewsCredential(<hidden>)"


def parse_article(raw: dict[str, Any]) -> dict[str, Any]:
    """The stored document of one API article; `content` and `images` are dropped."""
    return stored_document(
        article_id=str(raw["id"]),
        headline=str(raw.get("headline") or ""),
        summary=str(raw.get("summary") or ""),
        author=str(raw.get("author") or ""),
        created_at=parse_utc(str(raw["created_at"])),
        updated_at=parse_utc(str(raw["updated_at"])) if raw.get("updated_at") else None,
        url=raw.get("url") or None,
        source=str(raw.get("source") or ""),
        symbols=[str(s) for s in raw.get("symbols") or ()],
    )


class AlpacaNews(NewsFeed):
    source_id = ALPACA_NEWS
    parser_version = PARSER_VERSION
    display_name = "Alpaca News"
    missing_code = "alpaca_credential_missing"

    def __init__(
        self,
        cache: PublicSourceCache,
        credential: AlpacaNewsCredential | None,
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
        headers = {
            "APCA-API-KEY-ID": credential.key_id,
            "APCA-API-SECRET-KEY": credential.secret_key,
            "Accept": "application/json",
        }
        params: dict[str, str | int] = {
            "symbols": ticker,
            "start": utc_text(start),
            "end": utc_text(self.now()),
            "limit": PAGE_LIMIT,
            "sort": "desc",
            "include_content": "false",
        }
        pages: list[bytes] = []
        token: str | None = None
        for _ in range(MAX_PAGES):
            query = dict(params) if token is None else {**params, "page_token": token}
            body = self._get(NEWS_URL, query, headers)
            pages.append(body)
            try:
                payload = json.loads(body)
                token = payload.get("next_page_token") or None
                documents = [parse_article(raw) for raw in payload["news"]]
            except (ValueError, KeyError, TypeError) as exc:
                raise SourceError("Alpaca News answered with an unexpected body") from exc
            self._store(ticker, documents)
            if token is None:
                break
        else:
            self.truncated = True
            self.notes.append(
                f"Alpaca News had more than {MAX_PAGES * PAGE_LIMIT} articles for {ticker}; "
                "the oldest in the window were not read."
            )
        self._cache.mark_fetched(ALPACA_NEWS, key, b"".join(pages))
