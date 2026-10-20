# ADR 0016 — Ticker search over all US-listed stocks

- **Status:** accepted (implemented; checked live with Alpaca)
- **Date:** 2026-10-20

## Context

Charts could only show the few instruments configured in `SIW_ALPACA_SYMBOLS` (SPY, AAPL, MSFT by default), and the search box only filtered that list. The user asked for ticker search and chose to search every US-listed stock, rather than only the configured list.

## Decision

- **Source:** the active provider's asset list. For Alpaca this is `GET /v2/assets?status=active&asset_class=us_equity` on the trading API.
  - Live and paper keys work on different hosts, so `api.alpaca.markets` is tried first and `paper-api.alpaca.markets` after a 401/403.
  - Only that read-only path is called there: never orders or account data.
  - Exchanges map to MICs (NASDAQ→XNAS, NYSE→XNYS, ARCA→ARCX, AMEX→XASE, BATS→BATS). OTC and crypto assets are left out, because the exchange calendar covers listed US equities only.
- **Directory:** `services/market_data/symbol_directory.py` keeps the list in `SIW_DATA_DIR/symbols/<source_id>.json`. On 2026-10-20 that was 13,228 symbols and 1.2 MB.
  - **Refresh:** the list is refreshed when it is more than a day old.
  - **Failed refresh:** the old copy is served marked `stale`, and the next attempt waits 5 minutes. With no copy at all, the search answers 503.
  - **Search order:** exact ticker, then ticker prefix (shorter first), then a word of the company name that starts with the query, then any match in the company name.
- **API:** `GET /v1/market/symbols?q=&limit=` returns the new `symbol_search_response` contract, whose results are ordinary instrument listings plus the company name.
  - It answers 404 `symbol_search_unavailable` when the provider has no directory or `SIW_DATA_DIR` is unset.
  - The journal and the CSV import also accept directory instruments, read from disk only, so recording a trade never triggers a fetch.
- **Charting:** the Alpaca adapter accepts any listed-equity ID of the form `us:<symbol>:<xnas|xnys|arcx|xase|bats>:2026`, not only configured ones, because Alpaca market data is keyed by symbol alone. Configured symbols keep working as before.
- **Screen:** the symbol search box lists matching configured symbols first, then directory results with their company names. It waits for a 250 ms pause in typing before searching.
  - A picked stock is added to the watchlist, where it can be removed (×).
  - Picked stocks are remembered on this device (`siw.addedInstruments`). They are a per-device convenience, not shared or authoritative state.

## Consequences

- **Venue not checked by Alpaca:** the adapter trusts the MIC inside the ID. IDs come from the directory, but a hand-made ID with the wrong venue would still chart the right symbol under the wrong venue label.
- **Broker adapters:** brokers (KIS, Toss, Kiwoom, Meritz) still accept only their configured symbols. A stock picked from search shows "not offered" when a broker is the active provider.
- **Renames and delistings:** the identity namespace (`:2026`) is unchanged. If a symbol is renamed or delisted, its added entry stays until the user removes it.

## Alternatives considered

- **Configured symbols only:** simplest, but every new stock needs an `.env` edit and an API restart.
- **Query the provider on every keystroke:** no local copy, but one request per keystroke against a rate-limited key, and no offline fallback.
- **A bundled static symbol file:** this has no provider dependency, but goes stale and has unclear redistribution rights.

## Revisit when

- A broker becomes the main provider. Its adapters would then need the same dynamic symbol resolution, with each broker's exchange codes.
- Symbol changes need history (point-in-time identity), for example for backtests across renames.
