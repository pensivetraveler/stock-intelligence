# ADR 0003 — US equity calendar, session policy and minute aggregation

- **Status:** accepted; calendar version and loaded range amended by ADR 0005
- **Date:** 2026-10-12

## Context

Phase 1C must classify US equity bars without UTC/DST assumptions, preserve early closes and holidays, and aggregate canonical 1-minute bars without crossing sessions or hiding missing data. Extended trading availability varies by venue, feed and broker, so a calendar schedule does not prove data entitlement or actual coverage.

## Decision

- Use `exchange-calendars` 4.13.2 for regular US equity sessions, holidays and early closes. The library officially supports Python 3.10 and resolves the `XNAS` alias to its canonical `XNYS` calendar.
- Store and display in UTC while deriving session dates and extended boundaries with `America/New_York` and IANA timezone data. Never align intraday buckets by UTC modulo alone.
- Version the current extended-hours policy as `us-equity-extended-hours-v1-2026-10-12`: pre-market 04:00–09:30 ET, regular according to the exchange calendar, post-market until 20:00 ET on full days and 17:00 ET on listed early-close days. This policy classifies time; it does not claim a provider or broker supplies those periods.
- Keep the announced Nasdaq 23/5 schedule outside this policy. It was future/pending as of the decision date and requires a new overnight session contract, provider capability evidence and a policy-version change before implementation.
- Aggregate 1m into 5m/15m/30m/60m relative to each session start. Never combine pre, regular and post bars. Cap the last bucket at the session boundary, so the final regular 60m bucket may contain 30 expected minutes.
- Add `quality` and structured `lineage` to every canonical `PriceBar`. Derived bars record source revision, source/observed counts, calendar ID/version and a deterministic derived dataset revision. Missing input yields `partial`; unfinished input yields `provisional`; neither is presented as final.
- Compute expected Coverage minutes only within classified trading sessions. Holidays and closed periods produce empty Coverage, not fabricated missing bars.

## Evidence and references

- `exchange_calendars` source and Python support: https://github.com/gerrymanoim/exchange_calendars
- XNAS alias mapping: https://github.com/gerrymanoim/exchange_calendars/blob/master/exchange_calendars/calendar_utils.py
- NYSE holiday and early-close schedule: https://www.nyse.com/markets/hours-calendars
- Nasdaq regular and extended hours: https://www.nasdaq.com/market-activity/stock-market-holiday-schedule

## Consequences and trade-offs

- The dependency adds pandas/numpy and startup cost, but avoids hand-maintained DST and holiday rules.
- XNAS regular scheduling currently shares XNYS through the library alias. If venue calendars diverge, the alias must be replaced with venue-specific evidence and tests.
- Partial aggregates retain honest uncertainty but may not match charting products that silently omit or fill missing minutes.
- Existing Phase 1A synthetic bars gain required lineage fields; stale pre-Phase-1C local synthetic cache should be rebuilt rather than silently upgraded.

## Reconsider when

- Nasdaq overnight/23-hour trading becomes effective and required by a verified feed;
- NYSE and Nasdaq regular schedules diverge;
- provider-specific extended sessions or auction bars require separate session types;
- calendar library behavior/version changes historical schedules;
- tick or halt-aware aggregation is implemented.
