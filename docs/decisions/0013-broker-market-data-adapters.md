# ADR 0013 — Broker market-data adapters (KIS, Toss, Kiwoom, Meritz)

- **Status:** accepted (adapters built from official specs; live account checks pending). Amended 2026-10-17 to add Meritz at the user's request.
- **Date:** 2026-10-17

## Context

The user asked for four broker APIs as alternatives to Alpaca. On 2026-10-17, research using official sources only found:

| Broker               | US 1m bars by API                                                                                            | Key facts (official)                                                                                                                                                                                                                                                                                                                       | Not confirmed                                                                                       |
| -------------------- | ------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------- |
| 한국투자증권 (KIS)   | Yes: `GET /uapi/overseas-price/v1/quotations/inquire-time-itemchartprice`, TR `HHDFS76950200`                | Up to 120 records per call; page back with `KEYB`; about one month of history; local and KST times; REST limit 20/s; token issuance once per minute and each issuance sends a KakaoTalk notice; US quotes free real-time                                                                                                                   | Whether `xhms` marks the bar start; extended-hours inclusion; price adjustment; storage terms       |
| 토스증권 (Toss)      | Yes, since 2026-08-13: `GET /api/v1/candles`                                                                 | `timestamp` is the bar end; newest first; up to 200 per page via `nextBefore`; `adjusted` defaults to true; US data from some venues only (not NBBO); no-trade minutes omitted; past bars can change with late trades; 20/s chart limit; one valid token per client; IP allowlist; own-trading use only, no third-party sharing            | Extended-hours inclusion; token lifetime; local retention rules                                     |
| 키움증권 (Kiwoom)    | Yes: REST `POST /api/us/chart`, `api-id: usa06011`                                                           | Paging via `cont-yn`/`next-key` headers; prices as 4-decimal strings; `upd_stkpc_tp` 0/1; limit errors 1700–1702                                                                                                                                                                                                                           | Time zone and meaning of `cntr_tm` (the example shows duplicates); page size; history depth; limits |
| 메리츠증권 (Meritz)  | Yes: `GET /market/v1/overseas/candles/minutes` (TR `ohhts_eapi_005r`), public beta 2026-10-12, GA 2026-10-26 | **Only the latest 40 bars**, newest first, no date or paging parameter; `korea_date`/`korea_hour` in KST; all errors are HTTP 500 with `rsp_cd` (`EGW00200` = per-second limit); every call needs a `mac_address` header; token via form with `scope=login`, `expires_in` in seconds; free API; own-investment use only, no redistribution | Whether `korea_hour` is the bar start; sessions; adjustment; token error codes; rate limits         |
| 미래에셋증권 (Mirae) | **No official API for US stock data**                                                                        | Only an overseas-futures API; a "Trading Open API" appears to be in development (job postings, July 2026)                                                                                                                                                                                                                                  | Everything; not implemented                                                                         |

## Decision

- **Four opt-in adapters:** `SIW_MARKET_DATA_PROVIDER=kis|toss|kiwoom|meritz`. Each is a separate source with its own `source_id` and feed scope (`kis-us`, `toss-us-partial-venues`, `kiwoom-us`, `meritz-us`). Series from different sources are never merged.
- **Read-only by construction:** every adapter uses `AllowlistedClient`, which only permits the token path and the chart path. Order endpoints share the same token, but any other method or path raises `BlockedEndpointError` before a request is sent.
- **Credentials:** keys came from environment variables only (superseded by [ADR 0014](0014-provider-key-registry.md): keys are now registered on the Keys screen and kept in the OS keychain). Tokens are kept in memory, refreshed on expiry, and reissued at most once when a call is rejected (KIS `EGW00121/EGW00123`, Toss `expired-token`/`token-revoked`, Kiwoom 401).
  - Tokens are never written to disk.
  - Two processes using Toss at once revoke each other's token. The single reissue recovers, but simultaneous use is not supported.
- **Conservative defaults for unconfirmed behaviour:**
  - Sessions default to regular hours. Expected minutes outside them are reported with the new settled reason `session_not_covered`, never as no trades.
  - Kiwoom refused to start without an explicit `SIW_KIWOOM_TIME_ZONE`. Since the 2026-10-19 live check (below), New York time is the default.
  - KIS treats `xhms` as the bar start unless `SIW_KIS_TIMESTAMP_IS_BAR_END=true`.
  - Duplicate minutes raise `ProviderDataError`, never a silent choice.
  - Adjustment is reported as `raw` only where unadjusted prices are requested explicitly (Toss `adjusted=false`, Kiwoom `upd_stkpc_tp=0`), and `unknown` for KIS and Meritz.
