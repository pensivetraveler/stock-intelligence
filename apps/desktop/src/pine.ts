/**
 * Pine Script indicators (ADR 0040): a TradingView Pine v5 subset run in the browser,
 * without `eval`. The script runs once per bar, oldest first, as on TradingView:
 * variables keep one value per bar so `x[1]` reads the previous bar, `var` keeps its
 * value across bars, and each `ta.*` call has its own state. Every function reads the
 * current bar and earlier ones only, so a script cannot see the future.
 *
 * Supported: `indicator()`, `input.int/float/bool/source/color`, `plot()`, `hline()`,
 * variables, `:=`, `if`/`else`, `for`, `while`, the ternary, user functions, tuples,
 * and the `ta.*`, `math.*`, `color.*` functions listed in PINE_FUNCTIONS. Not
 * supported: `strategy.*`, `request.*`, arrays, drawings (line/label/box/table),
 * alerts. Loops are capped so a script cannot hang the app.
 */
import type {
  IndicatorContext,
  IndicatorDefinition,
  IndicatorInput,
  IndicatorOutput,
  OhlcvBar,
  Series,
} from "./indicators";
import { parsePine, PineError, type Expr, type Stmt } from "./pine-parser";

export { MAX_PINE_LENGTH, PineError } from "./pine-parser";

const MAX_LOOP_STEPS = 2_000_000; // per run, over all bars
const MAX_CALL_DEPTH = 32;

type Value = number | boolean | string | null | Value[];

const SOURCES = [
  "close",
  "open",
  "high",
  "low",
  "hl2",
  "hlc3",
  "ohlc4",
] as const;

const COLORS: Record<string, string> = {
  "color.red": "#f23645",
  "color.green": "#089981",
  "color.blue": "#2962ff",
  "color.orange": "#ff9800",
  "color.yellow": "#ffeb3b",
  "color.purple": "#9c27b0",
  "color.fuchsia": "#e040fb",
  "color.lime": "#00e676",
  "color.aqua": "#00bcd4",
  "color.teal": "#00897b",
  "color.navy": "#311b92",
  "color.maroon": "#880e4f",
  "color.olive": "#808000",
  "color.white": "#ffffff",
  "color.black": "#363a45",
  "color.gray": "#787b86",
  "color.silver": "#b2b5be",
};

const DEFAULT_PLOT_COLOR = "#2962ff";

/** Function names a script may call, for the editor's help. */
export const PINE_FUNCTIONS = [
  "indicator",
  "plot",
  "hline",
  "input",
  "input.int",
  "input.float",
  "input.bool",
  "input.source",
  "input.color",
  "nz",
  "na",
  "fixnan",
  "ta.sma",
  "ta.ema",
  "ta.rma",
  "ta.wma",
  "ta.vwma",
  "ta.hma",
  "ta.rsi",
  "ta.macd",
  "ta.bb",
  "ta.stoch",
  "ta.stdev",
  "ta.highest",
  "ta.lowest",
  "ta.change",
  "ta.mom",
  "ta.roc",
  "ta.atr",
  "ta.tr",
  "ta.cci",
  "ta.vwap",
  "ta.cum",
  "ta.crossover",
  "ta.crossunder",
  "ta.cross",
  "ta.barssince",
  "ta.valuewhen",
  "math.abs",
  "math.max",
  "math.min",
  "math.avg",
  "math.sum",
  "math.sqrt",
  "math.pow",
  "math.exp",
  "math.log",
  "math.log10",
  "math.round",
  "math.floor",
  "math.ceil",
  "math.sign",
  "color.new",
  "color.rgb",
] as const;

// --- compile: what the script declares --------------------------------------------

export interface PinePlot {
  key: string;
  title: string;
  color: string;
  kind: "line" | "histogram";
  width: number;
}

export interface PineProgram {
  body: Stmt[];
  title: string;
  short: string;
  overlay: boolean;
  inputs: (IndicatorInput & { source?: boolean })[];
  plots: PinePlot[];
  levels: number[];
  /** Input key per input call node, and plot key per plot call node. */
  inputKeys: Map<number, string>;
  plotKeys: Map<number, string>;
}

function constant(expr: Expr | undefined): Value | undefined {
  if (!expr) return undefined;
  switch (expr.kind) {
    case "number":
      return expr.value;
    case "string":
      return expr.value;
    case "bool":
      return expr.value;
    case "na":
      return null;
    case "name":
      if (expr.name in COLORS) return COLORS[expr.name];
      if ((SOURCES as readonly string[]).includes(expr.name)) return expr.name;
      if (expr.name.startsWith("plot.style_")) return expr.name;
      return undefined;
    case "unary": {
      const value = constant(expr.operand);
      return typeof value === "number" && expr.op === "-" ? -value : value;
    }
    case "call":
      if (expr.name === "color.new") {
        const base = constant(expr.args[0] ?? expr.named.color);
        const transp = constant(expr.args[1] ?? expr.named.transp);
        return typeof base === "string" && typeof transp === "number"
          ? rgba(base, transp)
          : undefined;
      }
      return undefined;
    default:
      return undefined;
  }
}

function argument(
  call: Extract<Expr, { kind: "call" }>,
  index: number,
  name: string,
): Expr | undefined {
  return call.args[index] ?? call.named[name];
}

