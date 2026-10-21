# ADR 0027 — Indicators on a larger timeframe

- **Status:** accepted
- **Date:** 2026-10-21

## Context

The user asked for indicators from a larger timeframe on a chart, such as a daily SMA on a 5-minute chart. Done naively, the current day's unfinished daily bar would leak into intraday values (look-ahead), which SYSTEM_SPEC forbids.

## Decision

- **Choices:** an indicator's settings offer the chart's own timeframe, or a larger one: daily and weekly on intraday charts, weekly on daily charts. They apply to built-in and formula indicators. Python indicators stay on the chart's bars, because the API computes them on the bars it is sent.
- **Data:** for each larger timeframe in use, the chart fetches those bars through the usual bar API. The range is the chart's span plus enough larger bars to warm up the longest look-back (three times it). Fetches are cached by request (instrument, timeframe, start and last day), so a finished request is never thrown away when the chart re-renders.
- **No look-ahead:**
  - only final larger bars are used, so today's forming daily bar is never used;
  - a chart bar takes the latest larger bar whose publication time (`max(interval end, available_at)`) is no later than the chart bar's own end;
  - the line therefore steps when a day or week closes and is published, never earlier;
  - in replay, the chart's bars stop at the replay time, so the same rule holds.
- **Warm-up:** larger-timeframe indicators warm up on their larger bars and add nothing to the chart's own warm-up.
- **Screen:** the legend shows the timeframe (e.g. "SMA 20 · 1D"). A failed fetch is shown on the chart, never drawn as a value.

## Alternatives considered

- **Aggregating the chart's own bars into days:** an intraday chart rarely holds enough days to warm up a daily indicator, and daily bars from the provider are authoritative.
- **Using the forming daily bar:** that is exactly the look-ahead the spec forbids.

## Revisit when

- Larger intraday timeframes on smaller ones are needed (e.g. 60m on 5m). That requires session-aligned buckets and a finality rule per bucket.
- Backtests should use larger-timeframe indicators. The same final-and-published rule would apply in services/backtest.