- **Meritz specifics:**
  - The required `mac_address` comes only from `SIW_MERITZ_MAC_ADDRESS`. It is never auto-detected and never shown in errors or `repr`.
  - Success is judged by `rsp_cd == "0000"`, not by the HTTP status.
  - When a full 40-bar page comes back, minutes older than its oldest bar are reported with the new settled reason `beyond_source_history`. This makes Meritz a recent-window alternative only.
- **Shared coverage builder (`broker_common.build_result`):** canonical bars with exact decimals, plus the same gap reasons as Alpaca: `provider_no_bar`, `not_yet_final`, `outside_entitlement`, `not_available_as_of_cutoff` and `session_not_covered`.
- **Live check:** `python -m services.market_data.broker_check --provider <name>` makes one 30-minute request. It prints the first raw record as the broker sent it, next to the derived UTC bar start, so the time semantics can be confirmed. Capabilities stay `verification_status: documented` until a check passes.

## Live checks (2026-10-19)

Run with the user's keys, registered on the Keys screen (ADR 0014), for the 2026-10-16 regular-session open:

- **Toss:** SPY 30/30 minutes, no gaps. The first raw record is the newest bar, `timestamp` `23:00 KST` = 14:00Z, which is the end of the last requested minute. This matches the documented bar-end meaning. Capabilities are now `integration_tested`.
- **Kiwoom:**
  - **Time zone:** AAPL 13:30–13:45Z. Read as New York time, 14 of 15 closes equal Alpaca SIP at the same bar start; read as Seoul time, no bar falls in the window. So `cntr_tm` is New York time marking the bar start, and that is now the default.
  - **Volume:** about 81% of SIP.
  - **Exchange code:** SPY (NYSE Arca) failed with `stex_tp=NA` (1903, no such symbol) and returned 30/30 with `NY`, so ARCX now maps to `NY`.
  - **Late bars:** the newest raw bar was 20:28 ET. Bars after the regular post-market close may be included, but sessions stay regular-only until that is examined.
  - Capabilities are now `integration_tested`.
- **Not checked:** KIS and Meritz (no key registered yet).

## Alternatives

- **Use unofficial wrappers or scrape the trading apps:** forbidden by the project rules and the brokers' terms.
- **Guess the Kiwoom time zone (KST or ET):** a wrong guess shifts every bar by 13–14 hours.
- **Implement Mirae from blog posts:** no official spec exists.

## Trade-offs

- **Unverified until each check:** each adapter is unverified until the user runs its check with their own keys. Some defaults, such as regular hours only, may hide data a broker actually provides.
- **Toss terms:** data may be used only for the account holder's own trading. Local storage for that purpose is assumed and not confirmed, and the data must never be shared.
- **KIS history:** about one month of minute history makes KIS a recent-window alternative, not a deep-history source.

## Evidence/verification

- `tests/market_data/test_brokers.py`:
  - each adapter's documented request shape (paths, headers, parameters or body);
  - timestamp conversion (ET; KST for Kiwoom; bar-end for Toss);
  - exact decimals and paging (KIS `KEYB`, Toss `nextBefore`, Kiwoom headers);
  - token reissue on expiry or revocation;
  - typed errors with no secrets in messages;
  - duplicate minutes rejected;
  - sessions not covered;
  - order endpoints blocked;
  - selection requiring each broker's keys;
  - contract-valid capabilities and instruments.
- Meritz: KST conversion, form token with `scope=login`, the `mac_address` header, the 40-bar history floor, `rsp_cd` error mapping and MAC validation.
- Live checks: **pending** (the user's keys are needed).

## Revisit when

- each broker's check is run: record the time semantics and sessions, then raise the capabilities to `integration_tested`;
- Mirae Asset publishes an official Open API covering overseas stocks;
- Meritz reaches general availability (2026-10-26) or adds paging or date parameters to minute bars;
- a broker publishes rate limits, history depth or storage terms.
