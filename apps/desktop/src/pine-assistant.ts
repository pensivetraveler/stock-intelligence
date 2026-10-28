/**
 * What the indicator assistant (ADR 0042) tells Claude about the app's Pine, and how a
 * script Claude wrote is run on the chart: the result goes back to Claude as the first
 * error, or as a summary of what was computed (no bar values).
 */

import type { OhlcvBar } from "./indicators";
import { defaultParams } from "./indicators";
import {
  compilePine,
  PINE_FUNCTIONS,
  PineError,
  pineDefinition,
  type PineContext,
  type PineProgram,
  runPine,
} from "./pine";

/** The supported subset, sent once as the conversation's fixed system prompt. */
export function pineReference(): string {
  const functions = PINE_FUNCTIONS.filter(
    (name) => name !== "strategy" && !name.startsWith("strategy."),
  );
  return [
    "Language: Pine v5/v6 indicator() scripts (//@version=5 or 6).",
    "- Variables, var, := and += style assignment, tuples ([a, b] = f()), if/else (also as a value), switch, for ... to ... by, for x in xs, while, break, continue, the ternary operator.",
    "- User functions (f(x) => ...) with default and named arguments; method definitions and calls.",
    "- Type annotations (float, int, bool, string, color, float[], array<line>, line, label, box, table, chart.point) are accepted.",
    "- x[n] reads n bars back; na, nz, fixnan. Strings with + and str.*; time, year...second, timestamp(), barstate.*, syminfo.*, timeframe.* (exchange time zone).",
    "Inputs: input, input.int, input.float, input.bool, input.source, input.string(options=[...]) and input.timeframe become settings; input.color, input.symbol and input.session stay at their defaults.",
    "Output: plot (line or histogram, colour per bar, linewidth, display.none), hline, plotshape, plotchar, plotarrow, bgcolor, barcolor, fill, line.*, label.*, box.*, table.*, linefill.*. overlay=true draws on the price chart, otherwise a separate pane.",
    `Functions: ${functions.join(", ")}.`,
    "request.security works on the chart, but the assistant's test run has no other symbol or timeframe data; avoid it unless asked.",
    "Not supported: strategy() and strategy.* in this assistant, user-defined types (type), import/export libraries, matrix, map, polyline, alerts (alertcondition, alert) and anything not listed above.",
    "Limits: 50,000 characters, 2,000,000 loop steps per run, 32 nested calls, 100,000 items per array.",
    "Errors are reported as line and column. No look-ahead: a bar never sees a later bar.",
  ].join("\n");
}

export type AssistantRun =
  | { ok: true; summary: string; program: PineProgram }
  | { ok: false; error: string };

const number = (value: number) =>
  Number.isInteger(value) ? String(value) : value.toPrecision(6);

/** Runs a script on the chart's bars with its default inputs, as the preview will. */
export function runForAssistant(
  source: string,
  bars: OhlcvBar[],
  context: PineContext,
): AssistantRun {
  let program: PineProgram;
  try {
    program = compilePine(source);
    if (program.strategy) {
      return {
        ok: false,
        error:
          "line 1, column 1: write an indicator() script; strategy() is not used here",
      };
    }
    const params = defaultParams(pineDefinition(source));
    const result = runPine(program, bars, params, context);
    return { ok: true, summary: summarize(program, result, bars), program };
  } catch (error) {
    if (error instanceof PineError) {
      return {
        ok: false,
        error: `line ${error.line}, column ${error.column}: ${error.message}`,
      };
    }
    return {
      ok: false,
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

function summarize(
  program: PineProgram,
  result: ReturnType<typeof runPine>,
  bars: OhlcvBar[],
): string {
  const lines = [
    `Title: ${program.title}; ${program.overlay ? "on the price chart" : "in its own pane"}; ${bars.length} bars.`,
  ];
  if (program.inputs.length > 0) {
    lines.push(
      `Inputs: ${program.inputs
        .map(
          (input) =>
            `${input.label.en}=${input.options?.find((o) => o.value === input.default)?.label ?? input.default}`,
        )
        .join(", ")}.`,
    );
  }
  for (const plot of program.plots) {
    const values = result.values[plot.key] ?? [];
    const present = values.filter(
      (value): value is number =>
        typeof value === "number" && Number.isFinite(value),
    );
    const empty = values.length === 0 ? 1 : 1 - present.length / values.length;
    const name = `plot '${plot.title}' (${plot.kind}${plot.hidden ? ", hidden" : ""})`;
    if (present.length === 0) {
      lines.push(`${name}: empty on every bar.`);
      continue;
    }
    const low = present.reduce((a, b) => Math.min(a, b));
    const high = present.reduce((a, b) => Math.max(a, b));
    const last = values[values.length - 1];
    lines.push(
      `${name}: last ${typeof last === "number" ? number(last) : "na"}, min ${number(low)}, max ${number(high)}, empty ${Math.round(empty * 100)}%${low === high ? ", never changes" : ""}.`,
    );
  }
  const levels = program.hlinePrices.filter(
    (price): price is number => price !== null,
  );
  if (levels.length > 0) lines.push(`hline levels: ${levels.join(", ")}.`);
  const graphics = result.graphics;
  const counts = [
    ["shapes/characters/arrows", graphics.shapes.length],
    ["background colours", graphics.backgrounds.length],
    ["bar colours", graphics.barColors.length],
    ["fills", graphics.fills.length],
    ["lines", graphics.lines.length],
    ["labels", graphics.labels.length],
    ["boxes", graphics.boxes.length],
    ["tables", graphics.tables.length],
  ].filter(([, count]) => (count as number) > 0);
  if (counts.length > 0) {
    lines.push(
      `Signals and drawings: ${counts.map(([label, count]) => `${count} ${label}`).join(", ")}.`,
    );
  }
  if (
    program.plots.length === 0 &&
    counts.length === 0 &&
    levels.length === 0
  ) {
    lines.push("It draws nothing.");
  }
  if (result.missing.length > 0) {
    lines.push(
      `No data in this test run for request.security: ${result.missing.join(", ")}.`,
    );
  }
  return lines.join("\n");
}
