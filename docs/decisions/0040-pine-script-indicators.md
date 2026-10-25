# ADR 0040 — Pine Script indicators

- **Status:** accepted
- **Date:** 2026-10-25

## Context

The user asked SIW Research to support Pine Script, TradingView's indicator language. Until now the app had three kinds of indicators:

- the built-in catalog;
- formula indicators, i.e. arithmetic over prices and catalog indicators (no code);
- Python indicators, which run in a separate, restricted process (ADR 0023).

Pine scripts are run bar by bar. They keep state across bars (`var`), read earlier bars (`x[n]`), and declare their own inputs and plots. None of the existing kinds can run one as written.

## Decision

### An interpreter in the app

- **Location:** Pine runs inside the web app (`pine-parser.ts`, `pine.ts`) on the chart's bars, like the catalog and formula indicators. No server, no network, and no TradingView service is involved.
- **Parsing:** the script is parsed into a syntax tree. The parser handles indentation blocks, line continuation, `#rrggbb` colours, and `if` used as a value.
- **Running:** the tree is interpreted. `eval` and `Function` are never used.
- **No look-ahead:** the script runs once per bar, in time order.
  - Each variable keeps a value per bar, so `x[n]` reads n bars back.
  - `var` values are kept across bars.
  - `ta.*` functions keep their own state per call site, as in Pine.
  - A bar never sees a later bar. A test checks that cutting the bars does not change earlier values.
- **Limits:**
  - scripts of at most 20,000 characters;
  - at most 2,000,000 loop steps per run (over all bars);
  - at most 32 nested function calls.
  - A script that breaks a limit stops with an error that names the line.

### Supported subset (Pine v5/v6 indicators)

- **Language:**
  - `indicator()` (or `study()`);
  - variables, `var`, `:=` and `+=`-style assignment, tuples (`[a, b] = …`);
  - `if`/`else`, `for … to … by`, `while`, `break` and `continue`;
  - user functions (`f(x) => …`), and the ternary operator.
- **Inputs:** `input`, `input.int`, `input.float`, `input.bool` and `input.source` become the indicator's inputs in the settings dialog.
  - Each input's key is a slug of its title.
  - Sources and booleans are offered as select lists.
  - `input.color` and `input.string` stay at their defaults and cannot be edited.
- **Output:**
  - `plot()` lines and histograms, with a fixed or per-bar colour and a line width;
  - `hline()` levels.
  - `overlay=true` draws on the price chart; otherwise the indicator gets its own pane.
- **Functions:** `ta.*` (moving averages, RSI, MACD, BB, stochastic, ATR, CCI, VWAP, crosses, `barssince`, `valuewhen` and others), `math.*`, `nz`, `na`, `fixnan`, `color.new` and `color.rgb`. The editor's help lists them all (`PINE_FUNCTIONS`).
- **Refused, with the line and column:**
  - `strategy()` and `strategy.*`: orders and backtests stay with the backtester and the agent app (ADR 0036);
  - `request.*`: other symbols or timeframes. The chart's timeframe setting covers larger and smaller timeframes (ADR 0027, ADR 0039).
  - drawings (`line.new`, `label.new`, `box.new`, `table.*`), `fill`, `bgcolor`, `plotshape` and arrays: any function not on the list.

### In the app

- **Adding:** the indicator dialog has a "Pine Script" category. "New Pine Script indicator" opens an editor with an example (EMA Cross).
  - Errors are checked as the user types and show as "Line N, column M: …".
  - The script is also tried on a few made-up bars, so a misspelt name shows before the indicator is added.
- **Stored:**
  - The chart's indicator has type `pine`, the source in `pine`, and the input values in `params`.
  - Plot widths from `linewidth=` go into `style`, so the Style tab can change them as for any indicator.
- **Settings:** the Inputs tab shows the script's inputs, the timeframe and the code.
  - "Apply" saves new code.
  - Input values the user set are kept if the input still exists.
- **Shared behaviour:** style, timeframe and visibility work as for built-in indicators. Pine is computed like the catalog, so it also works on larger and smaller timeframes.

## Alternatives considered

- **Translating Pine to JavaScript and running it with `Function`:** faster, but any gap in the translator becomes code execution in the app. An interpreter can only do what it implements.
- **Running Pine in the Python sandbox (ADR 0023):** safe, but needs a Pine runtime in Python. It also makes every chart change a round trip to the API.
- **A full Pine implementation (strategies, `request.security`, drawings):** too large. Strategies conflict with the rule that orders run only in the agent app.

## Consequences

- Many published TradingView indicators run unchanged, but not all. Scripts that use unsupported functions fail with a clear message rather than drawing something wrong.
- Pine's numbers are JavaScript doubles, like the catalog indicators. Indicator values are not money amounts, so the Decimal rule does not apply.

## Revisit when

- Users need drawings, `fill`/`bgcolor`, `plotshape` or arrays.
- Scripts become slow on long charts. The interpreter could be compiled to a faster form or moved to a worker.
- Pine strategies should feed the backtester.

## Amendment — 2026-10-25: strategies, requests, drawings and arrays

The user asked for the parts refused above to be implemented as well. The interpreter stays the same: no `eval`, one bar at a time, loop and size limits. What changes:

### Language

- **Types:** `float[]`, `array<line>`, `line`, `label`, `box`, `table` and `chart.point` declarations. They change nothing at run time.
- **Statements:**
  - `switch` (with or without a subject, as a statement or a value);
  - `for x in xs` and `for [i, x] in xs`;
  - `method` definitions and method calls (`xs.push(1)`, `l.set_x2(…)`);
  - default parameter values and named arguments for user functions.
