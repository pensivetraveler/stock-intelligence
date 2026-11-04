"""Kiwoom US orders on a real account (ADR 0047), through `LiveOnlyClient` only.

The official spec gives the live and paper domains the same api-ids and bodies, so the
paper adapter's request and reply handling is reused; only the client, the key check and
the account reference differ. Not yet tried against the live server: the first order
the user confirms is the first live request (ADR 0047, L3).
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone

import httpx

from services.live_trading.client import LiveOnlyClient, require_live_environment
from services.market_data.broker_common import TokenCache
from services.paper_trading.brokers.kiwoom_paper import ALLOWED, KiwoomPaperBroker


def live_account_ref(app_key: str) -> str:
    return f"kiwoom-{hashlib.sha256(app_key.encode()).hexdigest()[:16]}-real"


class KiwoomLiveBroker(KiwoomPaperBroker):
    def __init__(
        self,
        app_key: str,
        secret_key: str,
        environment: str | None,
        *,
        markets: frozenset[str] = frozenset(),
        transport: httpx.BaseTransport | None = None,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        # Not the paper constructor: that one refuses every key that is not a demo key.
        require_live_environment(environment)
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_call: dict[str, float] = {}
        self._pace_lock = threading.Lock()
        self._app_key = app_key
        self._secret_key = secret_key
        self._markets = markets
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._http = LiveOnlyClient(ALLOWED, transport=transport)  # type: ignore[assignment]
        self._token = TokenCache(self._issue_token)
        self.account_ref = live_account_ref(app_key)

    def __repr__(self) -> str:
        return f"KiwoomLiveBroker(account_ref={self.account_ref!r})"
