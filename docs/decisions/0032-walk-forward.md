# ADR 0032 — Walk-forward backtests

- **Status:** accepted
- **Date:** 2026-10-22

## Context

A backtest tuned on the same bars it reports on overstates what a strategy would have earned. The user asked for walk-forward runs after limit and stop orders (ADR 0031).

## Decision

### Settings

- **Run config:** an optional `walk_forward`:
  - `in_sample_bars` (20–5,000) and `out_of_sample_bars` (5–5,000);
  - `objective`: `net_return`, `return_over_drawdown`, `profit_factor` or `win_rate`;
  - `grid`: 1–3 numbers of the rules strategy, each a JSON pointer such as `/entry/left/params/length`, with 1–20 distinct values.
- **Limits:** at most 50 windows and 400 backtests (windows × combinations).
- **Python strategies:** not accepted for now. Every combination would need its own sandbox process.

### Windows

- **Time axis:** the run's bar times (all instruments together).
- **Window shape:** each window has `in_sample_bars` followed by `out_of_sample_bars`. The next window starts one out-of-sample stretch later (rolling). The last out-of-sample stretch may be shorter.
- **Choosing parameters:** in each window every combination is backtested on the in-sample bars only (engine `trade_window`). The best by the objective is chosen; ties go to the first combination in grid order, so runs are deterministic.
  - **No trades:** a combination without trades, or without a defined value, ranks last, except for `net_return`, where no trades scores 0.
  - **Profit factor:** `profit_factor` with winning trades and no losses ranks first.
- **No look-ahead:** indicators see every earlier bar, never later ones (ADR 0021). Bars before a window only warm indicators up.

### The result

- **One pass:** the final result is one engine pass over the out-of-sample stretches only, from the first out-of-sample bar to the last. Each window's chosen parameters give the signals for bars inside that window.
- **Out-of-sample only:** equity, fills, trades and metrics are all out-of-sample.
- **Positions:** a position open at a window boundary is kept. The next window's exit rule then manages it.
- **`walk_forward` section:** the results add `walk_forward`, with the windows' dates, the chosen values and their in-sample score.

## Alternatives considered

- **Anchored windows (in-sample always from the first bar):** possible later as an option. Rolling windows adapt faster to regime changes.
- **Closing positions at every window boundary:** simpler accounting, but trades that span a boundary would be cut short.
- **Stitching separate out-of-sample backtests:** would need a combined metric definition; one pass reuses the engine's metrics as they are.

## Revisit when

- Python strategies need walk-forward (a pooled sandbox per combination).
- Users want anchored windows, more parameters or random search.
