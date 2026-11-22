# ADR 0026 — Formula indicators (Phase 4)

- **Status:** accepted
- **Date:** 2026-10-21

## Context

Roadmap Phase 4 asks for "minimal visual composition using a safe expression/graph DSL; no arbitrary Python generation from UI expressions". Python indicators (ADR 0023) cover arbitrary code. The user asked for the remaining indicator work, including this one.

## Decision

- **Language:**
  - numbers and the bar fields `open`, `high`, `low`, `close`, `volume`, `hl2`, `hlc3` and `ohlc4`;
  - the operators `+ - * /`, unary minus and parentheses;
  - functions from an allow-list: `sma`, `ema`, `wma`, `rma`, `stdev`, `highest`, `lowest`, `sum`, `change`, `roc`, `shift`, `rsi`, `atr`, `vwap`, `obv`, `abs`, `sqrt`, `log`, `max` and `min`.
- **Window lengths:** whole-number constants from 1 to 500. `shift` takes 0 to 500 and only looks back.
- **Limits:** at most 500 characters and 200 nodes.
- **Safety:** the formula is parsed by a hand-written recursive-descent parser into a tree and evaluated by walking that tree, with no `eval` and no Python. Unknown names, wrong argument counts and non-constant lengths are refused, with the character position.
- **No look-ahead:** every function uses the current bar and earlier ones, and a test shows that appending bars never changes earlier values. The built-in names reuse the chart's own formulas (`sma`, `ema`, `rsi`, `atr` and the rest), so a formula matches the indicator it names exactly.
- **Missing values:** a missing value (warm-up, division by zero, a non-finite result, the log of a non-positive number) stays missing and is never drawn as zero.
- **Warm-up:** the parsed tree reports how many earlier bars it reads, summed through nested windows, and that feeds the chart's warm-up like the built-in look-backs.
- **Storage:** an instance is `{ type: "formula", formula, label, pane }` in the chart's saved indicators, independent per chart like any other indicator. Templates copy it like any other indicator.
- **Screen:**
  - a "Formula" category in the indicator dialog opens an editor that checks the formula as it is typed;
  - the legend's ⚙ edits the name, the formula and the pane, with the same checks;
  - an invalid saved formula draws nothing.

## Alternatives considered

- **A visual node graph:** more discoverable, but much more UI for the same expressiveness. The expression language can back a graph editor later.
- **Running formulas in the Python sandbox:** this would give one implementation for charts and backtests, but every keystroke would need a round trip to the server.

## Revisit when

- Formulas should be usable in backtest rules. That needs a Python evaluator with a shared parity fixture, like ADR 0021's indicators.
- Formulas need conditions or several outputs.

## Amendment — 2026-11-15: statements, settings, several lines ([ADR 0058](0058-pine-import-to-native-indicators.md) §2)

The "conditions or several outputs" item above is now open. Pine Script indicators are to be converted into formulas, so the language grew. A formula that is one expression works as before.

- **Statements:** one per line (or separated by `;`), with `//` comments: `name = value`, `name = input(default, "Title", minval, maxval, step)`, `plot(value, "Title", color, linewidth, style)` and `hline(level)`.
- **Values:** `true`/`false`, `na`, colours `"#rrggbb"`/`"#rrggbbaa"`, comparisons, `and`/`or`/`not`, `cond ? a : b`, `%`, and `x[n]` (n a constant from 0 to 500). A comparison with a missing value is false, as in Pine Script.
- **Settings:** number, true/false and price (`input(close)`) settings become the indicator's settings. A window length may be a number setting, narrowed to a whole number from 1 to 500. The warm-up uses the setting's value.
- **Functions added:** `crossover`, `crossunder`, `cum`, `tr`, `macd`/`macd_signal`/`macd_hist`, `bb_upper`/`bb_lower`, `stoch`, `cci`, `mfi`, `nz`, `na`. Each matches the Pine interpreter's function on test bars (within 1e-9).
- **Limits:** 4,000 characters, 1,000 nodes, 32 settings, 16 plots, 16 levels.
- **Still:** no `eval`, a hand-written parser and evaluator, no look-ahead (tested for every function), and no backtest use.
