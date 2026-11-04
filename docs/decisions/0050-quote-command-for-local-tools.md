# ADR 0050 — A read-only quote command for other local tools

- **Status:** accepted (2026-11-04)
- **Date:** 2026-11-04
- **Related:** [ADR 0035](0035-alpaca-default-us-provider.md) (Alpaca default), [ADR 0046](0046-shared-data-space.md) (shared data with the dilution analyzer)

## Context

The stock dilution analyzer adds a Price Map (request `#stock-quote-1`, user decision 2026-11-04). It sorts securities into ITM, OTM and price-independent groups by the current price. For ITM securities it compares the share amount with trading volume. The user decided that current prices come from SIW.

ADR 0046 says SIW alone fetches and stores prices, and that no other program reads SIW's bar cache yet. The analyzer needs:

- a last price;
- the last regular close;
- today's volume;
- an average daily volume (ADV);
- the split basis of all of these.

## Decision

- **Command:** `scripts/siw-quote quote --ticker T [--as-of YYYY-MM-DD] [--adv-days 20]` runs `python -m services.market_data.quote` from the repository with its `.env` and prints one JSON document (`schema: siw_quote/0.1`).
- **Source:**
  - It reads Alpaca bars live through the first enabled Alpaca key (Keys screen), the same adapter the day-market bars use.
  - It does not read or write SIW's bar cache, so ADR 0046's cache question does not apply.
  - Only `GET /v2/stocks/bars` and the corporate-actions read are called. There is no order or account code on this path.
- **One share basis:**
  - Every price and volume is split-adjusted as Alpaca has it on the retrieval date (`split_basis.reflects_splits_through`).
  - Splits after the `--as-of` date are listed so the analyzer can convert.
- **Values:**
  - `last` is the close of the latest 1-minute bar of the current extended-hours day, not a trade or quote.
  - `today_volume` sums today's 1-minute bars by session.
  - `adv` sums regular-session 1-minute bars per session. `adv_daily_bar` gives Alpaca's 1Day volumes alongside, because the two differ (AAPL 2026-10-30, 5 days: 28.8M vs 45.9M) and which one the analyzer should use is its decision.
  - Prices and shares are decimal strings. A value that cannot be known is null, never 0.
- **Exit codes:**
  - 0: answered
  - 2: usage, including a non-trading or not-yet-closed `--as-of`
  - 3: no bars for the ticker
  - 4: rate limit
  - 5: read failed
  - The reason goes to stderr. Keys and account data never reach the output.

## Alternatives considered

- **An HTTP endpoint on the SIW API:** the analyzer would need the API running. A command works whether or not an app is open.
- **Reading SIW's bar cache:** ADR 0046 holds that back until the cache decision.
- **Raw (unadjusted) prices with a split list only:** every consumer would have to adjust volumes itself. Split-adjusted values plus the basis date is what the analyzer asked for.

## Consequences

- Every call reads Alpaca. A 20-day ADV is about three requests.
- Delisted tickers are not mapped to their old company (`asof`); the command reads the ticker as it is today.
- **Licence (unverified):** ADR 0035 left Alpaca's market-data terms unverified. Showing the data to the account holder on this machine is within personal use as generally described. Secondary sources say Alpaca does not allow redistribution. The primary terms were not read. The analyzer must stay local and must not publish the values.

## Revisit when

- The analyzer needs a real last trade or quote rather than a 1-minute close.
- Data is shown to anyone other than the account holder.
- SIW's bar cache becomes readable by other programs (ADR 0046).
