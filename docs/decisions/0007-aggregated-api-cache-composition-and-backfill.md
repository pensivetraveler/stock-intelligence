# ADR 0007 — Aggregated bars API, cache composition and resumable backfill

- **Status:** accepted
- **Date:** 2026-10-16
- **Amends:** ADR 0002 (exact-only cache lookup)

## Context

The Phase 1 roadmap asks for session-aware aggregation, gap detection, idempotent backfill, throttling and a cache that avoids duplicate fetches. Before this change:

- aggregation existed only as an internal module, and the public API served `1m` only;
- the cache answered exact request ranges only, so a chunked backfill could not serve a different API range;
- the Collector had no acquisition logic.

## Decision

- **Aggregated timeframes:** `GET /v1/market/bars` accepts `timeframe` of `5m`, `15m`, `30m` or `60m`. `MarketBarsService` fetches the provider's `1m` result through the same cache and cutoff, then aggregates it per request.
  - Aggregates are never stored. They inherit the source revision and get the derived revision `<source>:agg:<tf>:<extended-hours policy>`.
  - Requests must align to session buckets: every bucket touching the range must lie inside it. Otherwise the API returns 422, so a response never contains a bar extending beyond the requested range.
- **Aggregated coverage:** coverage counts buckets that have a bar.
  - A bucket with only some source minutes is observed, but its bar is `quality: partial` and `is_final: false`.
  - A bucket with no source minutes is listed as missing. When any of its minutes were hidden by the cutoff, the reason is `not_available_as_of_cutoff`.
  - `CoverageManifest.timeframe` is widened from `"1m"` to `1m|5m|15m|30m|60m`. This is additive, so `schema_version` stays `0.1`.
- **Cache composition:** when there is no reusable exact hit, the cache may answer from several stored results. Every one of them must share:
  - instrument, timeframe, source, feed scope, adjustment, dataset revision and point-in-time cutoff;
  - no missing intervals in each result;
  - together, coverage of the whole range;
  - final bars that agree wherever they overlap.

  A complete result covers every interval its provider expected in its own range, so the union of their bars inside the query is the full expected set. No calendar assumption is needed in the store. Any other case fetches upstream, which is how gaps are re-collected. Composed results are not written back.

- **Backfill:** `siw-collector backfill` runs one acquisition job per whole range, using the synthetic provider only.
  - Each chunk (default 390 minutes, at most 1,440) goes through the cache-first provider. A checkpoint is written after each chunk, and the job is marked succeeded at the end.
  - Rerunning a succeeded request is a no-op. A stopped or crashed run resumes from its checkpoint after the store marks it interrupted.
  - Only transient upstream errors are retried, with exponential backoff. Invalid queries, unknown instruments and storage errors fail the job immediately with a short code.
  - `ThrottledProvider` spaces upstream calls by `--min-request-interval-seconds`.

## Alternatives

- **Store aggregates:** this saves CPU, but adds a second revisioned dataset that must be invalidated whenever its source revision, calendar fingerprint or policy changes. Aggregating from cached `1m` bars is cheap at Phase 1 volumes.
- **Expand misaligned ranges to whole buckets:** this silently returns data outside the request and breaks the response range contract.
- **Compute expected intervals in the store with the exchange calendar:** this would assume every provider expects pre/regular/post minutes. Deriving them from complete stored results keeps each provider's own expectation.

## Trade-offs

- Coverage `complete` for an aggregate means every bucket is present, not that every bucket is complete. Consumers must read bar `quality`. This keeps the contract rule that `partial` coverage means some buckets are absent.
- Composition loads every manifest for the instrument, timeframe, source and cutoff. That is acceptable for local Phase 1 volumes; an index or interval table is needed when many manifests exist.
- Backfill uses the synthetic provider only. Real provider adapters, rate-limit headers and entitlement checks belong to Phase 1D.

## Evidence/verification

- `tests/market_data/test_bars_service.py`:
  - complete, missing and partial buckets;
  - cutoff inside a bucket;
  - the capped last hour;
  - alignment rejection;
  - `1m` passthrough.
- `tests/api/test_api.py`: `15m` responses and misaligned ranges.
- `tests/market_data/test_storage.py`:
  - a range composed from two chunks without upstream calls;
  - fall-back cases for a gap, an uncovered range, a provisional bar, a different cutoff and mixed revisions.
- `tests/collector/test_backfill.py`:
  - chunking and a no-op rerun;
  - stop and resume after restart;
  - retry with backoff;
  - exhausted retries followed by a rerun;
  - unknown instrument;
  - throttle spacing;
  - CLI.
- A real run of the backfill CLI subprocess, plus a real uvicorn server for `1m`, `5m`, misaligned `15m` and 1990 queries.

## Revisit when

- real providers need provider-specific chunking or rate limits;
- manifest counts make composition slow;
- aggregates must be served faster than `1m` aggregation allows;
- a daily (`1d`) timeframe is added.
