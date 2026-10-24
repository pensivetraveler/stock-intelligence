# ADR 0039 — Indicator settings like TradingView, and indicators on smaller timeframes

- **Status:** accepted
- **Date:** 2026-10-24

## Context

The user asked for two changes.

- **Settings:** change indicators' colours and other settings, ideally with TradingView's indicator settings as the model. Until now the settings held lengths and a timeframe only. Colours came from the catalog.
- **Smaller timeframes:** use a smaller timeframe's indicator on a larger chart, for example the 60 EMA of 1-minute bars on a 5-minute chart. Indicators could already be computed on larger timeframes (ADR 0027, ADR 0034).

## Decision

### Settings dialog: "Inputs" and "Style" tabs

- **Inputs:**
  - The lengths and the timeframe, as before. Formula indicators also keep their formula fields here; "Apply" takes the formula.
  - **Source**, for indicators that read closes only: SMA, EMA, WMA, HMA, Bollinger Bands, Envelope, RSI, MACD, Stochastic RSI, Momentum, ROC, BB width and %B. The choices are close, open, high, low, (H+L)/2, (H+L+C)/3 and (O+H+L+C)/4. The source replaces each bar's close before the formula runs. Indicators that read highs, lows or volume get no source, since replacing their close would change their meaning.
- **Style:**
  - **Each output:** visible, colour, width (1–4 px) and line style (solid, dashed, dotted). Histograms take visibility and colour; signed histograms keep their up and down colours.
  - **Levels:** oscillators in their own pane take guide levels (value, colour, visible). Levels can be added (up to 10) or removed. Until a level is changed, the catalog's levels (e.g. RSI 30/70) are shown.
  - "Reset style" goes back to the catalog's look.
- **Applying changes:** every change applies at once and is saved with the layout.
- **Stored per indicator:** `source` and `style`. Unknown fields and bad values are dropped when a layout is read. Layouts without them look as before.
- **Not taken from TradingView:**
  - the "Visibility" tab (which timeframes show an indicator);
  - opacity;
  - price precision;
  - plotting an output as anything other than its kind.

### Indicators on smaller timeframes

- **Choices:** an intraday chart offers 1m, 2m, 3m, 5m and 10m below its own timeframe. A choice must divide the chart's timeframe evenly, with at most 60 smaller bars per chart bar. A 5-minute chart offers 1m; a 60-minute chart offers 1m to 10m. Daily and weekly charts offer none, because that would mean too many minute bars.
- **Values:** the indicator is computed on the smaller bars. Each chart bar shows the value of the last smaller bar available by the chart bar's end, using the same alignment as larger timeframes (`alignToChart`). On a 5-minute chart, a 1-minute EMA shows the EMA at the 1-minute bar that closes the 5 minutes. No value from after a chart bar's end is used, so replay and look-ahead rules hold.
- **Data:**
  - The smaller bars are fetched one session at a time, for the chart's sessions plus warm-up sessions, as for larger intraday timeframes (ADR 0034).
  - Bars loaded later with "older bars" get no smaller-timeframe values.
  - Python indicators still run on the chart's own bars only.

### Chart fix found on the way

The indicator settings opened under the drawing layer, so drawings showed through them. The legend (and its popovers) now sits above the drawing layer. The legend lets pointer events through, so drawing is unaffected.

### Amendment 2026-10-24: visibility, opacity, precision

At the user's request, the parts first left out were added.

- **"Visibility" tab:** minutes (1–59), hours (1–24), days (1–366) and weeks (1–52), each on or off with a range.
  - A chart timeframe falls in one group: 45m in minutes, 120m in hours 2, 1d in days, 1w in weeks.
  - An indicator outside its range is not drawn, and its legend says "hidden on this timeframe".
  - Only groups that differ from "all" are stored (`visibility`).
- **Opacity:** a 0–100 % slider beside each output's colour. The colour is drawn as rgba.
- **Precision:** default, or 0 to 8 decimals, for the legend and the indicator's price scale.

## Alternatives considered

- **A full copy of TradingView's indicator engine (Pine-like inputs and plots):** much larger than asked. The catalog and the formula indicators already cover the inputs.
- **Smaller-timeframe values as arrays per chart bar (TradingView's `request.security_lower_tf`):** needs a way to plot arrays. The last value in each chart bar is what a moving average on a smaller timeframe usually means.

## Revisit when

- Indicators should show only on some timeframes, or need opacity or precision settings.
- Smaller-timeframe indicators are wanted on daily charts.
