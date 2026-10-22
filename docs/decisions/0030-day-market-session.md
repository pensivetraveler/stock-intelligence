# ADR 0030 — US day market (overnight session) from Alpaca's BOATS feed

- **Status:** accepted
- **Date:** 2026-10-22

## Context

The user asked whether Alpaca serves day-market quotes. In the US this session is called overnight; Korean brokers call it 주간거래 (day market). It trades on Blue Ocean ATS from 20:00 to 04:00 New York time. The user then asked for an ADR and an implementation.

Alpaca's documentation names two feeds: `boats` (Blue Ocean ATS) and `overnight` (derived from BOATS, 15 minutes delayed and adjusted to the bid-ask spread). Read-only checks with the user's paid key on 2026-10-22 (AAPL, 1-minute bars):

- **`feed=boats`:** answered 200 with 160 bars, every one between 20:00 and 04:00 New York time. The newest bar started one minute before the request, so the feed is real time on this plan.
- **`feed=overnight`:** refused with 400 "invalid feed: overnight".
- **`feed=sip`:** no bars between 20:00 and 04:00 New York time.

Until now:

- **Calendar:** every session (pre-market 04:00–09:30, regular, after-hours 16:00–20:00 New York time) lies inside one local date. Coverage, minute aggregation, caches and every broker adapter depend on that.
- **Kiwoom sync:** day-market orders were skipped (`time_unresolved`, 1,119 orders over five years; ADR 0029).

## Decision

### The session

- **Name:** `overnight`, shown as "Day market" / "데이마켓". US listings only.
- **When it runs:** the overnight session of US trading day D runs from 20:00 New York time on the previous calendar day to 04:00 on D, and belongs to D. Monday's session starts on Sunday evening. When D is not a trading day, there is no session into D.
- **Policy version:** `us-overnight-boats-v1-2026-10-22`.
- **Unverified:** the policy assumes Blue Ocean's schedule around US holidays and early closes. It has not been checked against Blue Ocean's own calendar.

### A separate data stream, not merged into the existing bars

- **Where the bars come from:** day-market bars come only from Alpaca `feed=boats`, through the new `GET /v1/market/overnight-bars?instrument_id=&timeframe=&from_date=&to_date=`. It covers at most 14 session dates per call.
- **Labels:** each bar has `session: overnight`, `source_id`/`source_feed_scope` `alpaca-boats` and dataset revision `alpaca-boats-raw-r1`.
- **Never mixed:** day-market bars are never stored in the SIP cache or merged into `/v1/market/bars`. The pre, regular and post sessions, their coverage, daily bars, weekly bars and backtests do not change. Mixing would join two venues' prints under one source label.
- **Bar sizes:** `1m`, or `Nm` buckets counted from the session's 20:00 start (the last bucket ends at 04:00), built from the 1-minute bars of that response. Daily bars do not include the day market.
- **Coverage:** one entry for every expected bucket. Buckets without trades are listed as missing, with `provider_no_bar` once final and `not_yet_final` before that. The day market trades thinly, so most minutes have no bar.
- **Timing:**
  - Publication delay is 0, as checked live.
  - Finality and `as_of_utc` work as for Alpaca SIP bars, so replay never shows a bar before its estimated availability.
- **Keys:** the endpoint uses the first enabled Alpaca key on the Keys screen, whichever provider serves the charts. Without one it answers 404 `quote_source_unavailable`.

### Chart

- **Session option:** a third choice, "+ Day market", next to "Regular / Extended" (US charts only). The main bars load as for Extended. The day market of each shown session date (up to the newest 14) is drawn as a second candle series in its own colors.
- **Indicators, markers and backtests:** they keep using the main bars only. Mixing venues inside one indicator series is the merging this ADR avoids.
- **Replay:** day-market candles are cut at the replay time like the others.

### Kiwoom day-market orders

- **Evidence:** live data from 2026-03-02 to 2026-07-09 (account suffix 2859). On 23 order dates with unplaced fills, every such fill's Seoul time was between 09:00 and 17:59. On every one of them (38 of 38 dates checked), the order numbers came before the date's first order placed in the 04:00–20:00 window. Kiwoom therefore files a day-market order under the US trading day that follows it, matching the session definition above.
- **Rule:** a time that does not fall in 04:00–20:00 New York time on `ord_dt` is placed in the overnight session of `ord_dt` when exactly one Seoul date puts it there. Otherwise it stays `time_unresolved`. Running the sync again stores these orders; already stored orders are unchanged.

## Alternatives considered

- **Adding `overnight` to the shared calendar's trading minutes:** every provider (Kiwoom, KIS, Toss, Meritz, SIP) would then report eight missing hours each day. Aggregation buckets and caches keyed by local session dates would also need a redesign. Too wide for a change on one venue.
- **`feed=overnight`:** refused by the API for this account.
- **Merging day-market bars into the main series:** one series, but two venues under one coverage manifest, and indicators computed across both without saying so.

## Remaining risks

- The holiday and early-close schedule is assumed, not verified (see above).
- **Thin trading:** AAPL showed about 80 bars per eight-hour night, so most buckets have no bar.
- **Plan:** BOATS access depends on the Alpaca plan. Another key may be refused (502 `provider_auth_failed`).
- **Shown range:** the chart shows the day market for the newest 14 session dates only.

## Revisit when

- Day-market bars should feed indicators or backtests: decide on a combined series with its own lineage and source labels.
- Alpaca's `overnight` feed, or a broker's day-market quotes, become available and should be compared with BOATS.
- Blue Ocean publishes a holiday schedule that differs from this policy.
