# ADR 0006 — Session-date partitions, side-effect-free API import and one product version

- **Status:** accepted
- **Date:** 2026-10-16
- **Amends:** ADR 0002 (Parquet partition key)

## Context

The review found several smaller operational risks:

- **Parquet partitions:** parts were partitioned by UTC date, so a winter post-market session (19:00–20:00 ET is 00:00–01:00 UTC) was split across two `date=` directories.
- **SQLite connections:** connections were never closed. `with sqlite3.connect()` only ends the transaction.
- **Recovery temp files:** a failed crash recovery left its temporary files behind.
- **API import:** importing the API module opened the configured data directory, and the default provider read its synthetic data from `tests/`.
- **Request IDs:** the API echoed any `X-Request-ID`.
- **Version strings:** the Python, API, npm, Tauri and Cargo versions disagreed.
- **Desktop polling:** an in-flight health check from a replaced endpoint could still update the screen.

## Decision

- **Partition by exchange session date:** partition Parquet parts by the `America/New_York` date of the bar start (`session_date=YYYY-MM-DD`). Coverage stores explicit part paths, so parts written under the old `date=` layout stay readable. New writes use the new layout.
- **Close every connection:** each SQLite use is one transaction followed by `close()` (`contextlib.closing`), in the store and in the migration runner.
- **Clean up recovery temp files:** when recovery of an orphaned journal fails, delete that journal's temporary parts. Only names that look like this store's own temporary parts (`.part-*.tmp`) under the data root are eligible.
- **Side-effect-free import:**
  - `create_app()` builds the default provider lazily, under a lock, on first use. The FastAPI lifespan warms it up so that a server still fails at startup on misconfiguration.
  - The synthetic fixture moves to `services/market_data/fixtures/`.
- **Request IDs:** echo an incoming request ID only if it matches `[A-Za-z0-9._:-]{1,128}`. Otherwise generate a UUID.
- **One product version, `0.4.0-dev`:**
  - Spelled `0.4.0.dev0` in `pyproject.toml` (PEP 440).
  - The desktop shell reads the version from its `package.json`.
  - `tests/test_versions.py` fails if the API, root and desktop `package.json`, `tauri.conf.json`, `Cargo.toml` and `pyproject.toml` disagree.
  - The `@siw/contracts` library keeps its own version, because contract compatibility is tracked by `schema_version`.
- **Ignore stale desktop checks:** the desktop health loop carries a generation number. A check started before the endpoint changed or the component unmounted cannot update state or schedule polls.
- **`pnpm` fallback in `verify.sh`:** when `pnpm` is not on `PATH`, `verify.sh` puts a temporary corepack shim on `PATH`. Package scripts call `pnpm` through `sh`, so a shell function would not reach them.

## Alternatives

- **Rewrite existing partitions into the new layout:** this needs a migration that moves files and rewrites Coverage paths. Old parts are immutable and still verified by hash, so leaving them in place is safe.
- **`uvicorn --factory`:** this would also avoid import-time work, but it changes every documented run command. The lazy provider keeps `services.api.main:app` working.
- **Strip unsafe characters from a request ID instead of replacing it:** a partially kept ID can collide with or impersonate another request's ID. Replacing it is unambiguous.

## Trade-offs

- **Duplicate parts across layouts:** the same bars written before and after this change exist under two directories. Both are content-addressed and verified on read.
- **Prerelease version and Windows bundling:** `0.4.0-dev` is a valid semver prerelease, but Windows MSI bundling rejects non-numeric prerelease identifiers. Bundling is disabled and Tauri native builds remain **unverified**.

## Evidence/verification

- `tests/market_data/test_storage.py`:
  - one partition for a post-market session that crosses UTC midnight;
  - every SQLite connection is closed;
  - failed recovery removes only its own temporary file.
- `tests/api/test_api.py`:
  - importing the module creates no data directory;
  - lifespan opens the store;
  - the fixture lives under `services/`;
  - unsafe request IDs are replaced.
- `tests/test_versions.py`: versions match across ecosystems.
- `apps/desktop/src/App.test.tsx`: a stale check never reaches the screen, and the version comes from `package.json`.
- All of these fail on the previous code.

## Revisit when

- old `date=` partitions need to be compacted or garbage-collected;
- Windows installers are produced;
- request IDs are propagated to external services that impose their own format.
