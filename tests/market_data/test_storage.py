from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from services.market_data.fake import DEFAULT_FIXTURE_PATH, FakeMarketDataProvider
from services.market_data.provider import BarsQuery, BarsResult
from services.market_data.storage import (
    MIGRATIONS_DIR,
    PARQUET_SCHEMA,
    AmbiguousRevisionError,
    CachedMarketDataProvider,
    CacheIntegrityError,
    CacheWriteError,
    LocalMarketDataStore,
    MigrationRunner,
    RevisionPinnedError,
    StorageError,
)

INSTRUMENT_ID = "us:aapl:xnas:2026"
SOURCE_ID = "fake-market-data"
REVISION = "fake-aapl-20261009-r1"
START = datetime(2026, 10, 9, 13, 30, tzinfo=timezone.utc)
END = datetime(2026, 10, 9, 13, 34, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def query() -> BarsQuery:
    return BarsQuery(INSTRUMENT_ID, "1m", START, END)


def fixture_document() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads(DEFAULT_FIXTURE_PATH.read_text(encoding="utf-8")),
    )


def fake_provider(document: dict[str, Any] | None = None) -> FakeMarketDataProvider:
    return FakeMarketDataProvider(document or fixture_document(), now=lambda: NOW)


def fake_result(document: dict[str, Any] | None = None) -> BarsResult:
    return fake_provider(document).get_bars(query())


def store(root: Path, **options: object) -> LocalMarketDataStore:
    return LocalMarketDataStore(root, now=lambda: NOW, **cast(dict[str, Any], options))


def scalar(database: Path, statement: str) -> object:
    with sqlite3.connect(database) as connection:
        row = connection.execute(statement).fetchone()
    assert row is not None
    return row[0]