- **Continuation lines:** a line indented by other than a multiple of 4 continues the line before it, as in Pine.
- **Limits:** scripts may now be 50,000 characters long, with 30,000 parts.
- **Still refused:** user-defined types (`type`), libraries (`import`/`export`), `matrix`, `map` and `polyline`.

### Values and functions

- **Arrays** (`array.*`):
  - a reference type, as in Pine;
  - negative indexes count from the end;
  - an index outside the array stops the script with an error at its line;
  - at most 100,000 items per array;
  - `array.slice` returns a copy, not a view.
- **Strings:** `str.*`, `+` concatenation, and `str.tostring` formats (`#.##`, `format.mintick`, `format.percent`, `format.volume`).
- **Time:**
  - `time`, `time_close`, `year` … `second`, and `timestamp()`, all in the exchange's time zone;
  - `barstate.*`, `syminfo.*` and `timeframe.*`.
  - `barstate.islast` is the last bar loaded. In replay that is the replay's bar, so it does not reveal later bars.
- **New `ta.*` functions:**
  - pivots, supertrend, DMI, SAR, KC, ALMA, SWMA, linreg, correlation, percentrank, MFI, WPR;
  - highest/lowest bars, rising/falling, OBV and A/D.
  - A test checks that none of them reads a later bar. A pivot is reported on the bar that confirms it.
- **Inputs:**
  - `input.string(options=[…])` and `input.timeframe` become choice lists in the settings;
  - `input.symbol` and `input.session` stay at their defaults.
- **No-ops:** `alert`, `alertcondition` and `log.*` are accepted and do nothing. The app does not send alerts.

### What scripts draw

- **Shapes and colours:**
  - `plotshape`, `plotchar` and `plotarrow`;
  - `bgcolor`, and `barcolor` (price-pane scripts recolour those candles);
  - `fill` between two plots or two hlines, with a per-bar colour.
- **Hidden plots:** `display=display.none` plots are computed but hidden until shown in the Style tab.
- **Drawings:** `line.*`, `label.*`, `box.*`, `table.*` and `linefill.*`.
  - The chart draws their state after the last bar, as TradingView does.
  - `max_lines_count`, `max_labels_count` and `max_boxes_count` apply (default 50, at most 500). The oldest are deleted first.
  - Coordinates may be bar indexes or times. A bar index past the last bar is placed by bar length.
- **Rendering:** a lightweight-charts series primitive (`pine-graphics.ts`) draws all of this in the script's pane:
  - backgrounds and fills under the plots;
  - lines, boxes, labels, shapes, tables and strategy fills above them.

### request.security

- **Fixed symbol and timeframe:** both must be fixed when the script runs. Each may be a text, an input, `syminfo.tickerid` or `timeframe.period`, so the app knows what to fetch.
- **Timeframes:** 1, 2, 3, 5, 10, 15, 30, 60, 120, 240 minutes, D, W and M.
  - M is built from daily bars by calendar month in the exchange's time zone.
  - Minute bars are fetched on intraday charts only.
- **Other symbols:** found by ticker in the instrument list, then by the symbol search (exact ticker). A ticker that is not found is reported under the chart.
- **Values:**
  - The whole script runs on the other bars, and the expression's value is taken from that run.
  - A chart bar gets the value of the last other bar that was final by the chart bar's end, as for larger-timeframe indicators (ADR 0027). No value appears early.
  - `gaps_on` shows a value only on the bar where it became final.
- **lookahead_on:** allowed only when the expression reads earlier bars (`close[1]` or further back). It then gives the containing larger bar's earlier values. Other `lookahead_on` uses are refused, because they would show values before they were known.
- **request.security_lower_tf:** returns, for each chart bar, an array of the smaller bars that were final by the chart bar's end.
- **Other `request.*`:** financials, earnings, dividends, economic data and the like are refused. SIW has no such data.

### Strategies as chart backtests

`strategy()` scripts run in a broker emulator (`pine-strategy.ts`). Its fills are **backtest fills only**:

- they are kept in memory for the chart;
- they are shown with markers and a results panel under the chart;
- they are never sent to a broker and never written to any ledger.

This keeps the separation of real, manual, scenario and backtest fills (AGENTS.md), and ADR 0036's rule that orders are only placed from SIW Agent.

- **Supported calls:**
  - `strategy.entry`, `order`, `exit` (limit/stop, profit/loss in ticks, trailing stop), `close`, `close_all` and `cancel`;
  - the `strategy.*` variables and `strategy.closedtrades.*` / `opentrades.*`.
  - `strategy.risk.*` is refused.
- **Settings read:**
  - initial capital;
  - default quantity (fixed, cash, or percent of equity in whole shares);
  - commission (percent, per contract, per order);
  - slippage in ticks;
  - pyramiding;
  - `process_orders_on_close`;
  - `close_entries_rule`.
- **When orders fill:** an order placed on a bar fills from the next bar on. Market orders fill at the next bar's open. Limit and stop orders fill when the bar's range reaches their price (or at the open, if it opens past it). With `process_orders_on_close` or `immediately=true`, market orders fill at the close of the bar that placed them. An entry against the position closes it first.
- **Order inside a bar:** prices are assumed to go open → the nearer of high/low → the other → close (TradingView's assumption). OHLC bars do not record this. When two or more fills inside one bar depend on it, those fills are marked (`*`) and counted in the panel, so they are not taken as known.
- **Money and quantities:** fixed-point decimals with 10 digits (bigint), not floating point. Prices come from the bars, which the chart already holds as numbers.

## Revisit when (amendment)

- Users need user-defined types, `matrix`, `map`, `polyline`, `plotcandle` or `plotbar`.
- A strategy's chart backtest should be saved or compared with the backtest engine (ADR 0021). That needs a contract for its fills, not memory only.
- Minute bars for `request.security` are wanted on daily charts.
