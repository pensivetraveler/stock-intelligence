"""Builds per-request SEC and FINRA clients over one shared cache, pacing and HTTP client."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import httpx

from services.public_sources import finra, sec
from services.public_sources.cache import PublicSourceCache
from services.public_sources.finra import Finra, FinraCredential, TokenHolder
from services.public_sources.http import RateLimiter
from services.public_sources.sec import SecEdgar


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
    ) -> None:
        self.cache = PublicSourceCache(path, clock)
        self._user_agent = user_agent
        self._finra_credential = finra_credential
        self._client = client or httpx.Client(follow_redirects=False)
        self._sec_limiter = sec_limiter or RateLimiter(sec.REQUEST_INTERVAL)
        self._finra_limiter = finra_limiter or RateLimiter(finra.REQUEST_INTERVAL)
        self._tokens = TokenHolder()

    def sec(self) -> SecEdgar:
        return SecEdgar(self.cache, self._user_agent(), self._client, self._sec_limiter)

    def finra(self) -> Finra:
        return Finra(
            self.cache,
            self._finra_credential(),
            self._client,
            self._finra_limiter,
            self._tokens,
        )