def test_migration_is_versioned_and_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "metadata.sqlite3"
    runner = MigrationRunner(database)

    assert runner.apply() == ("0001", "0002")
    assert runner.apply() == ()
    assert scalar(database, "SELECT COUNT(*) FROM schema_migrations") == 2
    tables = {
        row[0]
        for row in sqlite3.connect(database).execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {
        "coverage_manifests",
        "dataset_revisions",
        "acquisition_jobs",
        "revision_pins",
        "write_journal",
    } <= tables


def test_existing_0001_database_upgrades_and_recovers_legacy_running_job(
    tmp_path: Path,
) -> None:
    legacy_migrations = tmp_path / "legacy_migrations"
    legacy_migrations.mkdir()
    (legacy_migrations / "0001_local_market_data.sql").write_text(
        (MIGRATIONS_DIR / "0001_local_market_data.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    root = tmp_path / "data"
    assert MigrationRunner(root / "metadata.sqlite3", legacy_migrations).apply() == ("0001",)
    with sqlite3.connect(root / "metadata.sqlite3") as connection:
        connection.execute(
            """
            INSERT INTO acquisition_jobs (
                job_key, request_json, source_id, status, created_at_utc, updated_at_utc
            ) VALUES ('legacy', '{}', ?, 'running', 'then', 'then')
            """,
            (SOURCE_ID,),
        )

    with store(root) as upgraded:
        assert upgraded.job_state("legacy")["status"] == "interrupted"
        assert scalar(upgraded.database_path, "SELECT COUNT(*) FROM schema_migrations") == 2


def test_parquet_write_is_idempotent_and_survives_restart(tmp_path: Path) -> None:
    first_store = store(tmp_path)
    result = fake_result()

    first = first_store.write_result(result)
    second = first_store.write_result(result)

    assert len(first.created_paths) == 1
    assert second.created_paths == ()
    assert first.parquet_paths == second.parquet_paths
    assert len(list(tmp_path.rglob("*.parquet"))) == 1
    assert scalar(first_store.database_path, "SELECT COUNT(*) FROM coverage_manifests") == 1
    assert scalar(first_store.database_path, "SELECT COUNT(*) FROM write_journal") == 1

    restarted = store(tmp_path)
    cached = restarted.read_exact(query(), SOURCE_ID, REVISION)
    assert cached is not None
    assert cached.to_document() == result.to_document()


def test_partial_coverage_gap_is_preserved_without_filling(tmp_path: Path) -> None:
    document = fixture_document()
    del cast(list[object], document["bars"])[2]
    result = fake_result(document)
    market_store = store(tmp_path)

    market_store.write_result(result)
    gaps = market_store.detect_gaps(query(), SOURCE_ID, REVISION)

    assert len(gaps) == 1
    assert gaps[0].start_utc == "2026-10-09T13:32:00Z"
    assert gaps[0].reason == "fixture_missing"
    cached = market_store.read_exact(query(), SOURCE_ID, REVISION)
    assert cached is not None
    assert len(cached.bars) == 3


def test_cache_read_rejects_parquet_content_hash_mismatch(tmp_path: Path) -> None:
    market_store = store(tmp_path)
    receipt = market_store.write_result(fake_result())
    parquet_path = tmp_path / receipt.parquet_paths[0]
    bars = cast(list[dict[str, Any]], pq.ParquetFile(parquet_path).read().to_pylist())
    bars[0]["close"] = "250.30"
    pq.write_table(pa.Table.from_pylist(bars, schema=PARQUET_SCHEMA), parquet_path)

    with pytest.raises(CacheIntegrityError, match="checksum mismatch"):
        market_store.read_exact(query(), SOURCE_ID, REVISION)


def test_missing_cache_reports_requested_range_as_gap(tmp_path: Path) -> None:
    gaps = store(tmp_path).detect_gaps(query(), SOURCE_ID, REVISION)

    assert gaps == (
        gaps[0].__class__(
            "2026-10-09T13:30:00Z",
            "2026-10-09T13:34:00Z",
            "not_cached",
        ),
    )


def test_revision_pin_persists_and_blocks_eviction_guard(tmp_path: Path) -> None:
    market_store = store(tmp_path)
    market_store.write_result(fake_result())
    pin = {
        "source_id": SOURCE_ID,
        "instrument_id": INSTRUMENT_ID,
        "timeframe": "1m",
        "adjustment": "raw",
        "dataset_revision": REVISION,
    }

    market_store.pin_revision("analysis:alpha", reason="analysis evidence", **pin)
    market_store.pin_revision("analysis:alpha", reason="analysis evidence", **pin)

    assert market_store.is_revision_pinned(**pin)
    assert store(tmp_path).is_revision_pinned(**pin)
    with pytest.raises(RevisionPinnedError):
        market_store.ensure_revision_unpinned(**pin)
    with pytest.raises(StorageError, match="pin_id already identifies"):
        market_store.pin_revision(
            "analysis:alpha",
            reason="different purpose",
            **{**pin, "dataset_revision": "another-revision"},
        )
    assert scalar(market_store.database_path, "SELECT COUNT(*) FROM revision_pins") == 1


def test_running_job_becomes_interrupted_after_restart_with_checkpoint(tmp_path: Path) -> None:
    first_store = store(tmp_path)
    job_key = first_store.enqueue_job(query(), SOURCE_ID)
    assert first_store.enqueue_job(query(), SOURCE_ID) == job_key
    first_store.start_job(job_key)
    first_store.checkpoint_job(job_key, {"next_start_utc": "2026-10-09T13:32:00Z"})
    first_store.close()  # the owning process exits

    restarted = store(tmp_path)
    state = restarted.job_state(job_key)

    assert state["status"] == "interrupted"
    assert state["attempt_count"] == 1
    assert state["error_code"] == "process_restarted"
    assert state["checkpoint"] == {"next_start_utc": "2026-10-09T13:32:00Z"}
    assert scalar(restarted.database_path, "SELECT COUNT(*) FROM acquisition_jobs") == 1


def test_disk_replace_failure_leaves_no_visible_cache_or_temp_file(tmp_path: Path) -> None:
    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("simulated disk failure")

    market_store = store(tmp_path, replace_file=fail_replace)

    with pytest.raises(CacheWriteError, match="OSError"):
        market_store.write_result(fake_result())

    assert market_store.read_exact(query(), SOURCE_ID, REVISION) is None
    assert list(tmp_path.rglob("*.parquet")) == []
    assert list(tmp_path.rglob("*.tmp")) == []
    assert (
        scalar(
            market_store.database_path,
            "SELECT COUNT(*) FROM write_journal WHERE state = 'failed'",
        )
        == 1
    )


def test_renamed_write_journal_recovers_metadata_after_restart(tmp_path: Path) -> None:
    first_store = store(tmp_path)
    receipt = first_store.write_result(fake_result())
    with sqlite3.connect(first_store.database_path) as connection:
        connection.execute(
            "DELETE FROM coverage_manifests WHERE coverage_key = ?", (receipt.coverage_key,)
        )
        connection.execute(
            "UPDATE write_journal SET state = 'renamed' WHERE coverage_key = ?",
            (receipt.coverage_key,),
        )

    assert first_store.read_exact(query(), SOURCE_ID, REVISION) is None
    first_store.close()  # the owning process exits
    restarted = store(tmp_path)

    recovered = restarted.read_exact(query(), SOURCE_ID, REVISION)
    assert recovered is not None
    assert len(recovered.bars) == 4
    assert (
        scalar(
            restarted.database_path,
            "SELECT COUNT(*) FROM write_journal WHERE state = 'committed'",
        )
        == 1
    )


def test_cache_first_wrapper_avoids_upstream_after_restart(tmp_path: Path) -> None:
    first = CachedMarketDataProvider(fake_provider(), store(tmp_path))
    expected = first.get_bars(query()).to_document()

    class FailingUpstream:
        source_id = SOURCE_ID

        def capabilities(self) -> dict[str, Any]:
            return deepcopy(fake_provider().capabilities())

        def get_bars(self, _query: BarsQuery) -> BarsResult:
            raise AssertionError("cache hit must not call upstream")

    restarted = CachedMarketDataProvider(FailingUpstream(), store(tmp_path))

    assert restarted.get_bars(query()).to_document() == expected


def test_cache_wrapper_defers_to_upstream_when_several_revisions_are_cached(
    tmp_path: Path,
) -> None:
    revised = fixture_document()
    for bar in revised["bars"]:
        bar["dataset_revision"] = "fake-aapl-20261009-r2"
    local_store = store(tmp_path)
    local_store.write_result(fake_result())
    local_store.write_result(fake_result(revised))
    with pytest.raises(AmbiguousRevisionError):
        local_store.read_exact(query(), SOURCE_ID)

    result = CachedMarketDataProvider(fake_provider(revised), local_store).get_bars(query())

    assert result.coverage["dataset_revision"] == "fake-aapl-20261009-r2"
    assert {bar["dataset_revision"] for bar in result.bars} == {"fake-aapl-20261009-r2"}


def _insert_pending_journal(database: Path, journal_id: str, owner_id: str | None) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO write_journal (
                journal_id, coverage_key, manifest_json, files_json, state, owner_id,
                created_at_utc, updated_at_utc
            ) VALUES (?, 'coverage', '{}', '[]', 'pending', ?, 'now', 'now')
            """,
            (journal_id, owner_id),
        )


def _journal_state(database: Path, journal_id: str) -> object:
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT state FROM write_journal WHERE journal_id = ?", (journal_id,)
        ).fetchone()
    assert row is not None
    return row[0]


def test_opening_second_store_does_not_recover_live_owner_work(tmp_path: Path) -> None:
    live = store(tmp_path)
    job_key = live.enqueue_job(query(), SOURCE_ID)
    live.start_job(job_key)
    _insert_pending_journal(live.database_path, "live-write", live.owner_id)
    _insert_pending_journal(live.database_path, "legacy-write", None)

    other = store(tmp_path)

    assert other.job_state(job_key)["status"] == "running"
    assert _journal_state(other.database_path, "live-write") == "pending"
    assert _journal_state(other.database_path, "legacy-write") == "failed"

    live.close()

    assert other.recover_interrupted_jobs() == 1
    assert other.job_state(job_key)["status"] == "interrupted"
    other.recover_interrupted_writes()
    assert _journal_state(other.database_path, "live-write") == "failed"


def test_closed_store_rejects_new_work_and_stale_owner_locks_are_removed(
    tmp_path: Path,
) -> None:
    first = store(tmp_path)
    lock_path = tmp_path / "owners" / f"{first.owner_id}.lock"
    assert lock_path.is_file()
    first.close()

    with pytest.raises(StorageError, match="closed"):
        first.write_result(fake_result())
    with pytest.raises(StorageError, match="closed"):
        first.start_job(first.enqueue_job(query(), SOURCE_ID))

    with store(tmp_path) as second:
        assert not lock_path.exists()
        assert (tmp_path / "owners" / f"{second.owner_id}.lock").is_file()


_CHILD_STORE = """
import sys
from datetime import datetime, timezone
from pathlib import Path
from services.market_data.provider import BarsQuery
from services.market_data.storage import LocalMarketDataStore

store = LocalMarketDataStore(Path(sys.argv[1]))
start = datetime(2026, 10, 9, 13, 30, tzinfo=timezone.utc)
end = datetime(2026, 10, 9, 13, 34, tzinfo=timezone.utc)
job_key = store.enqueue_job(BarsQuery("us:aapl:xnas:2026", "1m", start, end), "fake-market-data")
store.start_job(job_key)
print(job_key, flush=True)
sys.stdin.read()
"""


def test_running_job_of_live_process_survives_until_that_process_dies(tmp_path: Path) -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD_STORE, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    try:
        assert child.stdout is not None
        job_key = child.stdout.readline().strip()
        assert job_key

        with store(tmp_path) as while_alive:
            assert while_alive.job_state(job_key)["status"] == "running"
    finally:
        child.kill()
        child.wait(timeout=10)

    with store(tmp_path) as after_crash:
        state = after_crash.job_state(job_key)
    assert state["status"] == "interrupted"
    assert state["error_code"] == "process_restarted"


def test_cache_wrapper_refetches_complete_coverage_with_provisional_bars(
    tmp_path: Path,
) -> None:
    document = fixture_document()
    document["bars"][-1].update(is_final=False, quality="provisional")
    calls: list[BarsQuery] = []
    upstream = fake_provider(document)

    class CountingUpstream:
        source_id = SOURCE_ID

        def capabilities(self) -> dict[str, Any]:
            return upstream.capabilities()

        def get_bars(self, requested: BarsQuery) -> BarsResult:
            calls.append(requested)
            return upstream.get_bars(requested)

    cached = CachedMarketDataProvider(CountingUpstream(), store(tmp_path))
    first = cached.get_bars(query())
    second = cached.get_bars(query())

    assert first.coverage["coverage_status"] == "complete"
    assert second.to_document() == first.to_document()
    assert len(calls) == 2
