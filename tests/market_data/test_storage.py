from __future__ import annotations

import json
import sqlite3
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
    PARQUET_SCHEMA,
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

    assert runner.apply() == ("0001",)
    assert runner.apply() == ()
    assert scalar(database, "SELECT COUNT(*) FROM schema_migrations") == 1
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
