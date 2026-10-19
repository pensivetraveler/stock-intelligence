# ADR 0012 — Alpaca as the first real market-data adapter (prototype)

- **Status:** accepted
- **Date:** 2026-10-17

## Context

On 2026-10-17 the user decided the provider order:

- **First:** prototype the first real market-data adapter with Alpaca.
- **Then:** connect four broker APIs as alternatives. `EXECUTION_PROCESS.md` lists Toss, KIS and Kiwoom, plus Mirae Asset if chosen and verified.
- **Storage:** keep data locally for now, and consider a NAS or a cloud server later.

According to the user and Alpaca's documentation, the free plan serves consolidated SIP history except for the latest 15 minutes, and real-time data only from IEX. The user believes the broker APIs have no such limit; that is **unverified** until each account is tested.

The project rules forbid hard-coding either paid candidate (Alpaca or Massive) as the default.

## Decision

- **Opt-in adapter:** `AlpacaMarketDataProvider` is used only when `SIW_MARKET_DATA_PROVIDER=alpaca`. Synthetic data stays the default.
  - This is a prototype order chosen by the user, not a canonical vendor selection. Massive stays an unselected candidate.
- **Read-only, one endpoint:** it calls only `GET /v2/stocks/bars` (1Min, `adjustment=raw`, explicit `feed`, pages of 10,000 rows with `next_page_token`). No trading endpoint is used.
  - Credentials came only from `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` (superseded by [ADR 0014](0014-provider-key-registry.md): keys are registered on the Keys screen). They are sent as headers, kept out of `repr`, and never logged or echoed in errors.
- **Exact prices:** responses are parsed with `parse_float=Decimal`, so prices never pass through binary floats.
  - Bars that violate the contract (high below open, duplicate minutes) raise `ProviderDataError`. Nothing is fixed up silently.
- **Plan delay:** with `feed=sip`, requests end 15 minutes before now (`SIW_ALPACA_DELAY_MINUTES`). The excluded minutes are reported as `outside_entitlement`.
  - `available_at_utc` is estimated as the bar's end plus that delay.
  - A bar is final once it is `SIW_ALPACA_FINALITY_MINUTES` (default 15) old; younger bars are `provisional`.
- **No-trade minutes:** Alpaca omits minutes without trades. Coverage gains reasons that tell these cases apart:
  - `provider_no_bar`: settled, normally no trades in that minute;
  - `not_yet_final`: too recent to call a gap;
  - `outside_entitlement`: excluded by the plan.

  `provider_no_bar` minutes count as settled when deciding whether to reuse the cache (`SETTLED_MISSING_REASONS`). They also reduce the expected source minutes of an aggregate bucket, so a 5m bar is not marked partial just because some minutes had no trades. No OHLCV is ever invented for them.

- **Revision and identity:**
  - Alpaca exposes no data revisions, so each feed has one revision (`alpaca-sip-raw-r1`). Final bars are cached and not refetched, and the cache refuses overlapping results that disagree.
  - `source_id` and feed scope name the feed (`alpaca-sip` or `alpaca-iex`). IEX volume is not consolidated volume, and the feed is shown in every coverage lineage.
  - Instruments come from `SIW_ALPACA_SYMBOLS` (SYMBOL:MIC). Symbol history and renames are not handled yet.
- **Error mapping:**
  - The API returns 502 `provider_auth_failed` or `provider_data_invalid`, and 503 `provider_rate_limited` or `provider_unavailable`.
  - Backfill retries only unavailable or rate-limited errors. It fails at once with `provider_auth` or `provider_data`.
- **Account check:** `python -m services.market_data.alpaca check` makes one small request with the user's keys and prints counts and missing-reason types, never secrets.

### Live long-run check, 2026-10-17

The run used the user's keys, loaded from `.env` inside the command and never printed. Data went to a scratch directory, not the repository.

- **Full-day backfill:** SPY on 2026-10-16, 04:00–20:00 ET (960 minutes), feed `sip`, page size forced to 100.
  - 3 chunks in 10 HTTP requests, all 200, in 6.8 s, so pagination was exercised live.
  - 921 bars returned (pre 294, regular 390, post 237). The other 39 minutes were `provider_no_bar`, all in pre-market and after-hours. Every bar was final.
- **Cache reuse:**
  - The full day, a range crossing chunk boundaries (14:00–15:00Z) and the regular-session 5m aggregate (78 of 78 buckets, none partial) were all served from cache with 0 upstream requests.
  - This required letting range composition accept settled `provider_no_bar` gaps, added in this change with a test.
- **IEX vs SIP:** 13:30–14:00Z had 30 bars on both feeds. IEX volume was 108,395 against 4,518,499 on SIP (2.40%), and the first close differed (772.9 vs 772.92). This confirms that IEX is a partial-venue feed.

## Alternatives

- **Use `feed=iex` by default:** real-time, but only about 2–3% of volume is traded on IEX, so its bars differ from the consolidated tape. SIP history matches the paid plan, so `sip` is the default and IEX stays an explicit choice.
- **Parse floats and round:** this risks silent price changes; `Decimal` parsing is exact.
- **Treat missing minutes as gaps to re-fetch:** every request for sparse symbols would go upstream again, and aggregates would always look partial.

## Trade-offs

- **Estimated publication time:** the true time is unknown, so replay-style cutoffs on Alpaca data are approximate by the configured delay.
- **Range limit:** one adapter request covers at most 1,440 minutes; Collector backfill chunks longer ranges.
- **Storage location:** the cache stays on local disk. ADR 0002 and ADR 0004 rely on a local filesystem (SQLite plus OS locks), so a NAS or cloud server needs a remote Collector or service boundary (Phase 7), not a shared network folder.
- **Live verification:** the live checks in Evidence passed. Two things are still untested: the rate limit (not provoked on purpose) and the live edge of the 15-minute window (it needs an open market). Automated tests use responses shaped like the documented API.

## Evidence/verification

- `tests/market_data/test_alpaca.py`:
  - canonical mapping with exact decimals;
  - documented endpoint, headers and parameters;
  - the SIP 15-minute exclusion, IEX provisional bars and pagination;
  - the point-in-time cutoff;
  - typed failures (401/403/429/5xx/timeout/non-JSON/duplicate/invalid OHLC) with no secret in any message;
  - query validation before any request;
  - no-trade minutes in aggregates and in cache reuse;
  - backfill failing fast on rejected credentials;
  - environment configuration and provider selection;
  - API error mapping.
- The UI explains an instrument the current provider does not offer instead of requesting it.
- **Live account check, 2026-10-17:** the user ran `python -m services.market_data.alpaca check --symbol SPY` with their own keys.
  - Request: `alpaca-sip`, session 2026-10-16, 13:30–14:00Z.
  - Result: 30 of 30 expected minutes, no missing intervals. The first bar's prices were preserved exactly as sent (`773.24`/`773.25`/`772.5`/`772.92`, volume `474220`).
  - The output contained no secrets.
  - Capabilities now report `verification_status: integration_tested`. `entitlement_status` stays `unknown`, because entitlement differs by feed and range (for example the latest 15 minutes of SIP).

## Revisit when

- the 15-minute window and rate limiting are checked during a live session;
- a paid plan with real-time SIP is used (set the delay to 0);
- broker API adapters arrive (each is a separate source; series are never merged silently);
- data must live on a NAS or cloud server.
