# ADR 0023 — Python custom indicators (Phase 4)

- **Status:** accepted
- **Date:** 2026-10-21

## Context

SYSTEM_SPEC §6.7 and roadmap Phase 4 ask for a Python indicator SDK:

- an isolated worker with resource limits and output validation;
- warm-up handling;
- no look-ahead.

Per-chart indicator settings, presets and the 50 built-in TypeScript indicators already exist. On 2026-10-21 the user deferred Claude commentary (ADR 0022) and chose this as the next step.

## Decision

### Writing an indicator

The user writes one Python module:

```python
NAME = "RSI band"  # shown in the indicator list
INPUTS = {"length": 14}  # numeric settings and their defaults
OUTPUTS = ["value"]  # 1 to 6 series
PANE = "separate"  # "price" (over the candles) or "separate"


def update(ctx):
    # Called once per bar, oldest first. Return this bar's value for each output.
    rsi = ctx.indicator("rsi", length=ctx.params["length"])
    return {"value": rsi}
```

`ctx` gives:

- `bar` and `history` (the bars so far; indexing past the current bar raises an error);
- `index` and `params`;
- `state`, a dict kept between calls for incremental formulas;
- `indicator(id, output=None, **params)`, the built-in engine indicators' value at this bar, which match the chart (ADR 0021 parity).

### No look-ahead by construction

- **One bar at a time:** the worker calls `update` once per bar in time order and only ever passes the bars up to that one.
- **Built-in indicators:** each series is computed once over the bars sent. They are causal (tested in ADR 0021), so the value read at bar _i_ depends only on bars up to _i_.
- **The client's bars:** the chart sends the bars it has: warm-up bars, the shown bars, and in replay only the bars available at the replay time. The worker never gets anything later.
- **Limitation:** as for strategies (ADR 0021), the isolation guards against accidents, not against deliberately hostile code.

### Execution

- **Same sandbox as strategies:** a separate process with an empty environment, a temporary working directory, rlimits and an audit hook, plus a 30-second timeout per computation.
- **Output checks:** each output must be a finite number or `None`. Missing or extra output names, too many outputs or the wrong length fail the computation with a clear message.
- **Size limit:** at most 5,000 bars per computation.

### Versions and storage

- **Versions:** each save is a new immutable version with the SHA-256 of its code, unless the code is unchanged.
- **Checked on save:** saving runs the code once in the sandbox on 3 synthetic bars, to read and check `NAME`, `INPUTS` (at most 8, numbers between −1e9 and 1e9), `OUTPUTS` and `PANE`.
- **Storage:** `SIW_DATA_DIR/indicators.sqlite3`.
- **On a chart:** an instance's type is `py:<indicator_id>@<version>`, so it keeps the exact code it was added with until the user picks a newer version.

### API

- `GET /v1/indicators/custom` and `POST /v1/indicators/custom` (create, or with `indicator_id` the next version).
- `POST /v1/indicators/custom/{id}/compute` with the version, the parameters, the bars (time, OHLCV) and the time zone.
- Contracts: `custom_indicator`, `custom_indicator_list_response` and `custom_indicator_result`.

### Screen

- **Indicator dialog:** a "Python" category lists saved indicators, and "New Python indicator" opens an editor with a working template.
- **Charts:** a custom indicator is drawn like a built-in one, with the same legend, settings, hide and remove. Its values come from the API; a failure is shown on the chart, never drawn as zeros.
- **Warm-up:** integer settings count as look-backs, as for built-ins.

### In backtest rules (2026-10-21)

- **Operand:** a rules operand may be `{"indicator": "py:<indicator_id>@<version>", "params": {...}, "output": "<name>"}`. Omitted settings use that version's defaults; `output` defaults to the first output.
- **Validation on save:** the version must exist in `indicators.sqlite3`, settings must be ones it declares (finite numbers) and the output one it lists. Without the indicator store such operands are refused.
- **Computation:** before the run, each instrument's final bars go through `runner.compute`, once per indicator version and settings, in the same sandbox: `update(ctx)` sees one bar at a time, so a signal at bar _i_ depends only on bars up to _i_. Up to 5,000 final bars per instrument (the indicator limit); more fails the run with that reason.
- **Pinning:** the strategy definition names the exact version, and its fingerprint covers it. Results add `custom_indicators`: the type, ID, version, name and code SHA-256 of every version used. The code's hash is checked against the stored one before the run.
- **Failures:** an indicator error fails the run with the indicator type, the instrument and the reason. Nothing is retried, and the run has no results.
- **Screen:** the rule form's indicator list offers the latest version of each saved Python indicator (a rule pinned to an older version keeps it). Results list the pinned Python indicators with their hash.

## Editor follow-up (2026-10-21)

- **Preview:** `POST /v1/indicators/custom/preview` runs unsaved code in the same sandbox on the bars the active chart computes indicators on (warm-up included, and in replay only bars up to the replay time). It returns the description and the values (contract `custom_indicator_preview`). Nothing is stored.
- **Failing line:** sandbox errors end with `(line N)`, and the editor shows that line of the code under the message.
- **Newer versions:** when a chart holds an older version of an indicator, its legend offers "↑vN". Switching keeps the settings whose names still exist and takes the new defaults for the rest. Nothing changes until the user clicks.

## Alternatives considered

- **Running Python in the browser (Pyodide):** no server round trip, but a 10 MB+ runtime, and the sandbox and versioning policies would differ from strategies.
- **Calling `compute(bars)` once with all bars:** faster, but nothing stops the code from reading bar _i+1_ while computing bar _i_. Look-ahead could then only be checked after the fact, by recomputing on prefixes.

## Revisit when

- Backtests need more than 5,000 final bars per instrument with a Python indicator: compute in chunks with carried state, or raise the limit for backtests.
- Python strategies should call saved Python indicators (`ctx.indicator("py:...")`), not only rules.
- Indicators written by others or by an Agent are to be run: approval flow and a stronger sandbox.
