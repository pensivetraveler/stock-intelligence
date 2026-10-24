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
