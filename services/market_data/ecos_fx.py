"""USD/KRW reference rate from the Bank of Korea ECOS Open API (ADR 0037).

Series 731Y001 (주요국 통화의 대원화환율), item 0000001 (원/미국달러 매매기준율), daily.
Request: GET https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/{n}/731Y001/D/
{start YYYYMMDD}/{end YYYYMMDD}/0000001. Shapes below follow the ECOS developer guide and were
confirmed with one real call on 2026-10-23 (1325.5 for 2026-10-22). Error codes other than
INFO-200 were not seen live.

- The key sits in the URL path, so no URL is ever logged or put in an error message, and
  httpx's own request log is turned down for this module's client.
- Point in time: the rate of day D is published during D (Seoul), at a time ECOS does not
  state, so the latest observation strictly before the request's Seoul date is used.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import httpx

SEOUL = ZoneInfo("Asia/Seoul")
HOST = "https://ecos.bok.or.kr"
SERIES = "731Y001"
ITEM_USD = "0000001"
LOOKBACK_DAYS = 14  # weekends and Korean holidays have no observation
NO_DATA = "INFO-200"

logging.getLogger("httpx").setLevel(logging.WARNING)


class EcosError(RuntimeError):
    """ECOS could not give a rate. Messages never contain the key or the URL."""


@dataclass(frozen=True)
class EcosRate:
    krw_per_usd: Decimal
    observation_date: date  # the Seoul date the rate is for
    fetched_at_utc: datetime

    @property
    def source(self) -> str:
        return (
            f"BOK ECOS {SERIES}/{ITEM_USD} (원/미국달러 매매기준율) "
            f"for {self.observation_date.isoformat()}"
        )


class EcosUsdKrw:
    """Latest published USD/KRW rate before today (Seoul), cached per Seoul date."""

    def __init__(
        self,
        api_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not api_key.strip() or "/" in api_key:
            raise EcosError("the ECOS key is empty or malformed")
        self._key = api_key.strip()
        self._client = httpx.Client(
            base_url=HOST,
            timeout=httpx.Timeout(10.0, connect=5.0),
            transport=transport,
            follow_redirects=False,
        )
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._cache: tuple[date, EcosRate] | None = None
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return "EcosUsdKrw()"

    def rate(self) -> EcosRate:
        now = self._now()
        today = now.astimezone(SEOUL).date()
        with self._lock:
            if self._cache is not None and self._cache[0] == today:
                return self._cache[1]
            found = self._fetch(today, now)
            self._cache = (today, found)
            return found

    def _fetch(self, today: date, now: datetime) -> EcosRate:
        end = today - timedelta(days=1)
        start = end - timedelta(days=LOOKBACK_DAYS)
        path = (
            f"/api/StatisticSearch/{self._key}/json/kr/1/{LOOKBACK_DAYS + 1}/{SERIES}/D/"
            f"{start:%Y%m%d}/{end:%Y%m%d}/{ITEM_USD}"
        )
        try:
            response = self._client.get(path)
        except httpx.HTTPError as exc:
            raise EcosError(f"ECOS did not answer ({type(exc).__name__})") from None
        if response.status_code >= 400:
            raise EcosError(f"ECOS answered HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            raise EcosError("ECOS answered with something that is not JSON") from None
        result = payload.get("RESULT")
        if isinstance(result, dict):
            code = str(result.get("CODE", ""))
            if code == NO_DATA:
                raise EcosError(f"ECOS has no USD/KRW rate from {start} to {end}")
            raise EcosError(f"ECOS refused the request ({code})")
        rows = (payload.get("StatisticSearch") or {}).get("row") or []
        best: tuple[date, Decimal] | None = None
        for row in rows:
            if not isinstance(row, dict) or row.get("ITEM_CODE1", ITEM_USD) != ITEM_USD:
                continue
            try:
                day = datetime.strptime(str(row["TIME"]), "%Y%m%d").date()
                value = Decimal(str(row["DATA_VALUE"]).replace(",", ""))
            except (KeyError, ValueError, InvalidOperation):
                continue
            if day < today and value > 0 and (best is None or day > best[0]):
                best = (day, value)
        if best is None:
            raise EcosError(f"ECOS returned no usable USD/KRW rate from {start} to {end}")
        return EcosRate(best[1], best[0], now)