function walk(body: Stmt[], visit: (expr: Expr, topLevel: boolean) => void) {
  const expr = (e: Expr, topLevel: boolean): void => {
    visit(e, topLevel);
    switch (e.kind) {
      case "call":
        e.args.forEach((a) => expr(a, false));
        Object.values(e.named).forEach((a) => expr(a, false));
        break;
      case "index":
        expr(e.target, false);
        expr(e.offset, false);
        break;
      case "unary":
        expr(e.operand, false);
        break;
      case "binary":
        expr(e.left, false);
        expr(e.right, false);
        break;
      case "ternary":
        expr(e.test, false);
        expr(e.then, false);
        expr(e.otherwise, false);
        break;
      case "tuple":
        e.items.forEach((a) => expr(a, false));
        break;
      default:
        break;
    }
  };
  const stmts = (list: Stmt[], topLevel: boolean): void => {
    for (const s of list) {
      switch (s.kind) {
        case "declare":
        case "assign":
          expr(s.value, topLevel);
          break;
        case "expr":
          expr(s.value, topLevel);
          break;
        case "if":
          expr(s.test, false);
          stmts(s.body, false);
          stmts(s.otherwise, false);
          break;
        case "for":
          expr(s.from, false);
          expr(s.to, false);
          if (s.step) expr(s.step, false);
          stmts(s.body, false);
          break;
        case "while":
          expr(s.test, false);
          stmts(s.body, false);
          break;
        case "function":
          stmts(s.body, false);
          break;
        default:
          break;
      }
    }
  };
  stmts(body, true);
}

function slug(text: string, taken: Set<string>): string {
  const base =
    text
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_+|_+$/g, "")
      .slice(0, 30) || "input";
  let key = base;
  for (let n = 2; taken.has(key); n += 1) key = `${base}_${n}`;
  taken.add(key);
  return key;
}

/** Reads a script's declarations (title, pane, inputs, plots, levels) or throws. */
export function compilePine(source: string): PineProgram {
  const body = parsePine(source);
  const program: PineProgram = {
    body,
    title: "Pine",
    short: "Pine",
    overlay: false,
    inputs: [],
    plots: [],
    levels: [],
    inputKeys: new Map(),
    plotKeys: new Map(),
  };
  const inputKeys = new Set<string>();
  let seenIndicator = false;
  walk(body, (expr, topLevel) => {
    if (expr.kind !== "call") return;
    if (expr.name.startsWith("strategy") || expr.name.startsWith("request.")) {
      throw new PineError(
        `${expr.name} is not supported: SIW runs Pine indicators only`,
        expr.line,
        expr.column,
      );
    }
    if (expr.name === "indicator" || expr.name === "study") {
      seenIndicator = true;
      const title = constant(argument(expr, 0, "title"));
      const short = constant(argument(expr, 1, "shorttitle"));
      const overlay = constant(argument(expr, 2, "overlay"));
      if (typeof title === "string" && title.trim()) {
        program.title = title.trim().slice(0, 60);
      }
      program.short =
        typeof short === "string" && short.trim()
          ? short.trim().slice(0, 20)
          : program.title.slice(0, 20);
      program.overlay = overlay === true;
      return;
    }
    if (expr.name === "input" || expr.name.startsWith("input.")) {
      const kind = expr.name === "input" ? "auto" : expr.name.slice(6);
      const defval = constant(argument(expr, 0, "defval"));
      const titleValue = constant(argument(expr, 1, "title"));
      const title =
        typeof titleValue === "string" && titleValue.trim()
          ? titleValue.trim().slice(0, 40)
          : `Input ${program.inputs.length + 1}`;
      const key = slug(title, inputKeys);
      program.inputKeys.set(expr.id, key);
      const label = { en: title, ko: title };
      if (
        kind === "source" ||
        (kind === "auto" &&
          typeof defval === "string" &&
          (SOURCES as readonly string[]).includes(defval))
      ) {
        const index = Math.max(
          0,
          SOURCES.indexOf(defval as (typeof SOURCES)[number]),
        );
        program.inputs.push({
          key,
          label,
          default: index,
          min: 0,
          max: SOURCES.length - 1,
          step: 1,
          source: true,
          options: SOURCES.map((name, value) => ({ value, label: name })),
        });
        return;
      }
      if (kind === "bool" || (kind === "auto" && typeof defval === "boolean")) {
        program.inputs.push({
          key,
          label,
          default: defval === true ? 1 : 0,
          min: 0,
          max: 1,
          step: 1,
          options: [
            { value: 0, label: "false" },
            { value: 1, label: "true" },
          ],
        });
        return;
      }
      if (kind === "color" || kind === "string") {
        program.inputKeys.delete(expr.id); // fixed to the default; not editable
        return;
      }
      if (typeof defval !== "number") {
        throw new PineError(
          `${expr.name} needs a number default`,
          expr.line,
          expr.column,
        );
      }
      const minval = constant(argument(expr, 2, "minval"));
      const maxval = constant(argument(expr, 3, "maxval"));
      const step = constant(argument(expr, 4, "step"));
      const isInt =
        kind === "int" || (kind === "auto" && Number.isInteger(defval));
      program.inputs.push({
        key,
        label,
        default: defval,
        min: typeof minval === "number" ? minval : isInt ? 1 : -1e9,
        max: typeof maxval === "number" ? maxval : isInt ? 5000 : 1e9,
        ...(isInt
          ? typeof step === "number" && step !== 1
            ? { step }
            : {}
          : { step: typeof step === "number" ? step : 0.1 }),
      });
      return;
    }
    if (expr.name === "plot") {
      if (!topLevel) {
        throw new PineError(
          "plot() must be called at the top level of the script",
          expr.line,
          expr.column,
        );
      }
      const titleValue = constant(argument(expr, 1, "title"));
      const colorValue = constant(argument(expr, 2, "color"));
      const width = constant(argument(expr, 3, "linewidth"));
      const style = constant(argument(expr, 4, "style"));
      const key = `plot${program.plots.length}`;
      program.plotKeys.set(expr.id, key);
      program.plots.push({
        key,
        title:
          typeof titleValue === "string" && titleValue.trim()
            ? titleValue.trim().slice(0, 30)
            : `Plot ${program.plots.length + 1}`,
        color:
          typeof colorValue === "string"
            ? hexOf(colorValue)
            : DEFAULT_PLOT_COLOR,
        kind:
          style === "plot.style_histogram" || style === "plot.style_columns"
            ? "histogram"
            : "line",
        width:
          typeof width === "number"
            ? Math.min(4, Math.max(1, Math.round(width)))
            : 1,
      });
      return;
    }
    if (expr.name === "hline") {
      const price = constant(argument(expr, 0, "price"));
      if (typeof price === "number") program.levels.push(price);
    }
  });
  if (!seenIndicator) {
    throw new PineError('the script needs indicator("Title")', 1, 1);
  }
  if (program.plots.length === 0) {
    throw new PineError("the script plots nothing: add plot(…)", 1, 1);
  }
  return program;
}

