# ADR 0015 — Daily bars cached per settled calendar year

- **Status:** accepted (implemented; checked live with Alpaca AAPL)
- **Date:** 2026-10-20

## Context

Daily and weekly charts ask for about 1,200 daily bars (weekly charts: about 23 years) on every load. Until now they went straight to the provider each time.

Daily bars are small: one row per session, about 252 a year. The minute cache stores one Parquet file per session date. For daily bars that layout would give thousands of tiny files per symbol, each mostly file overhead.

A chart request also ends today, so its range changes daily, and it always contains a session that is not final yet. Because of that, caching whole requests (as `read_exact` does for minute bars) would never be reused.

## Decision

- **Unit:** one coverage record per instrument and calendar year (UTC `YYYY-01-01` to the next year; every regular open falls on the same date in UTC and New York). Daily Parquet parts are partitioned `year=YYYY` instead of `session_date=…`. Minute bars are unchanged.
- **Only settled years are stored.** A year is settled when every bar in it is final and every missing session has a settled reason (`provider_no_bar`, …). The current year never qualifies, so it is always fetched fresh. A year with no bars at all (before the provider's history) is stored as an empty coverage record with no file.
- **Read path (`DailyBarsCache`):**
  - Stored years are read with `read_exact`.
  - The years still needed are fetched in **one** provider call that spans whole years, from the first needed year to the last.
  - Each settled fetched year is then stored.
  - The response is clipped to the requested range and rebuilt as a validated contract document.
- **No mixing:** years are combined only when source, feed, adjustment and dataset revision all match. If a stored year disagrees with the provider (for example, a new revision), the whole range is refetched and only the provider's data is served. Years stored under the new revision replace nothing; the old revision's records remain, and `read_exact` treats several revisions as ambiguous and falls back to the provider.
- **Point in time:**
  - A request with `as_of_utc` reuses a stored year only when every bar in it was available by the cutoff. That is exactly what the provider would return.
  - A year fetched under a cutoff is stored, without the cutoff, only when the cutoff hid nothing in it.
- **Weekly bars** are still aggregated from daily bars on request, so they benefit from the same cache without storing anything themselves.

## Consequences

- A daily or weekly chart costs one provider call for the current year (one page) instead of the whole history. Locally the daily cache for AAPL is 10 files and 176 KB for 2016–2025.
- The current year is always refetched, which is up to about 250 bars per load.
- Only the provider's raw (unadjusted) daily bars are cached today. If an adjusted series is added, its `adjustment` value keeps it apart.

## Alternatives considered

- **One file per session, like minute bars:** thousands of tiny files per symbol.
- **One file per symbol rewritten on every fetch:** rewrites history for every new day and breaks the immutable, content-addressed part rule.
- **Month chunks:** a fresher current period, but 12× the records and files, and no benefit while the current year is one page.

## Revisit when

- A provider charges per bar or the current-year fetch becomes slow.
- Adjusted (split/dividend) daily series are added, since revisions then change history.
