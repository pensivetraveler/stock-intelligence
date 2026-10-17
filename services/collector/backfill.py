from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from services.market_data.provider import (
    BarsQuery,
    BarsResult,
    InstrumentNotFoundError,
    InvalidBarsQueryError,
    MarketDataProvider,
    ProviderAuthError,
    ProviderDataError,
    validate_bars_query,
)
from services.market_data.storage import (
    CachedMarketDataProvider,
    LocalMarketDataStore,
    StorageError,
)

MAX_CHUNK_MINUTES = 1_440


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True)
class BackfillRequest:
    instrument_id: str
    start_utc: datetime
    end_utc: datetime
    chunk_minutes: int = 390
    max_attempts: int = 3
    retry_delay_seconds: float = 1.0


class ThrottledProvider:
    """Spaces upstream calls at least `min_interval_seconds` apart and counts them."""

    def __init__(
        self,
        upstream: MarketDataProvider,
        min_interval_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if min_interval_seconds < 0:
            raise ValueError("min_interval_seconds cannot be negative")
        self.upstream = upstream
        self.min_interval_seconds = min_interval_seconds
        self.calls = 0
        self._clock = clock
        self._sleep = sleep
        self._last_call: float | None = None

    @property
    def source_id(self) -> str:
        return self.upstream.source_id

    def capabilities(self) -> dict[str, Any]:
        return self.upstream.capabilities()

    def instruments(self) -> list[dict[str, Any]]:
        return self.upstream.instruments()

    def get_bars(self, query: BarsQuery) -> BarsResult:
        if self._last_call is not None:
            wait = self.min_interval_seconds - (self._clock() - self._last_call)
            if wait > 0:
                self._sleep(wait)
        self._last_call = self._clock()
        self.calls += 1
        return self.upstream.get_bars(query)


def run_backfill(
    store: LocalMarketDataStore,
    upstream: MarketDataProvider,
    request: BackfillRequest,
    *,
    min_interval_seconds: float = 0.0,
    stop: threading.Event | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Fill the local cache for one range as a resumable acquisition job.

    The job is identified by the whole range, so rerunning the same request resumes
    from its checkpoint or reports that it already succeeded. Each chunk goes through
    the cache-first provider: complete cached chunks are reused and partial ones are
    fetched again. Only transient upstream errors are retried; invalid requests,
    unknown instruments and storage errors fail the job at once.
    """
    if not 1 <= request.chunk_minutes <= MAX_CHUNK_MINUTES:
        raise InvalidBarsQueryError(f"chunk_minutes must be between 1 and {MAX_CHUNK_MINUTES}")
    if request.max_attempts < 1:
        raise InvalidBarsQueryError("max_attempts must be at least 1")
    whole = BarsQuery(request.instrument_id, "1m", request.start_utc, request.end_utc)
    validate_bars_query(whole)
    for name, value in (("start_utc", whole.start_utc), ("end_utc", whole.end_utc)):
        if value.second:
            raise InvalidBarsQueryError(f"{name} must align to a minute boundary")

    throttled = ThrottledProvider(upstream, min_interval_seconds, clock=clock, sleep=sleep)
    cached = CachedMarketDataProvider(throttled, store)
    job_key = store.enqueue_job(whole, upstream.source_id)
    summary: dict[str, Any] = {"job_key": job_key, "service": "collector"}
    if store.job_state(job_key)["status"] == "succeeded":
        return {**summary, "status": "already_succeeded"}
    try:
        store.start_job(job_key)
    except StorageError:
        return {**summary, "status": "job_in_progress"}

    checkpoint = store.job_state(job_key)["checkpoint"] or {}
    chunk_start = (
        _parse_utc(checkpoint["next_start_utc"])
        if "next_start_utc" in checkpoint
        else whole.start_utc
    )
    chunks = fetched = complete = 0
    missing_intervals = 0
    while chunk_start < whole.end_utc:
        if stop is not None and stop.is_set():
            # The job stays running; once this store closes, the next store marks it
            # interrupted and a rerun resumes from the last checkpoint.
            return {**summary, "status": "stopped", "next_start_utc": _format_utc(chunk_start)}
        chunk_end = min(chunk_start + timedelta(minutes=request.chunk_minutes), whole.end_utc)
        query = BarsQuery(request.instrument_id, "1m", chunk_start, chunk_end)
        calls_before = throttled.calls
        result = _fetch_with_retry(store, job_key, cached, query, request, sleep)
        if result is None:
            return {**summary, "status": "failed"}
        chunks += 1
        fetched += throttled.calls > calls_before
        complete += (
            result.coverage["coverage_status"] in {"complete", "empty"}
            and not (result.coverage["missing_intervals"])
        )
        missing_intervals += len(result.coverage["missing_intervals"])
        store.checkpoint_job(job_key, {"next_start_utc": _format_utc(chunk_end)})
        chunk_start = chunk_end

    store.complete_job(job_key)
    return {
        **summary,
        "status": "succeeded",
        "chunks": chunks,
        "fetched_chunks": fetched,
        "cached_chunks": chunks - fetched,
        "complete_chunks": complete,
        "missing_intervals": missing_intervals,
    }


def _fetch_with_retry(
    store: LocalMarketDataStore,
    job_key: str,
    cached: CachedMarketDataProvider,
    query: BarsQuery,
    request: BackfillRequest,
    sleep: Callable[[float], None],
) -> BarsResult | None:
    for attempt in range(1, request.max_attempts + 1):
        try:
            return cached.get_bars(query)
        except InvalidBarsQueryError:
            store.fail_job(job_key, "invalid_query")
            raise
        except InstrumentNotFoundError:
            store.fail_job(job_key, "instrument_not_found")
            raise
        except (ProviderAuthError, ProviderDataError) as exc:
            # Retrying cannot fix rejected credentials or contract-violating data.
            store.fail_job(
                job_key,
                "provider_auth" if isinstance(exc, ProviderAuthError) else "provider_data",
            )
            raise
        except StorageError:
            store.fail_job(job_key, "storage_error")
            raise
        except Exception:
            if attempt == request.max_attempts:
                store.fail_job(job_key, "upstream_error")
                return None
            sleep(request.retry_delay_seconds * 2 ** (attempt - 1))
    return None
