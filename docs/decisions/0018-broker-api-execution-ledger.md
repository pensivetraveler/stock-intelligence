# ADR 0018 — Broker-API execution ledger (Toss), one record per order

- **Status:** accepted (implemented; checked live with the user's Toss account into a scratch journal)
- **Date:** 2026-10-20

## Context

Stage 5 of the direct Toss integration (ADR 0017) adds account data. On 2026-10-20 the user decided that fills are stored in a ledger, while holdings and buying power are only displayed.

Toss does not report individual fills. For each order it returns the total filled quantity, the average fill price, commission, tax and a single `filledAt`. The project rules forbid inventing execution order or times that the source does not give.

## Decision

- **A fourth ledger, `broker_api`.** It is kept separate from manual entries, statement imports and scenarios, and is never summed with them.
  - It lives in the journal store (migration `0003_broker_order_executions`).
  - Its record type is the new `broker_order_execution` contract.
- **One record per closed order with fills.**
  - Each record holds the filled quantity, average price, commission, tax and their sum as `fees`.
  - The record is marked `granularity: order_aggregate`, and its time `time_meaning: reported_last_fill`.
  - Orders without fills are skipped, and so are open orders, which can still change.
- **Append-only, synced on demand.** `POST /v1/brokers/toss/sync` pages through every account's closed orders.
  - The `execution_id` is derived from the account reference and the order ID, so a repeat sync stores nothing new.
  - If an already stored order is later reported differently, it is listed under `conflicts` and the stored record is kept.
- **Only US orders, for now.** USD orders resolve to instrument IDs through the configured instruments and the symbol directory, so venues stay Alpaca's (ADR 0016).
  - KRW orders are counted as `not_us` until the Korean-market stage defines Korean instrument IDs.
  - Symbols with no known listing (for example, delisted ones) are counted as `unknown_instrument`.
- **Account view, display only.** `GET /v1/brokers/toss/account` returns `broker_account_snapshot` and is never stored. It contains:
  - accounts with only the last four digits of the account number;
  - a holdings summary and items;
  - cash buying power in USD and KRW, and commission rates;
  - open orders and open conditional orders.

  A part that fails shows in the account's `errors` field, and the rest still displays. `GET /v1/brokers/toss/sellable` returns one stock's sellable quantity on demand.

- **No full account numbers or secrets.** An account is referenced as `toss-<sha256(client_id)[:16]>-<accountSeq>`.
- **No order writes.** The session can only send GET requests to read endpoints, plus the token POST (ADR 0017). The screen says that the account view is read-only.
- **Not exposed yet:** the order and conditional-order detail endpoints (`/orders/{id}`, `/conditional-orders/{id}`) are on the read allowlist, but the list responses already carry the same data, so nothing calls them yet.

## Live check (2026-10-20, scratch journal, then deleted)

- **Account view:** one BROKERAGE account, with no errors in any part.
- **Sync:** 3,862 closed orders were seen across 39 pages.
  - 2,021 filled US orders were stored, all with a fill time.
  - Skipped: 520 without fills, 1,112 KRW orders and 209 symbols with no known listing.
  - A second sync stored 0 new records and found 2,021 already stored.
- **Page cap:** with this much history, the cap was raised to 200 pages (20,000 closed orders per account).

## Consequences

- P&L from this ledger uses average prices per order. An order filled in several pieces counts as one trade at its average price, so it is accurate in total but not per fill.
- A full sync re-reads the whole closed-order history (about 8 seconds for about 3,900 orders at the documented 5 requests per second). An incremental `from` date is a later refinement.
- The user's real journal is filled only when the user presses **Sync fills** in the Account panel.

## Alternatives considered

- **Reuse `broker_confirmed_execution`:** that contract implies per-fill records and USD-only P&L semantics. Storing order aggregates in it would overstate the detail.
- **Store holdings snapshots:** the user chose display only, and snapshots would need their own point-in-time design.

## Revisit when

- Toss exposes per-fill executions. Each one would then be stored as its own record.
- The Korean-market stage lands, so KRW orders can be stored.
- Another broker's account API is connected. It would get the same ledger with its own `broker_id`.
