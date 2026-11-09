"""Per-request SEC, FINRA and Alpaca News clients over one shared cache and HTTP client."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import httpx

from services.public_sources import alpaca_news, finra, sec
from services.public_sources.alpaca_news import AlpacaNews, AlpacaNewsCredential
from services.public_sources.cache import PublicSourceCache
from services.public_sources.finra import Finra, FinraCredential, TokenHolder
from services.public_sources.http import RateLimiter
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
