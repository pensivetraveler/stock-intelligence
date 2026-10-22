# ADR 0029 — Kiwoom US order history in the broker ledger

- **Status:** accepted
- **Date:** 2026-10-22

## Context

On 2026-10-22 the user asked to import the trade history of their Kiwoom account (suffix 2859) or their Toss account. Toss executions were already synced into the broker ledger (ADR 0018). Kiwoom's REST API was checked live through the read-only `kiwoom-exec` MCP server.

- **ust21100** (거래내역): settlement lines with fees, but no order number and a batch processing time (the same `proc_time` on every row), so the lines cannot be matched to orders.
- **ust21180** (기간별 주문내역): every order in a date range, filled or not, with no fill time. One live month was 44 pages (1,318 orders).
- **ust21150** (일별 주문체결내역): one order date's orders with filled quantity, average fill price, status, order time and one fill time (`cntr_time`). It answers for past dates.

## Decision

### Source

- **One request per weekday:** `ust21150` with `query_tp=5` (filled orders), following `cont-yn`/`next-key`. A holiday or a day without orders answers `return_code` 20 with message code 571758, which is read as "no rows".
- **Allowlist:** only the token call and api-id `ust21150` can be sent. No order endpoint is reachable.
- **Key:** the first enabled real-account Kiwoom key on the Keys screen. The account number is never read or stored; the account is `kiwoom-<sha256(app key)[:16]>`.

### What is stored

- **One record per order:** each filled order becomes one `broker_order_execution` with `granularity: order_aggregate`, the order's total filled quantity and its average fill price (`cntr_uv`).
- **Fill time:** `cntr_time` is a single time per order, and the spec does not say whether it is the first or the last fill. It is stored with the new `time_meaning: reported_fill_time`.
- **Dates and time zones:** times are Seoul clock times, and `ord_dt` is the US (New York) trading date. One live order date held orders from 22:51 to 06:58 Seoul time in order-number order, which is 09:51 to 17:58 New York time on that date. A time is placed on the Seoul date that puts it inside 04:00–20:00 New York time on `ord_dt`. When no Seoul date does (for example daytime-session orders), the order is skipped and counted as `time_unresolved`, rather than dated by guess.
- **Order time:** the latest instant with the reported Seoul clock time that is not after the fill. This assumes day orders; an order resting for more than a day would be dated on the fill's day.
- **Fees:** not reported per order, so `fees`, `commission` and `tax` are `null` (the contract now allows null) and are never assumed to be zero. Ledger positions list such executions in `fees_unreported_execution_ids`, and their realized P&L leaves those fees out.
- **Idempotence:** `broker_order_id` is `<ord_dt>-<ord_no>`. A second sync reports `already_stored`, and a changed report is a conflict that keeps the stored record (as for Toss).

### API and screen

- `POST /v1/brokers/kiwoom/sync?since=YYYY-MM-DD&until=YYYY-MM-DD`: the default is the last year. One call covers at most 400 days and starts at most 5 years ago, so longer histories are synced in several calls.
- **Result:** the `broker_sync_result` now has `broker_id` `toss` or `kiwoom` and `skipped.time_unresolved`.
- **Screen:** the account panel has "Sync Kiwoom US fills" with an optional start date.

### Live check, 2026-10-22 (counts only)

- **Last year (2025-10-23 to 2026-10-22):**
  - 28,530 filled orders read and 23,844 stored, in 3 minutes 37 seconds;
  - 3,667 were skipped because their symbol is not in the symbol directory;
  - 1,019 were skipped as `time_unresolved`;
  - no conflicts.
- **Five years, in yearly calls:** 66,413 stored, 33,754 with unknown symbols and 1,119 `time_unresolved`. Two of the years had no orders. Per-year counts are in PROJECT_STATE.

## Alternatives considered

- **ust21180 to find the dates with fills, then ust21150 per date:** fewer daily calls, but ust21180 lists unfilled orders too and needed more than 200 pages for some months.
- **Fees from ust21100, matched by date, symbol, side and quantity:** guesswork without an order number, so it was not done.
- **Using the order time as the fill time:** it would invent a fill time.

## Remaining risks

- **Accounts combined in positions:** the broker ledger's positions combine all broker accounts per instrument, Toss and Kiwoom alike, so FIFO realized P&L can match a Kiwoom sell with a Toss buy. A per-account view is a follow-up.
- **Unknown symbols:** symbols missing from the directory (possibly delisted or renamed) are not stored.
- **Daytime-session orders:** these are not stored until their date rule is confirmed.
- **Domestic (KRW) history:** kt00007 and kt00015 are not synced yet.

## Revisit when

- Kiwoom documents the meaning of `ord_dt` and `cntr_time`, or offers per-fill times or per-order fees.
- Daytime-session orders need to be in the ledger.
