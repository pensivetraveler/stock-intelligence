# ADR 0002 — Local market-data storage and recovery boundary

- **Status:** accepted
- **Date:** 2026-10-12

## Context

Phase 1B needs local, restart-safe storage for canonical 1-minute bars, Coverage metadata, acquisition checkpoints and immutable dataset-revision pins. The API and Collector must later be separable, so the local database is an implementation detail behind a service boundary and must never be shared over a network filesystem. Presence of a Parquet file alone cannot prove complete or verified coverage.

## Decision

- Store metadata, numbered SQL migration history, Coverage manifests, acquisition jobs, revision pins and a recoverable write journal in SQLite. Enable WAL and `synchronous=FULL` for local durability.
- Store canonical bars as immutable PyArrow Parquet parts partitioned by source, instrument, timeframe, UTC date and dataset revision. Pin PyArrow `25.0.1` because the repository still supports Python 3.10; PyArrow 26 requires Python 3.11 or newer.
- Name each part from the canonical bar payload SHA-256. Recompute the hash during reads; file existence is insufficient evidence of integrity.
- Write Parquet to a same-directory temporary file, fsync it, then atomically rename it. Record the intended files and manifest in SQLite before writing. If the process stops after rename but before Coverage commit, startup recovery verifies the content hash and completes the metadata transaction.
- A failed write exposes neither Coverage nor a partial temporary file. Repeating the same request reuses the same content-addressed part and upserts one Coverage identity.
- Cache lookup is exact by source, request range, cutoff and revision. Multiple eligible revisions without an explicit canonical choice are an error, not an implicit “latest” selection.
- `SIW_DATA_DIR` explicitly enables the cache-first API wrapper. Without it, the existing synthetic provider remains side-effect free.

## Consequences and trade-offs

- SQLite provides low operational cost and transactional metadata, while Parquet keeps analytical bars portable and columnar. PyArrow adds a sizeable binary dependency.
- The write journal makes the SQLite/Parquet boundary recoverable but does not create a distributed transaction. Immutable content-addressed parts may remain as harmless orphans after an unrecoverable failure and can be audited later.
- Gap detection currently follows explicit 1-minute request boundaries and stored manifest gaps. Exchange holidays, DST, half-days and session-aware expected intervals remain Phase 1C work.
- Revision pins currently guard eviction eligibility; automated retention and garbage collection are not implemented.

## Reconsider when

- one local writer no longer suffices;
- Collector and API move to different hosts;
- data volume or query patterns justify DuckDB or another catalog/query layer;
- schema evolution requires a richer migration framework;
- Python 3.10 support is dropped, allowing a newer PyArrow major version.