// --- colours ------------------------------------------------------------------------

function hexOf(color: string): string {
  const rgbaMatch = /^rgba\((\d+), (\d+), (\d+)/.exec(color);
  if (rgbaMatch) {
    return `#${rgbaMatch
      .slice(1, 4)
      .map((part) => Number(part).toString(16).padStart(2, "0"))
      .join("")}`;
  }
  return /^#[0-9a-fA-F]{6}/.test(color)
    ? color.slice(0, 7)
    : DEFAULT_PLOT_COLOR;
}

function rgba(color: string, transparency: number): string {
  const hex = hexOf(color);
  const part = (offset: number) => parseInt(hex.slice(offset, offset + 2), 16);
  const alpha = Math.max(0, Math.min(100, 100 - transparency)) / 100;
  return `rgba(${part(1)}, ${part(3)}, ${part(5)}, ${alpha})`;
}

// --- run ----------------------------------------------------------------------------

class Stop extends Error {}
class BreakSignal {}
class ContinueSignal {}

interface Slot {
  values: Value[];
  persistent: boolean;
}

interface FunctionDef {
  params: string[];
  body: Stmt[];
}

type Scope = Map<string, Slot>;

/** Per-call state of a `ta.*` function: its inputs per bar and running values. */
interface CallState {
  inputs: Value[][];
  previous?: number | null;
  seed?: number[];
  extra?: Record<string, unknown>;
}

const num = (value: Value | undefined): number | null =>
  typeof value === "number" && Number.isFinite(value)
    ? value
    : typeof value === "boolean"
      ? value
        ? 1
        : 0
      : null;

const truthy = (value: Value | undefined): boolean =>
  value === true ||
  (typeof value === "number" && value !== 0 && Number.isFinite(value));

export interface PineResult {
  values: Record<string, Series>;
  colors: Record<string, (string | null)[]>;
}

/** Runs a compiled script over bars with the given input values. */
export function runPine(
  program: PineProgram,
  bars: OhlcvBar[],
  params: Record<string, number>,
  context?: IndicatorContext,
): PineResult {
  const values: Record<string, Series> = {};
  const colors: Record<string, (string | null)[]> = {};
  for (const plot of program.plots) {
    values[plot.key] = bars.map(() => null);
    colors[plot.key] = bars.map(() => null);
  }
  const slots = new Map<string, Slot>();
  const states = new Map<string, CallState>();
  const functions = new Map<string, FunctionDef>();
  let steps = 0;
  let bar = 0;
  const dayKey = (time: number) =>
    new Intl.DateTimeFormat("en-CA", {
      timeZone: context?.timeZone ?? "America/New_York",
    }).format(new Date(time * 1000));

  const fail = (
    message: string,
    at: { line: number; column: number },
  ): never => {
    throw new PineError(message, at.line, at.column);
  };

  const field = (name: string, i: number): Value => {
    const b = bars[i];
    if (!b) return null;
    switch (name) {
      case "open":
        return b.open;
      case "high":
        return b.high;
      case "low":
        return b.low;
      case "close":
        return b.close;
      case "volume":
        return b.volume;
      case "hl2":
        return (b.high + b.low) / 2;
      case "hlc3":
        return (b.high + b.low + b.close) / 3;
      case "ohlc4":
        return (b.open + b.high + b.low + b.close) / 4;
      case "hlcc4":
        return (b.high + b.low + 2 * b.close) / 4;
      case "bar_index":
        return i;
      case "time":
        return b.time * 1000;
      default:
        return undefined as unknown as Value;
    }
  };

  const state = (key: string): CallState => {
    let found = states.get(key);
    if (!found) {
      found = { inputs: [] };
      states.set(key, found);
    }
    return found;
  };

  /** Values of input `k` of a call over the last `length` bars, newest last. */
  const windowOf = (
    st: CallState,
    k: number,
    length: number,
  ): number[] | null => {
    if (bar < length - 1) return null;
    const out: number[] = [];
    for (let j = bar - length + 1; j <= bar; j += 1) {
      const value = num(st.inputs[j]?.[k]);
      if (value === null) return null;
      out.push(value);
    }
    return out;
  };

  const smooth = (
    st: CallState,
    value: number | null,
    length: number,
    alpha: number,
  ) => {
    if (value === null) return st.previous ?? null;
    if (st.previous === undefined || st.previous === null) {
      st.seed = [...(st.seed ?? []), value];
      if (st.seed.length < length) return null;
      st.previous = st.seed.reduce((a, b) => a + b, 0) / length;
      return st.previous;
    }
    st.previous = alpha * value + (1 - alpha) * st.previous;
    return st.previous;
  };

  const lengthOf = (value: Value, at: Expr): number => {
    const length = num(value);
    if (
      length === null ||
      !Number.isInteger(length) ||
      length < 1 ||
      length > 5000
    ) {
      return fail("a length must be a whole number from 1 to 5000", at);
    }
    return length;
  };

  const callBuiltin = (
    call: Extract<Expr, { kind: "call" }>,
    args: Value[],
    path: string,
  ): Value => {
    const key = `${path}/${call.id}`;
    const st = () => {
      const s = state(key);
      s.inputs[bar] = args;
      return s;
    };
    const a = (i: number) => num(args[i]);
    switch (call.name) {
      case "nz": {
        const value = args[0];
        return value === null ||
          (typeof value === "number" && !Number.isFinite(value))
          ? (args[1] ?? 0)
          : value;
      }
      case "na":
        return (
          args[0] === null ||
          (typeof args[0] === "number" && !Number.isFinite(args[0]))
        );
      case "fixnan": {
        const s = st();
        const value = num(args[0]);
        if (value !== null) s.previous = value;
        return s.previous ?? null;
      }
      case "math.abs":
        return a(0) === null ? null : Math.abs(a(0)!);
      case "math.sqrt":
        return a(0) === null || a(0)! < 0 ? null : Math.sqrt(a(0)!);
      case "math.exp":
        return a(0) === null ? null : Math.exp(a(0)!);
      case "math.log":
        return a(0) === null || a(0)! <= 0 ? null : Math.log(a(0)!);
      case "math.log10":
        return a(0) === null || a(0)! <= 0 ? null : Math.log10(a(0)!);
      case "math.pow":
        return a(0) === null || a(1) === null ? null : Math.pow(a(0)!, a(1)!);
      case "math.floor":
        return a(0) === null ? null : Math.floor(a(0)!);
      case "math.ceil":
        return a(0) === null ? null : Math.ceil(a(0)!);
      case "math.sign":
        return a(0) === null ? null : Math.sign(a(0)!);
      case "math.round": {
        if (a(0) === null) return null;
        const digits = a(1) ?? 0;
        const factor = 10 ** digits;
        return Math.round(a(0)! * factor) / factor;
      }
      case "math.max":
      case "math.min":
      case "math.avg": {
        const list = args.map(num);
        if (list.length === 0 || list.some((v) => v === null)) return null;
        const numbers = list as number[];
        if (call.name === "math.max") return Math.max(...numbers);
        if (call.name === "math.min") return Math.min(...numbers);
        return numbers.reduce((x, y) => x + y, 0) / numbers.length;
      }
      case "math.sum": {
        const window = windowOf(st(), 0, lengthOf(args[1], call));
        return window ? window.reduce((x, y) => x + y, 0) : null;
      }
      case "color.new":
        return typeof args[0] === "string" && a(1) !== null
          ? rgba(args[0], a(1)!)
          : null;
      case "color.rgb": {
        if ([0, 1, 2].some((i) => a(i) === null)) return null;
        const hex = `#${[0, 1, 2]
          .map((i) =>
            Math.max(0, Math.min(255, Math.round(a(i)!)))
              .toString(16)
              .padStart(2, "0"),
          )
          .join("")}`;
        return rgba(hex, a(3) ?? 0);
      }
      case "ta.sma": {
        const window = windowOf(st(), 0, lengthOf(args[1], call));
        return window
          ? window.reduce((x, y) => x + y, 0) / window.length
          : null;
      }
      case "ta.ema": {
        const length = lengthOf(args[1], call);
        return smooth(st(), a(0), length, 2 / (length + 1));
      }
      case "ta.rma": {
        const length = lengthOf(args[1], call);
        return smooth(st(), a(0), length, 1 / length);
      }
      case "ta.wma": {
        const length = lengthOf(args[1], call);
        const window = windowOf(st(), 0, length);
        if (!window) return null;
        const total = window.reduce((sum, v, k) => sum + v * (k + 1), 0);
        return total / ((length * (length + 1)) / 2);
      }
      case "ta.vwma": {
        const length = lengthOf(args[1], call);
        const s = state(key);
        s.inputs[bar] = [args[0], bars[bar].volume];
        const prices = windowOf(s, 0, length);
        const volumes = windowOf(s, 1, length);
        if (!prices || !volumes) return null;
        const volume = volumes.reduce((x, y) => x + y, 0);
        return volume === 0
          ? null
          : prices.reduce((sum, p, k) => sum + p * volumes[k], 0) / volume;
      }
      case "ta.hma": {
        const length = lengthOf(args[1], call);
        const s = st();
        const wmaOf = (k: number, n: number, index: number): number | null => {
          if (index < n - 1) return null;
          let total = 0;
          for (let j = 0; j < n; j += 1) {
            const v = num(s.inputs[index - j]?.[k]);
            if (v === null) return null;
            total += v * (n - j);
          }
          return total / ((n * (n + 1)) / 2);
        };
        const half = Math.max(1, Math.floor(length / 2));
        const root = Math.max(1, Math.floor(Math.sqrt(length)));
        const diffs = (s.extra ??= { diff: [] as (number | null)[] }).diff as (
          number | null
        )[];
        const w1 = wmaOf(0, half, bar);
        const w2 = wmaOf(0, length, bar);
        diffs[bar] = w1 === null || w2 === null ? null : 2 * w1 - w2;
        if (bar < root - 1) return null;
        let total = 0;
        for (let j = 0; j < root; j += 1) {
          const v = diffs[bar - j];
          if (v === null || v === undefined) return null;
          total += v * (root - j);
        }
        return total / ((root * (root + 1)) / 2);
      }
      case "ta.stdev": {
        const window = windowOf(st(), 0, lengthOf(args[1], call));
        if (!window) return null;
        const mean = window.reduce((x, y) => x + y, 0) / window.length;
        return Math.sqrt(
          window.reduce((sum, v) => sum + (v - mean) ** 2, 0) / window.length,
        );
      }
      case "ta.highest":
      case "ta.lowest": {
        // ta.highest(length) reads high; ta.highest(source, length) the source.
        const single = args.length === 1;
        const length = lengthOf(single ? args[0] : args[1], call);
        const s = state(key);
        s.inputs[bar] = [
          single
            ? call.name === "ta.highest"
              ? bars[bar].high
              : bars[bar].low
            : args[0],
        ];
        const window = windowOf(s, 0, length);
        if (!window) return null;
        return call.name === "ta.highest"
          ? Math.max(...window)
          : Math.min(...window);
      }
      case "ta.change":
      case "ta.mom": {
        const length = args.length > 1 ? lengthOf(args[1], call) : 1;
        const s = st();
        const now = a(0);
        const then = num(s.inputs[bar - length]?.[0]);
        return now === null || then === null ? null : now - then;
      }
      case "ta.roc": {
        const length = lengthOf(args[1], call);
        const s = st();
        const now = a(0);
        const then = num(s.inputs[bar - length]?.[0]);
        return now === null || then === null || then === 0
          ? null
          : (100 * (now - then)) / then;
      }
      case "ta.tr":
        return trueRangeAt(bar);
      case "ta.atr": {
        const length = lengthOf(args[0], call);
        return smooth(state(key), trueRangeAt(bar), length, 1 / length);
      }
      case "ta.rsi": {
        const length = lengthOf(args[1], call);
        const s = st();
        const now = a(0);
        const before = num(s.inputs[bar - 1]?.[0]);
        const extra = (s.extra ??= {
          up: {} as CallState,
          down: {} as CallState,
        });
        const up = extra.up as CallState;
        const down = extra.down as CallState;
        const change = now === null || before === null ? null : now - before;
        const gain = smooth(
          up,
          change === null ? null : Math.max(change, 0),
          length,
          1 / length,
        );
        const loss = smooth(
          down,
          change === null ? null : Math.max(-change, 0),
          length,
          1 / length,
        );
        if (gain === null || loss === null) return null;
        return loss === 0 ? 100 : 100 - 100 / (1 + gain / loss);
      }
      case "ta.macd": {
        const fast = lengthOf(args[1], call);
        const slow = lengthOf(args[2], call);
        const signalLength = lengthOf(args[3], call);
        const s = state(key);
        const extra = (s.extra ??= {
          fast: {},
          slow: {},
          signal: {},
        }) as Record<string, CallState>;
        const f = smooth(extra.fast, a(0), fast, 2 / (fast + 1));
        const sl = smooth(extra.slow, a(0), slow, 2 / (slow + 1));
        const macd = f === null || sl === null ? null : f - sl;
        const signal = smooth(
          extra.signal,
          macd,
          signalLength,
          2 / (signalLength + 1),
        );
        return [
          macd,
          signal,
          macd === null || signal === null ? null : macd - signal,
        ];
      }
      case "ta.bb": {
        const length = lengthOf(args[1], call);
        const mult = a(2) ?? 2;
        const window = windowOf(st(), 0, length);
        if (!window) return [null, null, null];
        const mean = window.reduce((x, y) => x + y, 0) / length;
        const dev = Math.sqrt(
          window.reduce((sum, v) => sum + (v - mean) ** 2, 0) / length,
        );
        return [mean, mean + mult * dev, mean - mult * dev];
      }
      case "ta.stoch": {
        const length = lengthOf(args[3], call);
        const s = st();
        const closes = windowOf(s, 0, length);
        const highs = windowOf(s, 1, length);
        const lows = windowOf(s, 2, length);
        if (!closes || !highs || !lows) return null;
        const hi = Math.max(...highs);
        const lo = Math.min(...lows);
        return hi === lo
          ? null
          : (100 * (closes[closes.length - 1] - lo)) / (hi - lo);
      }
      case "ta.cci": {
        const length = lengthOf(args[1], call);
        const window = windowOf(st(), 0, length);
        if (!window) return null;
        const mean = window.reduce((x, y) => x + y, 0) / length;
        const dev =
          window.reduce((sum, v) => sum + Math.abs(v - mean), 0) / length;
        return dev === 0
          ? null
          : (window[window.length - 1] - mean) / (0.015 * dev);
      }
      case "ta.cum": {
        const s = state(key);
        const value = a(0);
        s.previous = (s.previous ?? 0) + (value ?? 0);
        return s.previous;
      }
      case "ta.vwap": {
        const s = state(key);
        const extra = (s.extra ??= { day: "", pv: 0, v: 0 });
        const day = dayKey(bars[bar].time);
        if (extra.day !== day) Object.assign(extra, { day, pv: 0, v: 0 });
        const price = a(0);
        if (price === null) return null;
        extra.pv = (extra.pv as number) + price * bars[bar].volume;
        extra.v = (extra.v as number) + bars[bar].volume;
        return (extra.v as number) === 0
          ? null
          : (extra.pv as number) / (extra.v as number);
      }
      case "ta.crossover":
      case "ta.crossunder":
      case "ta.cross": {
        const s = st();
        const x = a(0);
        const y = a(1);
        const px = num(s.inputs[bar - 1]?.[0]);
        const py = num(s.inputs[bar - 1]?.[1]);
        if (x === null || y === null || px === null || py === null)
          return false;
        const over = x > y && px <= py;
        const under = x < y && px >= py;
        return call.name === "ta.crossover"
          ? over
          : call.name === "ta.crossunder"
            ? under
            : over || under;
      }
      case "ta.barssince": {
        const s = state(key);
        if (truthy(args[0])) s.previous = bar;
        return s.previous === undefined || s.previous === null
          ? null
          : bar - s.previous;
      }
      case "ta.valuewhen": {
        const s = state(key);
        const extra = (s.extra ??= { seen: [] as Value[] });
        const seen = extra.seen as Value[];
        if (truthy(args[0])) seen.push(args[1]);
        const occurrence = a(2) ?? 0;
        return seen[seen.length - 1 - occurrence] ?? null;
      }
      default:
        return fail(`${call.name}() is not supported`, call);
    }
  };

  const trueRangeAt = (i: number): number | null => {
    const b = bars[i];
    if (!b) return null;
    const previous = bars[i - 1];
    if (!previous) return b.high - b.low;
    return Math.max(
      b.high - b.low,
      Math.abs(b.high - previous.close),
      Math.abs(b.low - previous.close),
    );
  };

  const lookup = (scopes: Scope[], name: string): Slot | undefined => {
    for (let k = scopes.length - 1; k >= 0; k -= 1) {
      const found = scopes[k].get(name);
      if (found) return found;
    }
    return undefined;
  };

  const slotFor = (key: string, persistent: boolean): Slot => {
    let slot = slots.get(key);
    if (!slot) {
      slot = { values: [], persistent };
      slots.set(key, slot);
    }
    return slot;
  };

  // History of arbitrary expressions indexed with [n], per call path.
  const histories = new Map<string, Value[]>();

  const evaluate = (
    expr: Expr,
    scopes: Scope[],
    path: string,
    depth: number,
  ): Value => {
    switch (expr.kind) {
      case "number":
        return expr.value;
      case "string":
        return expr.value;
      case "bool":
        return expr.value;
      case "na":
        return null;
      case "name": {
        const slot = lookup(scopes, expr.name);
        if (slot) return slot.values[bar] ?? null;
        if (expr.name in COLORS) return COLORS[expr.name];
        if (expr.name === "math.pi") return Math.PI;
        if (expr.name === "ta.tr") return trueRangeAt(bar);
        const value = field(expr.name, bar);
        if (value !== undefined) return value;
        if (
          expr.name.startsWith("plot.style_") ||
          expr.name.startsWith("hline.style_")
        ) {
          return expr.name;
        }
        return fail(`unknown name "${expr.name}"`, expr);
      }
      case "index": {
        const offsetValue = num(evaluate(expr.offset, scopes, path, depth));
        if (
          offsetValue === null ||
          !Number.isInteger(offsetValue) ||
          offsetValue < 0
        ) {
          return fail(
            "a history offset must be a whole number of 0 or more",
            expr.offset,
          );
        }
        const target = expr.target;
        if (target.kind === "name") {
          const slot = lookup(scopes, target.name);
          if (slot) return slot.values[bar - offsetValue] ?? null;
          const value = field(target.name, bar - offsetValue);
          if (value !== undefined) return value ?? null;
        }
        const key = `${path}/${expr.id}`;
        let history = histories.get(key);
        if (!history) {
          history = [];
          histories.set(key, history);
        }
        history[bar] = evaluate(target, scopes, path, depth);
        return history[bar - offsetValue] ?? null;
      }
      case "unary": {
        const value = evaluate(expr.operand, scopes, path, depth);
        if (expr.op === "not") return !truthy(value);
        const n = num(value);
        return n === null ? null : expr.op === "-" ? -n : n;
      }
      case "binary": {
        if (expr.op === "and") {
          return (
            truthy(evaluate(expr.left, scopes, path, depth)) &&
            truthy(evaluate(expr.right, scopes, path, depth))
          );
        }
        if (expr.op === "or") {
          return (
            truthy(evaluate(expr.left, scopes, path, depth)) ||
            truthy(evaluate(expr.right, scopes, path, depth))
          );
        }
        const left = evaluate(expr.left, scopes, path, depth);
        const right = evaluate(expr.right, scopes, path, depth);
        if (expr.op === "==" || expr.op === "!=") {
          const same =
            typeof left === "string" || typeof right === "string"
              ? left === right
              : num(left) !== null && num(left) === num(right);
          return expr.op === "=="
            ? same
            : !same && left !== null && right !== null;
        }
        const x = num(left);
        const y = num(right);
        if (x === null || y === null) {
          return ["<", ">", "<=", ">="].includes(expr.op) ? false : null;
        }
        switch (expr.op) {
          case "+":
            return x + y;
          case "-":
            return x - y;
          case "*":
            return x * y;
          case "/":
            return y === 0 ? null : x / y;
          case "%":
            return y === 0 ? null : x % y;
          case "<":
            return x < y;
          case ">":
            return x > y;
          case "<=":
            return x <= y;
          case ">=":
            return x >= y;
          default:
            return fail(`unknown operator ${expr.op}`, expr);
        }
      }
      case "ternary":
        return truthy(evaluate(expr.test, scopes, path, depth))
          ? evaluate(expr.then, scopes, path, depth)
          : evaluate(expr.otherwise, scopes, path, depth);
      case "tuple":
        return expr.items.map((item) => evaluate(item, scopes, path, depth));
      case "call":
        return callExpr(expr, scopes, path, depth);
      case "block":
        return runStmt(expr.statement, scopes, path, depth);
    }
  };

  const callExpr = (
    expr: Extract<Expr, { kind: "call" }>,
    scopes: Scope[],
    path: string,
    depth: number,
  ): Value => {
    if (
      expr.name === "indicator" ||
      expr.name === "study" ||
      expr.name === "hline"
    ) {
      return null; // read at compile time
    }
    if (expr.name === "input" || expr.name.startsWith("input.")) {
      const key = program.inputKeys.get(expr.id);
      if (key === undefined)
        return evaluate(argument(expr, 0, "defval")!, scopes, path, depth);
      const declared = program.inputs.find((item) => item.key === key);
      const value = params[key] ?? declared?.default ?? null;
      if (declared?.source) {
        return field(SOURCES[Math.round(num(value) ?? 0)] ?? "close", bar);
      }
      if (
        declared?.options &&
        declared.options.length === 2 &&
        declared.options[1].label === "true"
      ) {
        return value === 1;
      }
      return value;
    }
    if (expr.name === "plot") {
      const key = program.plotKeys.get(expr.id);
      if (!key) return null;
      const series = argument(expr, 0, "series");
      const value = series ? num(evaluate(series, scopes, path, depth)) : null;
      values[key][bar] = value;
      const colorExpr = argument(expr, 2, "color");
      if (colorExpr && constant(colorExpr) === undefined) {
        const color = evaluate(colorExpr, scopes, path, depth);
        colors[key][bar] = typeof color === "string" ? color : null;
      }
      return null;
    }
    const user = functions.get(expr.name);
    if (user) {
      if (depth >= MAX_CALL_DEPTH)
        return fail("functions call each other too deeply", expr);
      if (expr.args.length !== user.params.length) {
        return fail(
          `${expr.name}() takes ${user.params.length} arguments`,
          expr,
        );
      }
      const local: Scope = new Map();
      const callPath = `${path}/${expr.id}`;
      user.params.forEach((param, k) => {
        const slot = slotFor(`${callPath}/param:${param}`, false);
        slot.values[bar] = evaluate(expr.args[k], scopes, path, depth);
        local.set(param, slot);
      });
      // A function sees globals and its own parameters, not the caller's locals.
      return runBlock(user.body, [scopes[0], local], callPath, depth + 1);
    }
    const args = expr.args.map((arg) => evaluate(arg, scopes, path, depth));
    // Named arguments of built-ins are taken in their documented order.
    for (const [name, arg] of Object.entries(expr.named)) {
      const order: Record<string, number> = { source: 0, length: 1, mult: 2 };
      args[order[name] ?? args.length] = evaluate(arg, scopes, path, depth);
    }
    return callBuiltin(expr, args, path);
  };

  /** Runs statements; the value is the last expression's (a function's result). */
  const runBlock = (
    body: Stmt[],
    scopes: Scope[],
    path: string,
    depth: number,
  ): Value => {
    let last: Value = null;
    const scope: Scope = new Map();
    const inner = [...scopes, scope];
    for (const stmt of body) {
      last = runStmt(stmt, inner, path, depth);
    }
    return last;
  };

  const runStmt = (
    stmt: Stmt,
    scopes: Scope[],
    path: string,
    depth: number,
  ): Value => {
    const scope = scopes[scopes.length - 1];
    switch (stmt.kind) {
      case "function":
        functions.set(stmt.name, { params: stmt.params, body: stmt.body });
        return null;
      case "declare": {
        const slotsHere = stmt.names.map((name) => {
          const slot = slotFor(`${path}/${stmt.id}:${name}`, stmt.persistent);
          scope.set(name, slot);
          return slot;
        });
        if (stmt.persistent && slotsHere[0].values.length > 0) {
          // `var`: set on the first bar it runs, then carried forward.
          const slot = slotsHere[0];
          if (slot.values[bar] === undefined) {
            slot.values[bar] = slot.values[bar - 1] ?? null;
          }
          return slot.values[bar];
        }
        const value = evaluate(stmt.value, scopes, path, depth);
        if (stmt.names.length === 1) {
          slotsHere[0].values[bar] = value;
        } else {
          if (!Array.isArray(value) || value.length !== stmt.names.length) {
            return fail(`expected ${stmt.names.length} values`, stmt);
          }
          slotsHere.forEach((slot, k) => {
            slot.values[bar] = value[k];
          });
        }
        return value;
      }
      case "assign": {
        const slot = lookup(scopes, stmt.name);
        if (!slot) return fail(`"${stmt.name}" is not declared`, stmt);
        const value = evaluate(stmt.value, scopes, path, depth);
        if (stmt.op === ":=") {
          slot.values[bar] = value;
        } else {
          const current = num(slot.values[bar]);
          const change = num(value);
          if (current === null || change === null) {
            slot.values[bar] = null;
          } else {
            const op = stmt.op[0];
            slot.values[bar] =
              op === "+"
                ? current + change
                : op === "-"
                  ? current - change
                  : op === "*"
                    ? current * change
                    : op === "/"
                      ? change === 0
                        ? null
                        : current / change
                      : change === 0
                        ? null
                        : current % change;
          }
        }
        return slot.values[bar];
      }
      case "if":
        return truthy(evaluate(stmt.test, scopes, path, depth))
          ? runBlock(stmt.body, scopes, path, depth)
          : runBlock(stmt.otherwise, scopes, path, depth);
      case "for": {
        const from = num(evaluate(stmt.from, scopes, path, depth));
        const to = num(evaluate(stmt.to, scopes, path, depth));
        const stepValue = stmt.step
          ? num(evaluate(stmt.step, scopes, path, depth))
          : null;
        if (from === null || to === null) return null;
        const step = stepValue ?? (to >= from ? 1 : -1);
        if (step === 0) return fail("a for loop's step cannot be 0", stmt);
        const counter = slotFor(`${path}/${stmt.id}:${stmt.name}`, false);
        const loopScope: Scope = new Map([[stmt.name, counter]]);
        let last: Value = null;
        for (let i = from; step > 0 ? i <= to : i >= to; i += step) {
          steps += 1;
          if (steps > MAX_LOOP_STEPS) throw new Stop();
          counter.values[bar] = i;
          try {
            last = runBlock(stmt.body, [...scopes, loopScope], path, depth);
          } catch (signal) {
            if (signal instanceof BreakSignal) break;
            if (signal instanceof ContinueSignal) continue;
            throw signal;
          }
        }
        return last;
      }
      case "while": {
        let last: Value = null;
        while (truthy(evaluate(stmt.test, scopes, path, depth))) {
          steps += 1;
          if (steps > MAX_LOOP_STEPS) throw new Stop();
          try {
            last = runBlock(stmt.body, scopes, path, depth);
          } catch (signal) {
            if (signal instanceof BreakSignal) break;
            if (signal instanceof ContinueSignal) continue;
            throw signal;
          }
        }
        return last;
      }
      case "break":
        throw new BreakSignal();
      case "continue":
        throw new ContinueSignal();
      case "expr":
        return evaluate(stmt.value, scopes, path, depth);
    }
  };

  try {
    for (bar = 0; bar < bars.length; bar += 1) {
      // `var` values carry over to this bar before the script runs.
      for (const slot of slots.values()) {
        if (slot.persistent && bar > 0 && slot.values[bar] === undefined) {
          slot.values[bar] = slot.values[bar - 1] ?? null;
        }
      }
      runBlock(program.body, [], "", 0);
    }
  } catch (error) {
    if (error instanceof Stop) {
      throw new PineError(
        `the script's loops ran more than ${MAX_LOOP_STEPS.toLocaleString("en-US")} times`,
        1,
        1,
      );
    }
    throw error;
  }
  return { values, colors };
}

// --- as an indicator ----------------------------------------------------------------

/** Per-bar colours of the latest run, by its values object (dynamic plot colours). */
export const PINE_COLORS = new WeakMap<
  Record<string, Series>,
  Record<string, (string | null)[]>
>();

const programs = new Map<string, PineProgram | PineError>();

/** A script compiled once per source text. */
export function pineProgram(source: string): PineProgram {
  let found = programs.get(source);
  if (!found) {
    try {
      found = compilePine(source);
    } catch (error) {
      found =
        error instanceof PineError
          ? error
          : new PineError(
              error instanceof Error ? error.message : String(error),
              1,
              1,
            );
    }
    if (programs.size > 50) programs.clear();
    programs.set(source, found);
  }
  if (found instanceof PineError) throw found;
  return found;
}

/** An indicator definition for a Pine script; a broken script draws nothing. */
export function pineDefinition(source: string): IndicatorDefinition {
  let program: PineProgram | null = null;
  try {
    program = pineProgram(source);
  } catch {
    program = null;
  }
  if (!program) {
    return {
      id: "pine",
      name: { en: "Pine (error)", ko: "Pine(오류)" },
      short: "Pine",
      category: "custom",
      pane: "separate",
      inputs: [],
      outputs: [{ key: "plot0", label: "Pine", color: DEFAULT_PLOT_COLOR }],
      compute: (bars) => ({ plot0: bars.map(() => null) }),
    };
  }
  const compiled = program;
  const outputs: IndicatorOutput[] = compiled.plots.map((plot) => ({
    key: plot.key,
    label: plot.title,
    color: plot.color,
    ...(plot.kind === "histogram" ? { kind: "histogram" as const } : {}),
  }));
  return {
    id: "pine",
    name: { en: compiled.title, ko: compiled.title },
    short: compiled.short,
    category: "custom",
    pane: compiled.overlay ? "price" : "separate",
    inputs: compiled.inputs.map((input) => {
      const copy = { ...input };
      delete copy.source;
      return copy;
    }),
    outputs,
    ...(compiled.levels.length > 0 ? { levels: compiled.levels } : {}),
    compute: (bars, params, context) => {
      try {
        const result = runPine(compiled, bars, params, context);
        PINE_COLORS.set(result.values, result.colors);
        return result.values;
      } catch {
        return Object.fromEntries(
          outputs.map((o) => [o.key, bars.map(() => null)]),
        );
      }
    },
  };
}

/** Line widths a script set with plot(linewidth=…), as a starting style. */
export function pineWidths(source: string): Record<string, number> {
  try {
    return Object.fromEntries(
      pineProgram(source)
        .plots.filter((plot) => plot.width > 1)
        .map((plot) => [plot.key, plot.width]),
    );
  } catch {
    return {};
  }
}

export const PINE_EXAMPLE = `//@version=5
indicator("EMA Cross", overlay=true)
fastLength = input.int(9, "Fast length", minval=1)
slowLength = input.int(21, "Slow length", minval=1)
src = input.source(close, "Source")
fast = ta.ema(src, fastLength)
slow = ta.ema(src, slowLength)
plot(fast, "Fast", color=color.orange)
plot(slow, "Slow", color=fast > slow ? color.green : color.red, linewidth=2)
`;
