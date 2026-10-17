from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote
from uuid import uuid4
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

from packages.contracts.validator import validate_document
from services.market_data.provider import (
    SETTLED_MISSING_REASONS,
    BarsQuery,
    BarsResult,
    MarketDataProvider,
    validate_bars_query,
)

MIGRATIONS_DIR = Path(__file__).with_name("migrations")
OWNER_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
EXCHANGE_TIMEZONE = ZoneInfo("America/New_York")
JOB_ERROR_CODE_PATTERN = re.compile(r"^[a-z0-9_]{1,64}$")
PARQUET_SCHEMA = pa.schema(
    [
        ("kind", pa.string()),
        ("schema_version", pa.string()),
        ("instrument_id", pa.string()),
        ("timeframe", pa.string()),
        ("interval_start_utc", pa.string()),
        ("interval_end_utc", pa.string()),
        ("open", pa.string()),
        ("high", pa.string()),
        ("low", pa.string()),
        ("close", pa.string()),
        ("volume", pa.string()),
        ("volume_unit", pa.string()),
        ("session", pa.string()),
        ("is_final", pa.bool_()),
        ("quality", pa.string()),
        (
            "lineage",
            pa.struct(
                [
                    ("kind", pa.string()),
                    ("source_timeframe", pa.string()),
                    ("expected_source_bar_count", pa.int64()),
                    ("observed_source_bar_count", pa.int64()),
                    ("source_dataset_revision", pa.string()),
                    ("calendar_id", pa.string()),
                    ("calendar_version", pa.string()),
                ]
            ),
        ),
        ("adjustment", pa.string()),
        ("source_id", pa.string()),
        ("source_feed_scope", pa.string()),
        ("dataset_revision", pa.string()),
        ("available_at_utc", pa.string()),
    ]
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical_json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_component(value: str) -> str:
    return quote(value, safe="-_.")


if sys.platform == "win32":
    import msvcrt

    def _lock_descriptor(descriptor: int, *, blocking: bool) -> bool:
        try:
            msvcrt.locking(descriptor, msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
        except OSError:
            if blocking:
                raise
            return False
        return True

    def _unlock_descriptor(descriptor: int) -> None:
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock_descriptor(descriptor: int, *, blocking: bool) -> bool:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            return False
        return True

    def _unlock_descriptor(descriptor: int) -> None:
        fcntl.flock(descriptor, fcntl.LOCK_UN)


class _ExclusiveFileLock:
    """OS advisory lock released by the kernel when its holder exits or crashes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._descriptor: int | None = None

    def acquire(self, *, blocking: bool) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            locked = _lock_descriptor(descriptor, blocking=blocking)
        except BaseException:
            os.close(descriptor)
            raise
        if not locked:
            os.close(descriptor)
            return False
        self._descriptor = descriptor
        return True

    def release(self) -> None:
        descriptor, self._descriptor = self._descriptor, None
        if descriptor is None:
            return
        try:
            _unlock_descriptor(descriptor)
        finally:
            os.close(descriptor)


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _session_date(bar: dict[str, Any]) -> str:
    """US exchange-local date of the bar start.

    Partitioning by UTC date would split a winter post-market session (19:00-20:00 ET
    is 00:00-01:00 UTC the next day) across two partitions.
    """
    start = _parse_utc(cast(str, bar["interval_start_utc"]))
    return start.astimezone(EXCHANGE_TIMEZONE).date().isoformat()


class StorageError(RuntimeError):
    """Base error for local market-data persistence."""


class MigrationError(StorageError):
    """A migration cannot be applied or no longer matches its recorded checksum."""


class CacheWriteError(StorageError):
    """A recoverable atomic cache write failed."""


class CacheIntegrityError(StorageError):
    """Persisted metadata or Parquet content is inconsistent."""


class AmbiguousRevisionError(StorageError):
    """A cache read omitted a revision when more than one eligible record exists."""


class RevisionPinnedError(StorageError):
    """A destructive revision operation was blocked by an evidence pin."""


@dataclass(frozen=True)
class Gap:
    start_utc: str
    end_utc: str
    reason: str


@dataclass(frozen=True)
class WriteReceipt:
    coverage_key: str
    parquet_paths: tuple[str, ...]
    created_paths: tuple[str, ...]


class MigrationRunner:
    def __init__(self, database_path: Path, migrations_dir: Path = MIGRATIONS_DIR) -> None:
        self.database_path = database_path
        self.migrations_dir = migrations_dir

    def apply(self) -> tuple[str, ...]:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        applied_now: list[str] = []
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    applied_at_utc TEXT NOT NULL
                )
                """
            )
            recorded = {
                cast(str, row[0]): cast(str, row[1])
                for row in connection.execute("SELECT version, checksum FROM schema_migrations")
            }
            for path in sorted(self.migrations_dir.glob("[0-9][0-9][0-9][0-9]_*.sql")):
                version = path.name.split("_", maxsplit=1)[0]
                sql = path.read_text(encoding="utf-8")
                checksum = _sha256(sql.encode())
                if version in recorded:
                    if recorded[version] != checksum:
                        raise MigrationError(
                            f"migration {version} checksum changed after application"
                        )
                    continue
                escaped_name = path.name.replace("'", "''")
                script = (
                    "BEGIN IMMEDIATE;\n"
                    f"{sql}\n"
                    "INSERT INTO schema_migrations(version, name, checksum, applied_at_utc) "
                    f"VALUES ('{version}', '{escaped_name}', '{checksum}', "
                    f"'{_format_utc(_utc_now())}');\nCOMMIT;"
                )
                try:
                    connection.executescript(script)
                except sqlite3.Error as exc:
                    raise MigrationError(f"migration {path.name} failed: {exc}") from exc
                applied_now.append(version)
        return tuple(applied_now)


class LocalMarketDataStore:
    """Local cache shared safely by several processes on one data root.

    Each instance holds an owner lock for its lifetime. Crash recovery only touches
    journal entries and running jobs whose owner lock is no longer held, so opening
    a second store never interrupts work that a live process is still doing.
    """

    def __init__(
        self,
        root: Path,
        *,
        now: Callable[[], datetime] | None = None,
        replace_file: Callable[[Path, Path], None] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.database_path = self.root / "metadata.sqlite3"
        self._now = now or _utc_now
        self._replace_file = replace_file or os.replace
        self.root.mkdir(parents=True, exist_ok=True)
        self.owner_id = uuid4().hex
        self._owner_lock = _ExclusiveFileLock(self._owner_lock_path(self.owner_id))
        with self._maintenance_lock():
            # Taken under the maintenance lock so a concurrent stale-owner sweep cannot
            # unlink this lock file between its creation and its lock.
            self._owner_lock.acquire(blocking=True)
            try:
                MigrationRunner(self.database_path).apply()
                self._configure_database()
                self._recover_interrupted_writes()
                self._recover_interrupted_jobs()
                self._remove_stale_owner_locks()
            except BaseException:
                self._owner_lock.release()
                raise
        self._closed = False

    def close(self) -> None:
        """Release the owner lock; unfinished work becomes recoverable by others."""
        self._closed = True
        self._owner_lock.release()

    def __enter__(self) -> LocalMarketDataStore:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def _require_open(self) -> None:
        if self._closed:
            raise StorageError("market data store is closed")

    def _owner_lock_path(self, owner_id: str) -> Path:
        return self.root / "owners" / f"{owner_id}.lock"

    @contextmanager
    def _maintenance_lock(self) -> Iterator[None]:
        lock = _ExclusiveFileLock(self.root / "maintenance.lock")
        lock.acquire(blocking=True)
        try:
            yield
        finally:
            lock.release()

    def _owner_is_alive(self, owner_id: object) -> bool:
        if owner_id == self.owner_id:
            return True
        if not isinstance(owner_id, str) or not OWNER_ID_PATTERN.fullmatch(owner_id):
            return False
        path = self._owner_lock_path(owner_id)
        if not path.exists():
            return False
        probe = _ExclusiveFileLock(path)
        if not probe.acquire(blocking=False):
            return True
        probe.release()
        return False

    def _remove_stale_owner_locks(self) -> None:
        for path in (self.root / "owners").glob("*.lock"):
            if not self._owner_is_alive(path.stem):
                path.unlink(missing_ok=True)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Yield one transaction (commit on success, rollback on error), then close.

        `with sqlite3.connect(...)` alone only ends the transaction and leaves the
        connection and its file descriptors open until garbage collection.
        """
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            with connection:
                yield connection

    def _configure_database(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")

    def write_result(self, result: BarsResult) -> WriteReceipt:
        self._require_open()
        document = result.to_document()
        validate_document(document)
        manifest = result.coverage
        coverage_key = self._coverage_key(manifest)
        grouped = self._group_bars(result.bars)
        file_entries = [self._file_entry(group) for group in grouped.values()]
        parquet_paths = tuple(entry["final_path"] for entry in file_entries)
        requires_file_write = any(
            not (self.root / entry["final_path"]).exists() for entry in file_entries
        )
        created_paths: list[str] = []
        journal_id = str(uuid4())
        timestamp = _format_utc(self._now())

        if requires_file_write:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO write_journal (
                        journal_id, coverage_key, manifest_json, files_json,
                        state, owner_id, created_at_utc, updated_at_utc
                    ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)
                    """,
                    (
                        journal_id,
                        coverage_key,
                        _canonical_json(manifest),
                        _canonical_json(file_entries),
                        self.owner_id,
                        timestamp,
                        timestamp,
                    ),
                )

        try:
            for group, entry in zip(grouped.values(), file_entries, strict=True):
                final_path = self.root / entry["final_path"]
                if final_path.exists():
                    self._assert_parquet_payload(final_path, entry["payload_sha256"])
                    continue
                temp_path = self.root / entry["temp_path"]
                temp_path.parent.mkdir(parents=True, exist_ok=True)
                table = pa.Table.from_pylist([dict(bar) for bar in group], schema=PARQUET_SCHEMA)
                pq.write_table(table, temp_path, compression="zstd", version="2.6")
                self._fsync_file(temp_path)
                self._replace_file(temp_path, final_path)
                self._fsync_directory(final_path.parent)
                created_paths.append(entry["final_path"])

            if requires_file_write:
                with self._connect() as connection:
                    connection.execute(
                        "UPDATE write_journal SET state = 'renamed', updated_at_utc = ? "
                        "WHERE journal_id = ?",
                        (timestamp, journal_id),
                    )
            self._commit_coverage(
                coverage_key,
                manifest,
                parquet_paths,
                journal_id if requires_file_write else None,
            )
        except Exception as exc:
            self._cleanup_failed_write(file_entries)
            if requires_file_write:
                with self._connect() as connection:
                    connection.execute(
                        "UPDATE write_journal SET state = 'failed', error = ?, updated_at_utc = ? "
                        "WHERE journal_id = ?",
                        (type(exc).__name__, _format_utc(self._now()), journal_id),
                    )
            if isinstance(exc, StorageError):
                raise
            raise CacheWriteError(f"atomic Parquet write failed: {type(exc).__name__}") from exc

        return WriteReceipt(coverage_key, parquet_paths, tuple(created_paths))

    def read_exact(
        self,
        query: BarsQuery,
        source_id: str,
        dataset_revision: str | None = None,
    ) -> BarsResult | None:
        cutoff = _format_utc(query.as_of_utc) if query.as_of_utc is not None else None
        statement = (
            "SELECT manifest_json, parquet_paths_json FROM coverage_manifests "
            "WHERE instrument_id = ? AND timeframe = ? AND requested_start_utc = ? "
            "AND requested_end_utc = ? AND source_id = ? "
            "AND point_in_time_cutoff_utc IS ?"
        )
        parameters: list[object] = [
            query.instrument_id,
            query.timeframe,
            _format_utc(query.start_utc),
            _format_utc(query.end_utc),
            source_id,
            cutoff,
        ]
        if dataset_revision is not None:
            statement += " AND dataset_revision = ?"
            parameters.append(dataset_revision)
        with self._connect() as connection:
            rows = connection.execute(statement, parameters).fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise AmbiguousRevisionError("cache read requires an explicit unique dataset revision")
        manifest = cast(dict[str, Any], json.loads(cast(str, rows[0]["manifest_json"])))
        relative_paths = cast(list[str], json.loads(cast(str, rows[0]["parquet_paths_json"])))
        bars: list[dict[str, Any]] = []
        for relative_path in relative_paths:
            bars.extend(self._read_verified_parquet(relative_path))
        bars.sort(key=lambda bar: cast(str, bar["interval_start_utc"]))
        result = BarsResult(tuple(bars), manifest)
        self._validate_cached(result)
        return result

    def read_composed(self, query: BarsQuery, source_id: str) -> BarsResult | None:
        """Serve a range from several cached results without calling upstream.

        Only cached results with the same source, timeframe and point-in-time cutoff
        are combined, and only when every interval they miss is a settled fact (such as
        "provider_no_bar": no trades). Each then accounts for every interval its provider
        expected in its own range, so their bars plus settled gaps inside the query range
        are the full expected set. Returns None, which means "fetch upstream", when:
        - the candidates span several revisions (no implicit choice),
        - they leave any part of the range uncovered,
        - one result has a bar for a minute another settled as having none,
        - any bar is not final.
        Bars that differ for the same minute are an integrity error, not a choice.
        """
        cutoff = _format_utc(query.as_of_utc) if query.as_of_utc is not None else None
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT manifest_json, parquet_paths_json FROM coverage_manifests "
                "WHERE instrument_id = ? AND timeframe = ? AND source_id = ? "
                "AND point_in_time_cutoff_utc IS ?",
                (query.instrument_id, query.timeframe, source_id, cutoff),
            ).fetchall()
        candidates: list[tuple[dict[str, Any], list[str], datetime, datetime]] = []
        for row in rows:
            manifest = cast(dict[str, Any], json.loads(cast(str, row["manifest_json"])))
            start = _parse_utc(cast(str, manifest["requested_start_utc"]))
            end = _parse_utc(cast(str, manifest["requested_end_utc"]))
            gaps = cast(list[dict[str, str]], manifest["missing_intervals"])
            if any(gap["reason"] not in SETTLED_MISSING_REASONS for gap in gaps):
                continue
            if end <= query.start_utc or start >= query.end_utc:
                continue
            paths = cast(list[str], json.loads(cast(str, row["parquet_paths_json"])))
            candidates.append((manifest, paths, start, end))
        if not candidates:
            return None
        identity_keys = ("dataset_revision", "source_feed_scope", "adjustment")
        if len({tuple(c[0][key] for key in identity_keys) for c in candidates}) != 1:
            return None
        covered_until = query.start_utc
        for _manifest, _paths, start, end in sorted(candidates, key=lambda c: c[2]):
            if start > covered_until:
                return None
            covered_until = max(covered_until, end)
        if covered_until < query.end_utc:
            return None

        bars_by_start: dict[str, dict[str, Any]] = {}
        for _manifest, paths, _start, _end in candidates:
            for relative_path in paths:
                for bar in self._read_verified_parquet(relative_path):
                    bar_start = _parse_utc(cast(str, bar["interval_start_utc"]))
                    if not query.start_utc <= bar_start < query.end_utc:
                        continue
                    key = cast(str, bar["interval_start_utc"])
                    existing = bars_by_start.setdefault(key, bar)
                    if existing != bar:
                        raise CacheIntegrityError("cached results disagree on one interval")
        bars = tuple(bars_by_start[key] for key in sorted(bars_by_start))
        if not all(cast(bool, bar["is_final"]) for bar in bars):
            return None
        settled: dict[str, dict[str, str]] = {}
        for manifest, _paths, _start, _end in candidates:
            for gap in cast(list[dict[str, str]], manifest["missing_intervals"]):
                if query.start_utc <= _parse_utc(gap["start_utc"]) < query.end_utc:
                    settled[gap["start_utc"]] = gap
        if settled.keys() & bars_by_start.keys():
            return None  # one result saw trades where another saw none: refetch, do not pick
        missing = [settled[key] for key in sorted(settled)]

        exemplar = candidates[0][0]
        coverage: dict[str, Any] = {
            **exemplar,
            "requested_start_utc": _format_utc(query.start_utc),
            "requested_end_utc": _format_utc(query.end_utc),
            "coverage_status": (
                "complete" if bars and not missing else ("partial" if bars else "empty")
            ),
            "verification_status": (
                "verified"
                if all(c[0]["verification_status"] == "verified" for c in candidates)
                else "unverified"
            ),
            "observed_interval_count": len(bars),
            "expected_interval_count": len(bars) + len(missing),
            "missing_intervals": missing,
        }
        result = BarsResult(bars, coverage)
        self._validate_cached(result)
        return result

    def detect_gaps(
        self,
        query: BarsQuery,
        source_id: str,
        dataset_revision: str | None = None,
    ) -> tuple[Gap, ...]:
        cached = self.read_exact(query, source_id, dataset_revision)
        if cached is None:
            return (Gap(_format_utc(query.start_utc), _format_utc(query.end_utc), "not_cached"),)
        missing = cast(list[dict[str, str]], cached.coverage["missing_intervals"])
        return tuple(Gap(item["start_utc"], item["end_utc"], item["reason"]) for item in missing)

    def enqueue_job(self, query: BarsQuery, source_id: str) -> str:
        request = self._query_document(query)
        job_key = _sha256(_canonical_json({"request": request, "source_id": source_id}).encode())
        timestamp = _format_utc(self._now())
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO acquisition_jobs (
                    job_key, request_json, source_id, status, created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, 'queued', ?, ?)
                ON CONFLICT(job_key) DO NOTHING
                """,
                (job_key, _canonical_json(request), source_id, timestamp, timestamp),
            )
        return job_key

    def start_job(self, job_key: str) -> None:
        self._require_open()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE acquisition_jobs
                SET status = 'running', attempt_count = attempt_count + 1,
                    error_code = NULL, owner_id = ?, updated_at_utc = ?
                WHERE job_key = ? AND status IN ('queued', 'interrupted', 'failed')
                """,
                (self.owner_id, _format_utc(self._now()), job_key),
            )
            if cursor.rowcount != 1:
                raise StorageError("job is missing or cannot transition to running")

    def checkpoint_job(self, job_key: str, checkpoint: dict[str, Any]) -> None:
        self._require_open()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE acquisition_jobs SET checkpoint_json = ?, updated_at_utc = ?
                WHERE job_key = ? AND status = 'running' AND owner_id = ?
                """,
                (_canonical_json(checkpoint), _format_utc(self._now()), job_key, self.owner_id),
            )
            if cursor.rowcount != 1:
                raise StorageError("only the store running this job can checkpoint it")

    def complete_job(self, job_key: str) -> None:
        """Mark a running job owned by this store as succeeded; this is terminal."""
        self._finish_job(job_key, "succeeded", None)

    def fail_job(self, job_key: str, error_code: str) -> None:
        """Mark a running job owned by this store as failed; `start_job` may retry it.

        Only a short machine code is stored, so provider messages, URLs or secrets
        never reach the metadata database.
        """
        if not JOB_ERROR_CODE_PATTERN.fullmatch(error_code):
            raise StorageError("job error_code must be 1-64 characters of [a-z0-9_]")
        self._finish_job(job_key, "failed", error_code)

    def _finish_job(self, job_key: str, status: str, error_code: str | None) -> None:
        self._require_open()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE acquisition_jobs SET status = ?, error_code = ?, updated_at_utc = ?
                WHERE job_key = ? AND status = 'running' AND owner_id = ?
                """,
                (status, error_code, _format_utc(self._now()), job_key, self.owner_id),
            )
            if cursor.rowcount != 1:
                raise StorageError("only the store running this job can finish it")

    def job_state(self, job_key: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM acquisition_jobs WHERE job_key = ?", (job_key,)
            ).fetchone()
        if row is None:
            raise StorageError("job not found")
        value = dict(row)
        value["checkpoint"] = (
            json.loads(cast(str, value.pop("checkpoint_json")))
            if value["checkpoint_json"] is not None
            else None
        )
        return value

    def recover_interrupted_jobs(self) -> int:
        with self._maintenance_lock():
            return self._recover_interrupted_jobs()

    def _recover_interrupted_jobs(self) -> int:
        recovered = 0
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT job_key, owner_id FROM acquisition_jobs WHERE status = 'running'"
            ).fetchall()
            for row in rows:
                if self._owner_is_alive(row["owner_id"]):
                    continue
                cursor = connection.execute(
                    """
                    UPDATE acquisition_jobs
                    SET status = 'interrupted', error_code = 'process_restarted',
                        updated_at_utc = ?
                    WHERE job_key = ? AND status = 'running' AND owner_id IS ?
                    """,
                    (_format_utc(self._now()), row["job_key"], row["owner_id"]),
                )
                recovered += cursor.rowcount
        return recovered

    def pin_revision(
        self,
        pin_id: str,
        *,
        source_id: str,
        instrument_id: str,
        timeframe: str,
        adjustment: str,
        dataset_revision: str,
        reason: str,
    ) -> None:
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM revision_pins WHERE pin_id = ?", (pin_id,)
            ).fetchone()
            expected = (
                source_id,
                instrument_id,
                timeframe,
                adjustment,
                dataset_revision,
                reason,
            )
            if existing is not None:
                actual = tuple(
                    cast(str, existing[key])
                    for key in (
                        "source_id",
                        "instrument_id",
                        "timeframe",
                        "adjustment",
                        "dataset_revision",
                        "reason",
                    )
                )
                if actual != expected:
                    raise StorageError("pin_id already identifies a different revision or reason")
                return
            connection.execute(
                """
                INSERT INTO revision_pins (
                    pin_id, source_id, instrument_id, timeframe, adjustment,
                    dataset_revision, reason, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pin_id,
                    source_id,
                    instrument_id,
                    timeframe,
                    adjustment,
                    dataset_revision,
                    reason,
                    _format_utc(self._now()),
                ),
            )

    def is_revision_pinned(
        self,
        *,
        source_id: str,
        instrument_id: str,
        timeframe: str,
        adjustment: str,
        dataset_revision: str,
    ) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM revision_pins
                WHERE source_id = ? AND instrument_id = ? AND timeframe = ?
                  AND adjustment = ? AND dataset_revision = ? LIMIT 1
                """,
                (source_id, instrument_id, timeframe, adjustment, dataset_revision),
            ).fetchone()
        return row is not None

    def ensure_revision_unpinned(
        self,
        *,
        source_id: str,
        instrument_id: str,
        timeframe: str,
        adjustment: str,
        dataset_revision: str,
    ) -> None:
        if self.is_revision_pinned(
            source_id=source_id,
            instrument_id=instrument_id,
            timeframe=timeframe,
            adjustment=adjustment,
            dataset_revision=dataset_revision,
        ):
            raise RevisionPinnedError("dataset revision is pinned and cannot be evicted")

    def recover_interrupted_writes(self) -> int:
        with self._maintenance_lock():
            return self._recover_interrupted_writes()

    def _recover_interrupted_writes(self) -> int:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM write_journal WHERE state IN ('pending', 'renamed')"
            ).fetchall()
        recovered = 0
        for row in rows:
            if self._owner_is_alive(row["owner_id"]):
                continue
            files = cast(list[dict[str, Any]], json.loads(cast(str, row["files_json"])))
            try:
                for entry in files:
                    final_path = self.root / cast(str, entry["final_path"])
                    temp_path = self.root / cast(str, entry["temp_path"])
                    if not final_path.exists() and temp_path.exists():
                        self._assert_parquet_payload(temp_path, cast(str, entry["payload_sha256"]))
                        self._replace_file(temp_path, final_path)
                        self._fsync_directory(final_path.parent)
                    self._assert_parquet_payload(final_path, cast(str, entry["payload_sha256"]))
                manifest = cast(dict[str, Any], json.loads(cast(str, row["manifest_json"])))
                paths = tuple(cast(str, entry["final_path"]) for entry in files)
                self._commit_coverage(
                    cast(str, row["coverage_key"]), manifest, paths, row["journal_id"]
                )
                recovered += 1
            except Exception as exc:
                self._remove_journal_temp_files(files)
                with self._connect() as connection:
                    connection.execute(
                        "UPDATE write_journal SET state = 'failed', error = ?, updated_at_utc = ? "
                        "WHERE journal_id = ?",
                        (type(exc).__name__, _format_utc(self._now()), row["journal_id"]),
                    )
        return recovered

    def _remove_journal_temp_files(self, files: list[dict[str, Any]]) -> None:
        for entry in files:
            temp_path = (self.root / cast(str, entry["temp_path"])).resolve()
            # Journal paths come from the database; never delete outside the data root
            # or anything that is not one of this store's temporary part names.
            if (
                temp_path.is_relative_to(self.root)
                and temp_path.name.startswith(".part-")
                and temp_path.suffix == ".tmp"
            ):
                temp_path.unlink(missing_ok=True)

    def _commit_coverage(
        self,
        coverage_key: str,
        manifest: dict[str, Any],
        parquet_paths: tuple[str, ...],
        journal_id: object | None,
    ) -> None:
        timestamp = _format_utc(self._now())
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO dataset_revisions (
                    source_id, instrument_id, timeframe, adjustment,
                    dataset_revision, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                """,
                (
                    manifest["source_id"],
                    manifest["instrument_id"],
                    manifest["timeframe"],
                    manifest["adjustment"],
                    manifest["dataset_revision"],
                    timestamp,
                ),
            )
            connection.execute(
                """
                INSERT INTO coverage_manifests (
                    coverage_key, instrument_id, timeframe, requested_start_utc,
                    requested_end_utc, source_id, source_feed_scope, adjustment,
                    dataset_revision, point_in_time_cutoff_utc, coverage_status,
                    verification_status, manifest_json, parquet_paths_json, checked_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(coverage_key) DO UPDATE SET
                    manifest_json = excluded.manifest_json,
                    parquet_paths_json = excluded.parquet_paths_json,
                    coverage_status = excluded.coverage_status,
                    checked_at_utc = excluded.checked_at_utc
                """,
                (
                    coverage_key,
                    manifest["instrument_id"],
                    manifest["timeframe"],
                    manifest["requested_start_utc"],
                    manifest["requested_end_utc"],
                    manifest["source_id"],
                    manifest["source_feed_scope"],
                    manifest["adjustment"],
                    manifest["dataset_revision"],
                    manifest["point_in_time_cutoff_utc"],
                    manifest["coverage_status"],
                    manifest["verification_status"],
                    _canonical_json(manifest),
                    _canonical_json(parquet_paths),
                    timestamp,
                ),
            )
            if journal_id is not None:
                connection.execute(
                    "UPDATE write_journal SET state = 'committed', updated_at_utc = ? "
                    "WHERE journal_id = ?",
                    (timestamp, journal_id),
                )

    @staticmethod
    def _validate_cached(result: BarsResult) -> None:
        try:
            validate_document(result.to_document())
        except ValueError as exc:
            raise CacheIntegrityError(f"cached result violates the contract: {exc}") from exc

    @staticmethod
    def _coverage_key(manifest: dict[str, Any]) -> str:
        identity = {
            key: manifest[key]
            for key in (
                "instrument_id",
                "timeframe",
                "requested_start_utc",
                "requested_end_utc",
                "source_id",
                "source_feed_scope",
                "adjustment",
                "dataset_revision",
                "point_in_time_cutoff_utc",
            )
        }
        return _sha256(_canonical_json(identity).encode())

    @staticmethod
    def _group_bars(
        bars: Iterable[dict[str, Any]],
    ) -> dict[str, tuple[dict[str, Any], ...]]:
        groups: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for bar in bars:
            groups[_session_date(bar)].append(dict(bar))
        return {
            date: tuple(sorted(group, key=lambda bar: cast(str, bar["interval_start_utc"])))
            for date, group in sorted(groups.items())
        }

    def _file_entry(self, bars: tuple[dict[str, Any], ...]) -> dict[str, str]:
        first = bars[0]
        payload_sha256 = _sha256(_canonical_json(bars).encode())
        session_date = _session_date(first)
        relative_directory = Path("bars") / Path(
            f"source_id={_safe_component(cast(str, first['source_id']))}",
            f"instrument_id={_safe_component(cast(str, first['instrument_id']))}",
            f"timeframe={_safe_component(cast(str, first['timeframe']))}",
            f"session_date={session_date}",
            f"revision={_safe_component(cast(str, first['dataset_revision']))}",
        )
        final_path = relative_directory / f"part-{payload_sha256}.parquet"
        temp_path = relative_directory / f".{final_path.name}.{uuid4().hex}.tmp"
        return {
            "final_path": final_path.as_posix(),
            "temp_path": temp_path.as_posix(),
            "payload_sha256": payload_sha256,
        }

    def _assert_parquet_payload(self, path: Path, expected_sha256: str) -> None:
        bars = self._read_parquet(path)
        actual = _sha256(_canonical_json(tuple(bars)).encode())
        if actual != expected_sha256:
            raise CacheIntegrityError(f"Parquet payload checksum mismatch: {path.name}")

    def _read_verified_parquet(self, relative_path: str) -> list[dict[str, Any]]:
        path = (self.root / relative_path).resolve()
        if not path.is_relative_to(self.root):
            raise CacheIntegrityError("Parquet path escapes the configured data root")
        prefix = "part-"
        if not path.name.startswith(prefix) or path.suffix != ".parquet":
            raise CacheIntegrityError("Parquet part name does not contain a content hash")
        expected_sha256 = path.stem.removeprefix(prefix)
        bars = self._read_parquet(path)
        actual_sha256 = _sha256(_canonical_json(tuple(bars)).encode())
        if actual_sha256 != expected_sha256:
            raise CacheIntegrityError(f"Parquet payload checksum mismatch: {path.name}")
        return bars

    @staticmethod
    def _read_parquet(path: Path) -> list[dict[str, Any]]:
        # Bars are not re-validated one by one here: callers compare the content hash
        # with the name the part was written under, and every result assembled from
        # parts is validated as a whole contract document before it is returned.
        if not path.is_file():
            raise CacheIntegrityError(f"Parquet part is missing: {path}")
        try:
            table = pq.ParquetFile(path).read()
            return cast(list[dict[str, Any]], table.to_pylist())
        except Exception as exc:
            if isinstance(exc, CacheIntegrityError):
                raise
            raise CacheIntegrityError(f"invalid Parquet part: {path.name}") from exc

    def _cleanup_failed_write(self, file_entries: list[dict[str, str]]) -> None:
        # Final parts are immutable and content-addressed. Another process may have
        # found and committed a part this write created, so only temp files are removed;
        # an unreferenced part is invisible to reads and reused by a retry.
        for entry in file_entries:
            (self.root / entry["temp_path"]).unlink(missing_ok=True)

    @staticmethod
    def _fsync_file(path: Path) -> None:
        with path.open("rb") as handle:
            os.fsync(handle.fileno())

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _query_document(query: BarsQuery) -> dict[str, object]:
        return {
            "instrument_id": query.instrument_id,
            "timeframe": query.timeframe,
            "start_utc": _format_utc(query.start_utc),
            "end_utc": _format_utc(query.end_utc),
            "as_of_utc": _format_utc(query.as_of_utc) if query.as_of_utc else None,
        }


class CachedMarketDataProvider:
    """Cache-first wrapper that never merges distinct sources or revisions."""

    def __init__(self, upstream: MarketDataProvider, store: LocalMarketDataStore) -> None:
        self.upstream = upstream
        self.store = store

    @property
    def source_id(self) -> str:
        return self.upstream.source_id

    def capabilities(self) -> dict[str, Any]:
        return self.upstream.capabilities()

    def instruments(self) -> list[dict[str, Any]]:
        return self.upstream.instruments()

    def get_bars(self, query: BarsQuery) -> BarsResult:
        validate_bars_query(query)
        try:
            cached = self.store.read_exact(query, self.source_id)
        except AmbiguousRevisionError:
            # Several revisions are cached; never pick one silently. The upstream
            # result names its revision explicitly and is read back by that revision.
            cached = None
        if cached is not None and self._is_reusable(cached):
            return cached
        composed = self.store.read_composed(query, self.source_id)
        if composed is not None:
            return composed
        fetched = self.upstream.get_bars(query)
        self.store.write_result(fetched)
        stored = self.store.read_exact(
            query,
            self.source_id,
            cast(str, fetched.coverage["dataset_revision"]),
        )
        if stored is None:
            raise CacheIntegrityError("stored result could not be read back")
        return stored

    @staticmethod
    def _is_reusable(cached: BarsResult) -> bool:
        # A provisional bar can still be revised upstream, and an unexplained gap may
        # still be filled, so a cached result is served without refetching only when every
        # bar is final and every missing minute is a settled fact (e.g. no trades).
        settled = all(
            gap["reason"] in SETTLED_MISSING_REASONS
            for gap in cast(list[dict[str, str]], cached.coverage["missing_intervals"])
        )
        return settled and all(cast(bool, bar["is_final"]) for bar in cached.bars)
