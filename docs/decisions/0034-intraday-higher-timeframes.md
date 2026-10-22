# ADR 0034 — Larger intraday timeframes for indicators

- **Status:** accepted
- **Date:** 2026-10-22

## Context

ADR 0027 let an indicator use daily or weekly bars on a smaller chart. It deferred larger intraday timeframes, such as a 60-minute SMA on a 5-minute chart, because those need session-aligned buckets and a finality rule per bucket. The user asked for all remaining features.

## Decision

- **Choices:** an intraday chart of N minutes offers 15, 30, 60, 120 and 240-minute bars larger than N, besides daily and weekly. Daily and weekly charts are unchanged.
- **Buckets:** the larger bars are the same session buckets the chart's own timeframes use. The bar service aggregates them per session from 1-minute bars, so a 60-minute bucket never spans the open, the close or two sessions.
- **Fetching:** one request per session, because a minute-bar request covers at most one session. The chart's sessions plus warm-up sessions for three times the longest look-back are fetched, in the chart's session scope (regular or extended). At most 120 sessions, the session API's limit.
- **No look-ahead:** the same rule as ADR 0027. Only final larger bars are used, and a chart bar takes the latest one published (interval end or `available_at`, whichever is later) by its own end. The forming 60-minute bucket is never used.
- **Caching:** requests are cached by instrument, timeframe, scope, chart day and session count.

## Alternatives considered

- **Aggregating the chart's own bars:** a 5-minute chart rarely holds enough history to warm a 60-minute indicator, and the bar service is already the source of every aggregate.

## Revisit when

- Backtests should use larger-timeframe indicators (ADR 0027's open point).
