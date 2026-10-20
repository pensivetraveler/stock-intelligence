# ADR 0019 — Toss realtime relay and a separate, lossy tick store

- **Status:** accepted (implemented; checked live for 20 seconds with scratch storage)
- **Date:** 2026-10-20

## Context

Stage 6 of the direct Toss integration (ADR 0017) covers the realtime WebSocket (AsyncAPI 1.2.2): trade ticks, order book updates and the user's own order events. The documented constraints:

- **Authentication:** the stream authenticates with the REST access token, sent in an `Authorization` header at the handshake. A browser cannot set that header, and the token must not leave the API process.
- **Limits:** at most 2 connections per account (a new one evicts the oldest), 100 topics per connection, and 5 declarations a second.
- **Subscriptions:** each declaration is one array that replaces the previous subscription set.
- **Keepalive:** the client must send a text `PING`, recommended every 60 seconds; Toss closes a connection after 180 seconds without one.
- **Data loss:** trade and order book frames have no sequence numbers and may be dropped. Order events are lossless only within one connection, so after a reconnect the orders must be resynced over REST.

On 2026-10-20 the user decided:

- received trade ticks are stored in a separate tick store;
- when an order closes, the broker ledger syncs automatically.

## Decision

- **One relay in the API** (`services/market_data/toss_stream.py`, `RealtimeHub`). The API keeps the only Toss connection and relays events to the app over `WS /v1/realtime`, where a client sends `{"symbols": [...]}`.
  - The connection runs only while at least one app client is connected.
  - It subscribes the union of the clients' symbols, at most 45, to `trade:us` and `orderbook:us`, plus `personal:order` for every account.
  - It sends a `PING` every 60 seconds, closes the old connection before reconnecting, and reconnects with exponential backoff (up to 60 seconds) and jitter.
  - A `server-shutdown` frame triggers a reconnect.
  - Each app client has a bounded queue; a slow screen drops its oldest events, never the Toss stream.
- **Origin check:** browsers do not apply CORS to WebSockets, so `/v1/realtime` refuses (close code 1008) any `Origin` that is not one of the app's allowed origins. Otherwise another site open in the same browser could read the user's order events.
- **Events:** the new `realtime_event` contract has five variants: `trade`, `orderbook`, `order`, `ledger_synced` and `status`.
  - Prices stay exact decimal strings and times are converted to UTC.
  - Trade and order book events go only to clients watching that symbol. Order, ledger and status events go to every client.
  - Order events carry an opaque account reference, never an account number.
- **Ledger auto-sync:** after a connect or reconnect, and after an order event with a closed status (or a `FILL`), the hub waits 3 seconds to collect a burst of events. It then runs the ADR 0018 sync for orders from the last 4 days (KST order dates) and announces `ledger_synced`. The sync stays append-only and idempotent.
- **Tick store** (`services/market_data/tick_store.py`): Parquet files at `data/ticks/source_id=…/symbol=…/session_date=<New York date>/part-….parquet`.
  - **Completeness:** every tick is marked `completeness: lossy_stream` and keeps both the exchange time and the time the app received it. Prices and volumes are exact strings.
  - **Writes:** ticks are buffered in memory and flushed every 10 seconds (and on shutdown) into new immutable files, written through a temporary name. A crash loses at most the unflushed buffer. Path components are percent-encoded.
  - **Not an input:** ticks are never turned into bars and never feed charts, indicators, replay or backtests. Bars keep their own sources.
  - **Reading:** `GET /v1/market/ticks?instrument_id=&session_date=` returns `tick_list_response`: the latest 5,000 ticks with the total count and a `truncated` flag.
  - **Scope:** only symbols on screen (visible charts) are subscribed, and only while the app is open. No retention limit for now; the folder can be deleted at any time.
- **Screen:**
  - The app keeps one reconnecting relay socket and requests the visible charts' symbols. It requests none during replay, which shows the past.
  - The Live quote panel overlays pushed trades and order book updates on its 5-second polling and shows whether it is live.
  - Order updates and ledger saves appear as a short notice, and the journal refreshes after `ledger_synced`.

## Live check (2026-10-20, about 10:00 ET, 20 seconds, scratch tick folder, ledger sync stubbed)

- 7 topics subscribed (AAPL, NVDA and SPY trades and order books, plus one account), 0 rejected.
- 50 trade events and 21 order book events arrived, all valid against `realtime_event`.
- 50 ticks were stored (AAPL 3, NVDA 28, SPY 19).
- The connect triggered one ledger resync request.

## Consequences

- Stored ticks are what this app saw while it was open, from Toss's partial-venue feed. They suit inspection and later study, not volume reconstruction; Toss itself warns against summing frames.
- The relay uses one of the account's two Toss WebSocket connections while the app is open.
- With the app open during market hours, ledger syncs happen without a click. They write only the user's own closed, filled orders, as ADR 0018 defines.

## Revisit when

- Tick volume or disk use grows: add retention or compaction (merging a day's parts into one file).
- More than 45 symbols need to be live at once: open a second connection, which uses the account's last one.
- Korean stocks get full support: subscribe `trade:kr`/`orderbook:kr` and store KRW ticks.
