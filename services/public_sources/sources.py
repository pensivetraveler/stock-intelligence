"""Per-request SEC, FINRA and Alpaca News clients over one shared cache and HTTP client."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import httpx

from services.public_sources import alpaca_news, finnhub_news, finra, marketnews, sec
from services.public_sources.alpaca_news import AlpacaNews, AlpacaNewsCredential
from services.public_sources.cache import PublicSourceCache
from services.public_sources.finnhub_news import FinnhubCredential, FinnhubNews
from services.public_sources.finra import Finra, FinraCredential, TokenHolder
from services.public_sources.http import RateLimiter
from services.public_sources.marketnews import MarketNews, MarketNewsCredential
from services.public_sources.news_common import NewsFeed
from services.public_sources.sec import SecEdgar
from services.public_sources.shared_sec import shared_sec_files


class PublicSources:
    def __init__(
        self,
        path: Path,
        clock: Callable[[], datetime],
        user_agent: Callable[[], str | None],
        finra_credential: Callable[[], FinraCredential | None],
        client: httpx.Client | None = None,
        sec_limiter: RateLimiter | None = None,
        finra_limiter: RateLimiter | None = None,
        alpaca_credential: Callable[[], AlpacaNewsCredential | None] = lambda: None,
        news_limiter: RateLimiter | None = None,
        finnhub_credential: Callable[[], FinnhubCredential | None] = lambda: None,
        marketnews_credential: Callable[[], MarketNewsCredential | None] = lambda: None,
        finnhub_limiter: RateLimiter | None = None,
        marketnews_limiter: RateLimiter | None = None,
    ) -> None:
        self.cache = PublicSourceCache(path, clock)
        self._user_agent = user_agent
        self._finra_credential = finra_credential
        self._client = client or httpx.Client(follow_redirects=False)
        self._sec_limiter = sec_limiter or RateLimiter(sec.REQUEST_INTERVAL)
        self._finra_limiter = finra_limiter or RateLimiter(finra.REQUEST_INTERVAL)
        self._tokens = TokenHolder()
        self._alpaca_credential = alpaca_credential
        self._news_limiter = news_limiter or RateLimiter(alpaca_news.REQUEST_INTERVAL)
        self._finnhub_credential = finnhub_credential
        self._marketnews_credential = marketnews_credential
        self._finnhub_limiter = finnhub_limiter or RateLimiter(finnhub_news.REQUEST_INTERVAL)
        self._marketnews_limiter = marketnews_limiter or RateLimiter(marketnews.REQUEST_INTERVAL)

    def sec(self) -> SecEdgar:
        return SecEdgar(
            self.cache, self._user_agent(), self._client, self._sec_limiter, shared_sec_files()
        )

    def finra(self) -> Finra:
        return Finra(
            self.cache,
            self._finra_credential(),
            self._client,
            self._finra_limiter,
            self._tokens,
        )

    def alpaca_news(self) -> AlpacaNews:
        return AlpacaNews(self.cache, self._alpaca_credential(), self._client, self._news_limiter)

    def finnhub_news(self) -> FinnhubNews:
        return FinnhubNews(
            self.cache, self._finnhub_credential(), self._client, self._finnhub_limiter
        )

    def marketnews(self) -> MarketNews:
        return MarketNews(
            self.cache, self._marketnews_credential(), self._client, self._marketnews_limiter
        )

    def news_feeds(self) -> list[NewsFeed]:
        """Every news feed, in a fixed order; a feed without a key is still listed."""
        return [self.alpaca_news(), self.marketnews(), self.finnhub_news()]
