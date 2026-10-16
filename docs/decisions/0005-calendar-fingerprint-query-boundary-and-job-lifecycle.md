# ADR 0005 — Calendar schedule fingerprint, query boundary and job lifecycle

- **Status:** accepted
- **Date:** 2026-10-16
- **Amends:** ADR 0003 (calendar version and loaded range), ADR 0004 (job ownership)

## Context

- **Calendar version:** ADR 0003 recorded `calendar_version` as the `exchange-calendars` library version. A patch upgrade that left the schedule unchanged still changed that string. The aggregator would then reject every stored 1-minute bar as having a mismatched calendar lineage.
- **Calendar range:** the library's default range runs from about 20 years before to 1 year after the day the process starts. The same historical request could therefore succeed today and return 422 later.
- **Validation order:** the cache-first wrapper looked up the cache before the provider validated the query. A non-UTC offset could be served from cache while an uncached request was rejected. Cache keys use whole-second UTC text, so a sub-second `as_of_utc` shared a key with the truncated cutoff. A later request at that truncated cutoff could then be served bars published after it.
- **Job lifecycle:** jobs could be queued, started, checkpointed and interrupted, but not finished. Any store could also checkpoint a job that another live process was running.

## Decision

- **Fixed calendar range:**
  - Load the calendar for fixed bounds, `2000-01-03..2030-12-31`, once per process per calendar ID.
  - Dates outside the range raise `CalendarError`, which the API returns as `422 invalid_market_data_query`.
- **Schedule fingerprint as `calendar_version`:**
  - `calendar_version` is `xnys-regular-<first>-<last>-sha256-<16 hex>`, a SHA-256 over every session date with its UTC open and close inside the bounds. XNAS and XNYS share it while the library aliases them.
  - A library upgrade that changes no session keeps the version. A schedule change alters it, and `test_fixture_lineage_matches_current_calendar_schedule` fails until someone reviews the difference and regenerates the fixtures.
- **Query validation before the cache:** `validate_bars_query` runs before any cache lookup and in every provider. It requires UTC offsets, whole-second precision and `end_utc > start_utc`.
- **Exact decimal comparison:** the TypeScript validator compares decimal strings exactly instead of using `Number`, matching the Python `Decimal` checks.
- **Instant-based publication time:** aggregation picks the latest `available_at_utc` by instant, not by string order.
- **Job lifecycle:**
  - `complete_job` sets `succeeded`, which is terminal.
  - `fail_job` sets `failed` with a code matching `[a-z0-9_]{1,64}`. Free text is rejected so that provider messages and secrets never reach SQLite.
  - `start_job` may retry `failed` and `interrupted` jobs. The checkpoint is kept and `attempt_count` increases.
  - Checkpointing and finishing require the store that owns the running job. A closed store can do neither.

## Alternatives

- **Pin the library version string and bump it manually:** this still invalidates data on every upgrade and relies on someone noticing.
- **Floor sub-second cutoffs instead of rejecting them:** a floored cutoff is never earlier-leaking, but it silently changes what the caller asked for. Rejecting keeps the request explicit.
- **Allow any store to finish a job:** this is simpler, but it would let a second process overwrite a live owner's outcome.

## Trade-offs

- **Startup cost:** loading 31 years of sessions and hashing them adds about 0.5 s on first use in each process. Caching makes later constructions free and shortened the test run.
- **Range edges:** requests before 2000-01-03 or after 2030-12-31 are rejected until the bounds are extended. Extending them changes the fingerprint and therefore requires regenerating fixtures and lineage.
- **Fingerprint sensitivity:** library changes to future holiday rules inside the range also change the fingerprint, even when the stored bars predate the changed dates. That is a deliberate review point, not a silent pass.
- **Old local data:** caches written before this change keep the old `exchange-calendars-4.13.2` lineage and will not aggregate with new bars. They should be rebuilt, consistent with ADR 0003's guidance on stale caches.

## Evidence/verification

- `tests/market_data/test_calendar.py`: fixed range edges, a fingerprint shared by XNAS and XNYS, and the fixture-lineage guard.
- `tests/market_data/test_storage.py`: offset and sub-second queries are rejected even when the cache holds a matching entry, plus the job lifecycle and ownership rules.
- `tests/market_data/test_aggregation.py`: publication time chosen by instant.
- `packages/contracts/tests/contracts.test.ts`: exact decimal comparison, and a precision fixture that the previous TypeScript validator accepted.
- All behavioural tests fail on the previous code.

## Revisit when

- requests outside 2000–2030 are needed;
- XNAS and XNYS regular schedules diverge;
- the Collector needs more job states, such as cancellation or a scheduled retry.
