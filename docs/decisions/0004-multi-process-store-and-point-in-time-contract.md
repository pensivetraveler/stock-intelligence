# ADR 0004 — Multi-process cache ownership and point-in-time response rules

- **Status:** accepted
- **Date:** 2026-10-16

## Context

ADR 0002 ran crash recovery whenever a `LocalMarketDataStore` was constructed. Recovery treated every `running` job and every `pending`/`renamed` write journal as orphaned. If two processes opened the same `SIW_DATA_DIR` (several API workers, or the API alongside a future Collector), opening the second store marked the first store's live work as interrupted or failed. Migrations had the same race.

Two other look-ahead gaps were found in review. First, the cache-first wrapper reused any `complete` Coverage, even when it held provisional (`is_final=false`) bars that upstream could still revise. Second, the response contract did not check that bars were available at the point-in-time cutoff, lay inside the requested range, or arrived ordered and unique. Those rules depended only on each provider's implementation.

## Decision

- Each store instance takes an exclusive OS advisory lock on `owners/<owner_id>.lock` for its lifetime. It uses `flock` on POSIX and `msvcrt.locking` on Windows. The kernel releases this lock when the process exits or crashes.
- Migration `0002` adds `owner_id` to `write_journal` and `acquisition_jobs`. `write_result` and `start_job` record the current owner.
- Recovery only touches rows whose owner lock can be acquired, meaning the owner is gone. Rows with a `NULL` owner (written before 0002) are treated as orphaned.
- Startup migration, recovery and the stale-owner sweep run under `maintenance.lock`. A new owner lock is also created while holding it, so a concurrent sweep cannot unlink a lock file between its creation and its lock.
- `close()` (or a `with` block) releases the owner lock without finalizing rows, which is equivalent to the process exiting. A closed store rejects writes and job starts.
- A failed write no longer deletes final Parquet parts that it created. Another process may already have committed Coverage that points at the same content-addressed part. Unreferenced parts stay as the harmless orphans described in ADR 0002.
- `CachedMarketDataProvider` serves a cached result without refetching only when Coverage is `complete` and every bar is final.
- Contract semantics (Python and TypeScript):
  - A final `price_bar` cannot be available before its interval ends.
  - In a `market_bars_response`, every bar lies within the requested range and bars are strictly ordered by start time.
  - When `point_in_time_cutoff_utc` is set, no bar has `available_at_utc` after it.

## Alternatives

- **One exclusive lock for the whole data root:** simpler, but it forbids the API and Collector from sharing a cache, which conflicts with independent Collector deployment.
- **PID and heartbeat columns:** PIDs are reused, and heartbeats need timeouts that either misjudge slow processes or delay recovery. OS locks report liveness exactly and need no clock.
- **Serve cached provisional bars for `as_of_utc` queries:** a provisional bar is a faithful snapshot only if the provider is perfectly point-in-time. Refetching is the conservative choice until a real provider's revision behaviour is verified.

## Trade-offs

- Advisory locks only work on a local filesystem. The data root must still never sit on a network share, as ADR 0002 already requires.
- The Windows path (`msvcrt.locking`) is implemented but **unverified**, because tests ran on macOS only.
- Results containing provisional bars always call upstream again, which costs more provider requests near the live edge.
- An object that is garbage-collected without `close()` keeps its lock until the process exits. Other processes then see it as alive, which errs on the safe side.

## Evidence/verification

- `tests/market_data/test_storage.py`:
  - Live-owner work survives a second store, and legacy `NULL` rows are recovered.
  - A real child process holding a running job is left alone while alive and recovered after `kill`.
  - A 0001 database upgrades to 0002.
  - A closed store rejects work, and stale locks are swept.
  - Complete Coverage with a provisional bar is refetched.
- New invalid contract fixtures are rejected by both the Python and TypeScript validators.
- All new tests fail on the previous code.

## Revisit when

- the Collector writes to the store in production, to decide whether job lifecycle end states also need owner checks;
- a real provider's point-in-time revision behaviour is verified;
- the data root must be shared across hosts;
- Windows becomes a supported runtime and the lock path is tested there.
