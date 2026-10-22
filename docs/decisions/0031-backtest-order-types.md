# ADR 0031 — Backtest limit and stop orders

- **Status:** accepted
- **Date:** 2026-10-22

## Context

Backtest orders could only fill at the next bar's open (ADR 0021); stop loss and take profit were the only price-triggered exits. The user put "limit/stop orders and walk-forward" next on the list.

## Decision

### Placing an order

- **Order types:** an order intent may carry `order_type` `market` (default), `limit` or `stop`.
- **Price:** a limit or stop needs exactly one price. It is either absolute (`limit_price` or `stop_price`) or `offset_pct` from the signal bar's close (−90 to +1000; e.g. −2 for a buy limit 2% below the close).
- **Expiry:** `expire_bars` (1–100, default 1) is how many eligible bars the order waits. An order not filled in time ends `expired`.
- **Rejection:** a bad type, price or expiry rejects the order (`invalid_order_type`, `invalid_order_price`, `invalid_expiry`).
- **Python strategies:** pass these fields in their intents.
- **Rules strategies:** may set `entry_order` and `exit_order` (`{type, offset_pct, expire_bars}`). When absent they stay market orders, so saved strategies keep their definition and fingerprint.
- **One order at a time:** a rules strategy still has at most one order per instrument, so it gives no new signal while a limit or stop order waits.

### When and how it fills

Queued orders are checked at each later bar's open, before stop losses and take profits, sells before buys:

| Order        | Fills when      | Price                                 |
| ------------ | --------------- | ------------------------------------- |
| Buy limit L  | open ≤ L        | the open with slippage, never above L |
|              | or low **<** L  | L, no slippage                        |
| Sell limit L | open ≥ L        | the open with slippage, never below L |
|              | or high **>** L | L                                     |
| Buy stop S   | open ≥ S        | the open with slippage (a gap)        |
|              | or high ≥ S     | S with slippage                       |
| Sell stop S  | open ≤ S        | the open with slippage                |
|              | or low ≤ S      | S with slippage                       |

- **Trade-through:** a limit needs a trade through its price; a touch is not a fill, because queue position is unknown.
- **No slippage on limits:** a limit price is never worse than asked.
- **Slippage on stops:** stops become market orders once triggered, so they take slippage.
- **Same-bar exits:** a position entered inside a bar (not at the open) is checked against that bar's stop loss and take profit like any other. Such an exit fill is marked `same_bar_as_entry`, because the bar's low or high may have come before the entry.
- **Fill model:** these fills carry `fill_model_version` `next-open-slippage-v1+limit-stop-v1`, and so does a run that used them. Runs with market orders only keep `next-open-slippage-v1`.
- **Results:** orders list `order_type`, `price` and `expire_bars` when not market. Fill reasons are `limit_order` and `stop_order`.

## Alternatives considered

- **Filling a limit on a touch:** optimistic. Most touches in real markets do not fill a resting order behind the queue.
- **Modelling the path inside the bar from 1-minute data:** more exact, but bars are the backtest's unit, and finer data is not always loaded for daily runs.

## Revisit when

- Backtests run on finer data than their signals, so fills inside a bar can be ordered.
- Orders need good-till-cancelled or bracket (OCO) behaviour.
