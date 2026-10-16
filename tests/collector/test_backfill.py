from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from services.collector.backfill import BackfillRequest, ThrottledProvider, run_backfill
from services.collector.cli import main
from services.market_data.fake import FakeMarketDataProvider
from services.market_data.provider import BarsQuery, BarsResult, InstrumentNotFoundError
from services.market_data.storage import LocalMarketDataStore
from tests.conftest import SESSION_OPEN

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)
INSTRUMENT_ID = "us:aapl:xnas:2026"


class Upstream:
    """Fake provider that can fail its first calls or run a hook on each call."""

    def __init__(self, document: dict[str, Any], *, failures: int = 0) -> None:
        self.provider = FakeMarketDataProvider(document, now=lambda: NOW)
        self.failures = failures
        self.calls: list[BarsQuery] = []
        self.on_call: Any = None

    @property
    def source_id(self) -> str:
        return self.provider.source_id

    def capabilities(self) -> dict[str, Any]:
        return self.provider.capabilities()

    def get_bars(self, query: BarsQuery) -> BarsResult:
        self.calls.append(query)
        if self.on_call is not None:
            self.on_call()
        if self.failures:
            self.failures -= 1
            raise TimeoutError("simulated upstream timeout")
        return self.provider.get_bars(query)


def request(minutes: int = 150, **changes: Any) -> BackfillRequest:
    values: dict[str, Any] = {
        "instrument_id": INSTRUMENT_ID,
        "start_utc": SESSION_OPEN,
        "end_utc": SESSION_OPEN + timedelta(minutes=minutes),
        "chunk_minutes": 60,
    }
    values.update(changes)
    return BackfillRequest(**values)


def test_backfill_fills_chunks_and_a_rerun_is_a_no_op(
    tmp_path: Path, synthetic_document: Any
) -> None:
    upstream = Upstream(synthetic_document(150))
    with LocalMarketDataStore(tmp_path) as store:
        first = run_backfill(store, upstream, request())
        second = run_backfill(store, upstream, request())
        state = store.job_state(first["job_key"])

    assert first["status"] == "succeeded"
    assert (first["chunks"], first["fetched_chunks"], first["complete_chunks"]) == (3, 3, 3)
    assert [query.end_utc - query.start_utc for query in upstream.calls] == [
        timedelta(minutes=60),
        timedelta(minutes=60),
        timedelta(minutes=30),
    ]
    assert second == {
        "job_key": first["job_key"],
        "service": "collector",
        "status": "already_succeeded",
    }
    assert len(upstream.calls) == 3
    assert state["status"] == "succeeded"
    assert state["checkpoint"] == {"next_start_utc": "2026-10-09T16:00:00Z"}


def test_stopped_backfill_resumes_from_checkpoint_after_restart(
    tmp_path: Path, synthetic_document: Any
) -> None:
    stop = threading.Event()
    upstream = Upstream(synthetic_document(150))
    upstream.on_call = stop.set
    with LocalMarketDataStore(tmp_path) as store:
        stopped = run_backfill(store, upstream, request(), stop=stop)
    assert stopped["status"] == "stopped"
    assert stopped["next_start_utc"] == "2026-10-09T14:30:00Z"

    upstream.on_call = None
    with LocalMarketDataStore(tmp_path) as restarted:
        assert restarted.job_state(stopped["job_key"])["status"] == "interrupted"
        resumed = run_backfill(restarted, upstream, request())

    assert resumed["status"] == "succeeded"
    assert resumed["chunks"] == 2
    assert [query.start_utc for query in upstream.calls[1:]] == [
        SESSION_OPEN + timedelta(minutes=60),
        SESSION_OPEN + timedelta(minutes=120),
    ]


def test_transient_errors_are_retried_with_backoff(tmp_path: Path, synthetic_document: Any) -> None:
    sleeps: list[float] = []
    upstream = Upstream(synthetic_document(60), failures=2)
    with LocalMarketDataStore(tmp_path) as store:
        result = run_backfill(store, upstream, request(60), sleep=sleeps.append)

    assert result["status"] == "succeeded"
    assert sleeps == [1.0, 2.0]


def test_exhausted_retries_fail_the_job_and_a_rerun_retries_it(
    tmp_path: Path, synthetic_document: Any
) -> None:
    upstream = Upstream(synthetic_document(60), failures=3)
    with LocalMarketDataStore(tmp_path) as store:
        failed = run_backfill(store, upstream, request(60), sleep=lambda _seconds: None)
        state = store.job_state(failed["job_key"])
        retried = run_backfill(store, upstream, request(60), sleep=lambda _seconds: None)

    assert failed["status"] == "failed"
    assert (state["status"], state["error_code"]) == ("failed", "upstream_error")
    assert retried["status"] == "succeeded"
    assert len(upstream.calls) == 4


def test_unknown_instrument_fails_without_retry(tmp_path: Path, synthetic_document: Any) -> None:
    upstream = Upstream(synthetic_document(60))
    bad = request(60, instrument_id="us:missing:xnas:2026")
    with LocalMarketDataStore(tmp_path) as store:
        with pytest.raises(InstrumentNotFoundError):
            run_backfill(store, upstream, bad, sleep=lambda _seconds: None)
        job_key = store.enqueue_job(
            BarsQuery(bad.instrument_id, "1m", bad.start_utc, bad.end_utc), upstream.source_id
        )
        state = store.job_state(job_key)

    assert (state["status"], state["error_code"]) == ("failed", "instrument_not_found")
    assert len(upstream.calls) == 1


def test_throttle_spaces_upstream_calls() -> None:
    now = [0.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    class Instant:
        source_id = "instant"

        def capabilities(self) -> dict[str, Any]:
            return {}

        def get_bars(self, query: BarsQuery) -> BarsResult:
            return BarsResult((), {})

    throttled = ThrottledProvider(Instant(), 2.0, clock=lambda: now[0], sleep=sleep)
    query = BarsQuery(INSTRUMENT_ID, "1m", SESSION_OPEN, SESSION_OPEN + timedelta(minutes=1))
    throttled.get_bars(query)
    now[0] += 0.5
    throttled.get_bars(query)  # 0.5 s after the first call: waits the remaining 1.5 s
    now[0] += 3.0
    throttled.get_bars(query)  # already 3 s later: no wait

    assert sleeps == [1.5]
    assert throttled.calls == 3


def test_cli_backfill_uses_synthetic_provider_and_data_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SIW_DATA_DIR", raising=False)
    arguments = [
        "backfill",
        "--instrument-id",
        INSTRUMENT_ID,
        "--start-utc",
        "2026-10-09T13:30:00Z",
        "--end-utc",
        "2026-10-09T13:40:00Z",
    ]

    assert main(arguments) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "invalid_config"

    assert main([*arguments, "--data-dir", str(tmp_path)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert (summary["status"], summary["chunks"], summary["external_calls"]) == (
        "succeeded",
        1,
        False,
    )
    assert summary["missing_intervals"] == 6
    assert (tmp_path / "metadata.sqlite3").is_file()
