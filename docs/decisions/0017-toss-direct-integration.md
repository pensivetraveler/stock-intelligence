# ADR 0017 — Toss data through one shared read-only session

- **Status:** accepted. Stages 1–3 are implemented; stages 4–6 and full Korean-market support are planned.
- **Date:** 2026-10-20

## Context

The user asked for all Toss Securities data in the app. They compared a third-party MCP server (`scjang01/tossinvest-mcp`) with building the integration directly, and chose the direct route.

The deciding constraint is that the user can have only one Toss key, and Toss keeps one valid token per client: a new token revokes the old one. Two programs on one key would keep invalidating each other, and MCP data would not reach the app's contracts, cache or ledgers.

On 2026-10-20 the user decided:

- every documented read is in scope, but order writes never are;
- Korean stocks get full chart support, as a separate stage;
- fills are stored in the broker-confirmed ledger, while holdings and buying power are only displayed;
- work proceeds in stage order.

## Decision

- **One session per key** (`services/market_data/toss_session.py`). `session_for(credentials)` returns one `TossSession` per key for the whole process.
  - The chart adapter, live quotes and, later, account views share its token. A key is never used to issue two tokens.
- **Read-only allowlist.** All 34 documented GET endpoints of OpenAPI 1.2.24 are allowed, each tagged with its rate-limit group.
  - Order and conditional-order create, modify and cancel endpoints are not on the allowlist, and the session can only send GET (plus the token POST).
  - Path values are checked against `[A-Za-z0-9._-]`, and account endpoints must carry `X-Tossinvest-Account`.
- **Rate limits.** Every request waits for its group's documented requests per second, per key: ACCOUNT 1/s, MARKET_DATA 15/s, MARKET_DATA_CHART 20/s, and so on. ORDER_INFO uses its stricter 09:00–09:10 KST peak limit at all times.
- **Daily bars (stage 2).** Toss `interval=1d` with `adjusted=false`, paged with `nextBefore`, feeds the shared daily path (ADR 0015). This includes the yearly cache and weekly aggregation.
  - Live check: US daily candles are stamped at midnight New York written in Korean time, so the session date is read in New York time.
  - AAPL 10/15, 10/16 and 10/19 matched Alpaca SIP open, high, low and close exactly. Volume was about 0.4% lower, because Toss covers some venues only.
- **Listed symbols.** The Toss adapter now accepts any listed US equity ID from ticker search, like Alpaca (ADR 0016).
- **Live quote (stage 2).** `GET /v1/market/quote` returns the new `market_quote` contract: last price, order book, recent trades (30) and daily price limits (null for US stocks, which Toss refuses).
  - It reads the first enabled real-account Toss key on the Keys screen, whichever market-data provider is active.
  - Nothing is stored, and responses are `no-store`.
  - The Live quote panel refreshes every 5 seconds and pauses during replay.

## Stage 3 — stock and market info (implemented 2026-10-20)

- **Stock info:** `GET /v1/market/stock-info` returns the `stock_info` contract. It holds Toss's reference data (Korean and English name, ISIN, Toss market label, security type, status, listing dates, shares outstanding, leverage) and the active buying warnings.
- **Exchange rate:** `GET /v1/market/exchange-rate` returns the `exchange_rate` contract with Toss's USD/KRW reference rate. It is display only, and real trades may use another rate.
- **Market hours:** `GET /v1/market/broker-calendar` returns the `broker_market_calendar` contract with Toss's US or KR hours for the previous, current and next business day.
  - Each US session is compared with the local calendar the charts use.
  - Live check: Toss's pre-market and regular hours agree with the local calendar. Its after-hours session ends at 19:50 ET instead of 20:00, and the screen shows that difference rather than changing either calendar.
  - The Toss "day market" (20:00–04:00 ET) has no local counterpart.
- **Korean names in ticker search:** the symbol directory adds Toss's Korean names (`stocks/all` for NASDAQ, NYSE and AMEX, fetched daily), and `symbol_search_response` results carry `local_name`.
  - An exact Korean name ranks first, like an exact ticker.
  - A list saved before names existed is refreshed once per process.
- **Venues stay Alpaca's:** Toss's US market labels are not listing exchanges. SPY, listed on NYSE Arca, appears under Toss "AMEX", and `US_ETC` holds OTC stocks. Toss therefore never decides the venue in an instrument ID.
- **Screen:** a "Stock info" panel shows the reference data, warnings, the rate and today's US hours with any differences.

## Planned stages

4. Research: rankings, market indicators, sectors and Korean trading trends.
5. Account (read-only): accounts, holdings, orders and conditional-order history, buying power, sellable quantity and commissions. Filled orders go into the broker-confirmed ledger with provenance.
6. Realtime WebSocket: trade ticks (stored apart from minute bars), the order book, and personal order events.

- Korean stocks with full charts: a KRX calendar, KRW and Korean sessions.

## Consequences

- Toss quotes come from some venues only, so the panel says so. The quote is not the consolidated market.
- If the user changes the Toss key, the old session stays in memory until the API restarts. It is unused but holds a token that has been revoked.

## Revisit when

- Toss changes its rate limits (the `X-RateLimit-Limit` header) or adds a demo server.
- A second Toss key becomes available. Spreading load across keys is still not done (ADR 0014).
