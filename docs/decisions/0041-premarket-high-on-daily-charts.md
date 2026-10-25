# ADR 0041 — Pre-market high on daily charts

- **Status:** accepted
- **Date:** 2026-10-26

## Context

The user asked for a way to show the pre-market high on daily charts, then chose the front-end-only option.

- **Daily bars:** a daily bar covers the regular session only (`services/market_data/daily.py`). Its high is not the pre-market high.
- **Minute bars on daily charts:** until now no feature fetched them. ADR 0039 (smaller-timeframe indicators) and ADR 0040 (`request.security`) both list it under "Revisit when".
- **What the API already offers:** `GET /v1/market/bars` aggregates `Nm` buckets counted from each session's start and cuts the last bucket at the session's end (ADR 0034). A `960m` request over a session's pre-market window (`pre_open_utc` to `regular_open_utc`) therefore answers with exactly one `session: pre` bar. Its high is the pre-market high, with the source, coverage, finality and `as_of_utc` rules of any aggregated bar.

## Decision

### Data

- **Requests:** for the chart's day and the 59 trading sessions before it (60 in all), one `960m` request per session over its pre-market window. Requests run four at a time, newest first.
- **No backend change:** the existing endpoint and its cache are used. The pre-market minutes come from the chart's provider, like any extended-hours bars.
- **Never merged:** the values are kept apart from the daily bars. Indicators, markers, replay bounds and backtests keep using the daily bars only.
- **Placing on the chart:** a `pre` bar ends at the regular open, which is where that session's daily bar starts. A value is drawn only on the daily bar with that start time.
- **Pre-market session:** 04:00–09:30 New York time, from the extended-hours policy (ADR 0003). The US day market (BOATS, ADR 0030) is a separate venue and is not included.

### Chart

- **Switch:** a "PM high" / "프리마켓 고가" toolbar button, shown on US daily (`1d`) charts only. It is per chart and saved with the layout (`premarketHigh: true`). Layouts without it look as before.
- **Bar marks:** a short amber level across each daily candle. The marks are not joined from day to day, so they do not read as one continuous indicator.
- **Price line:** the newest daily candle's pre-market high also gets a price line labelled "PM High" with its value on the price scale.
- **Not final:** while the pre-market runs (or its bar is not yet final), the mark and the line are dashed and the legend says "not final".
- **Legend:** the hovered day's pre-market high is shown beside the OHLC values.
- **Price scale:** the marks in view widen the price scale, so a pre-market high above the day's high stays visible.
- **No trades:** a session whose pre-market had no trades has no bar, so it gets no mark. The note under the chart says how many of the 60 sessions have a value.

### Look-ahead and replay

- The pre-market high becomes final at the regular open, which is the start of the daily bar it is drawn on. It never shows on an earlier bar.
- In replay, a mark appears only once its pre-market bar has closed by the replay time. While a session's pre-market is still running in replay, that day has no mark.

## Alternatives considered

- **A backend endpoint for per-session statistics (pre-market high, low, volume):** reusable by backtests and Agents, with "no trades" and "no data" told apart in one place. More work (schema, contract tests, docs). It is the next step if those uses appear.
- **Allowing minute `request.security` on daily charts (Pine):** general, but needs a way to pick the pre-market out of the minute bars and opens the wider question in ADR 0040.
- **Putting the pre-market into the daily bar's OHLC:** rejected. Daily bars would no longer mean the regular session, and two sessions would share one bar without saying so.

## Remaining risks

- **First load cost:** each session's pre-market (up to 330 one-minute bars) is fetched from the provider the first time. Sixty sessions are about 20,000 minute bars before the cache is warm.
- **Shown range:** only the newest 60 sessions get values. Older daily bars, including those loaded with "older bars", have none.
- **Providers:** a provider that serves no pre-market minute bars gives no marks. The note then shows 0 sessions.
- **Not checked live:** checked on the synthetic provider only. Not checked against Alpaca SIP data.

## Revisit when

- Backtests, strategies or Agents need the pre-market high: move it to a backend endpoint with its own contract.
- Pre-market highs are wanted for more than 60 sessions or on weekly charts.
- The day market should count as part of the pre-market.
