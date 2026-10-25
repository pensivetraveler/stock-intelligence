/**
 * Pine Script (ADR 0040): a TradingView Pine v5/v6 subset run in the browser, without
 * `eval`. The script runs once per bar, oldest first, as on TradingView: variables
 * keep one value per bar so `x[1]` reads the previous bar, `var` keeps its value
 * across bars, and each `ta.*` call has its own state. Every function reads the
 * current bar and earlier ones only, so a script cannot see the future.
 *
 * Supported: `indicator()` and `strategy()`, inputs, `plot`, `hline`, `plotshape`,
 * `plotchar`, `plotarrow`, `bgcolor`, `barcolor`, `fill`, drawings (`line`, `label`,
 * `box`, `table`, `linefill`), arrays, strings, `request.security` (other timeframes
 * and symbols, aligned without look-ahead), `switch`, `for … in`, user functions and
 * methods, and the `ta.*`, `math.*`, `str.*`, `color.*` functions in PINE_FUNCTIONS.
 * Strategies are backtests on the chart (pine-strategy.ts); they never place orders.
 * Loops are capped so a script cannot hang the app.
 */
import type {
  IndicatorContext,
  IndicatorDefinition,
  IndicatorInput,
  IndicatorOutput,
  OhlcvBar,
  Series,
} from "./indicators";
import {
  DrawingStore,
  emptyGraphics,
  sizeOf,
  type PineFill,
  type PineGraphics,
} from "./pine-drawings";
import {
  callArray,
  callString,
  isNa,
  methodNamespace,
  namedConstant,
  num,
  PineArray,
  PinePoint,
  PineRef,
  PineValueError,
  toText,
  truthy,
  type Value,
} from "./pine-objects";
import { parsePine, PineError, type Expr, type Stmt } from "./pine-parser";
import {
  DEFAULT_STRATEGY,
  StrategyBroker,
  type StrategyReport,
  type StrategySettings,
} from "./pine-strategy";

export { MAX_PINE_LENGTH, PineError } from "./pine-parser";

const MAX_LOOP_STEPS = 2_000_000; // per run, over all bars
const MAX_CALL_DEPTH = 32;

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
const TRANSPARENT = "rgba(0, 0, 0, 0)";

/**
 * `input.timeframe` choices and the Pine timeframe strings `request.security` takes,
 * with the app's timeframe for each. "" is the chart's own.
 */
const TIMEFRAME_CHOICES: { pine: string; app: string; label: string }[] = [
  { pine: "", app: "", label: "Chart" },
  { pine: "1", app: "1m", label: "1m" },
  { pine: "2", app: "2m", label: "2m" },
  { pine: "3", app: "3m", label: "3m" },
  { pine: "5", app: "5m", label: "5m" },
  { pine: "10", app: "10m", label: "10m" },
  { pine: "15", app: "15m", label: "15m" },
  { pine: "30", app: "30m", label: "30m" },
  { pine: "60", app: "60m", label: "1h" },
  { pine: "120", app: "120m", label: "2h" },
  { pine: "240", app: "240m", label: "4h" },
  { pine: "D", app: "1d", label: "1D" },
  { pine: "W", app: "1w", label: "1W" },
  { pine: "M", app: "1M", label: "1M" },
];

/** The app timeframe ("5m", "1d", "1M") for a Pine timeframe ("5", "D", "1M"), or null. */
export function appTimeframe(pine: string): string | null {
  const text = pine.trim().toUpperCase();
  const aliases: Record<string, string> = {
    "1D": "D",
    "1W": "W",
    "1M": "M",
    "1H": "60",
    "2H": "120",
    "4H": "240",
  };
  const name = aliases[text] ?? text;
  return TIMEFRAME_CHOICES.find((item) => item.pine === name)?.app ?? null;
}

/** The Pine timeframe string (`timeframe.period`) for an app timeframe. */
export function pineTimeframe(app: string): string {
  return TIMEFRAME_CHOICES.find((item) => item.app === app)?.pine ?? app;
}

/** Function names a script may call, for the editor's help. */
export const PINE_FUNCTIONS = [
  "indicator",
  "strategy",
  "plot",
  "hline",
  "plotshape",
  "plotchar",
  "plotarrow",
  "bgcolor",
  "barcolor",
  "fill",
  "input.*",
  "request.security",
  "request.security_lower_tf",
  "line.*",
  "label.*",
  "box.*",
  "table.*",
  "linefill.*",
  "chart.point.*",
  "array.*",
  "str.*",
  "strategy.entry",
  "strategy.order",
  "strategy.exit",
  "strategy.close",
  "strategy.close_all",
  "strategy.cancel",
  "nz",
  "na",
  "fixnan",
  "timestamp",
  "ta.sma",
  "ta.ema",
  "ta.rma",
  "ta.wma",
  "ta.vwma",
  "ta.hma",
  "ta.alma",
  "ta.swma",
  "ta.rsi",
  "ta.macd",
  "ta.bb",
  "ta.kc",
  "ta.stoch",
  "ta.stdev",
  "ta.variance",
  "ta.dev",
  "ta.median",
  "ta.highest",
  "ta.lowest",
  "ta.highestbars",
  "ta.lowestbars",
  "ta.range",
  "ta.change",
  "ta.mom",
  "ta.roc",
  "ta.atr",
  "ta.tr",
  "ta.cci",
  "ta.mfi",
  "ta.wpr",
  "ta.vwap",
  "ta.obv",
  "ta.accdist",
  "ta.cum",
  "ta.linreg",
  "ta.correlation",
  "ta.percentrank",
  "ta.supertrend",
  "ta.dmi",
  "ta.sar",
  "ta.pivothigh",
  "ta.pivotlow",
  "ta.rising",
  "ta.falling",
  "ta.crossover",
  "ta.crossunder",
  "ta.cross",
  "ta.barssince",
  "ta.valuewhen",
  "math.*",
  "color.new",
  "color.rgb",
  "color.from_gradient",
] as const;

/** Parameter names of built-ins, so named arguments land in the right place. */
const SIGNATURES: Record<string, string[]> = {
  plot: [
    "series",
    "title",
    "color",
    "linewidth",
    "style",
    "trackprice",
    "histbase",
    "offset",
    "join",
    "editable",
    "show_last",
    "display",
  ],
  hline: [
    "price",
    "title",
    "color",
    "linestyle",
    "linewidth",
    "editable",
    "display",
  ],
  plotshape: [
    "series",
    "title",
    "style",
    "location",
    "color",
    "offset",
    "text",
    "textcolor",
    "editable",
    "size",
    "show_last",
    "display",
  ],
  plotchar: [
    "series",
    "title",
    "char",
    "location",
    "color",
    "offset",
    "text",
    "textcolor",
    "editable",
    "size",
    "show_last",
    "display",
  ],
  plotarrow: [
    "series",
    "title",
    "colorup",
    "colordown",
    "offset",
    "minheight",
    "maxheight",
    "editable",
    "show_last",
    "display",
  ],
  bgcolor: ["color", "offset", "editable", "show_last", "title", "display"],
  barcolor: ["color", "offset", "editable", "show_last", "title", "display"],
  fill: [
    "plot1",
    "plot2",
    "color",
    "title",
    "editable",
    "show_last",
    "fillgaps",
    "display",
  ],
  "line.new": [
    "x1",
    "y1",
    "x2",
    "y2",
    "xloc",
    "extend",
    "color",
    "style",
    "width",
  ],
  "label.new": [
    "x",
    "y",
    "text",
    "xloc",
    "yloc",
    "color",
    "style",
    "textcolor",
    "size",
    "textalign",
    "tooltip",
    "text_font_family",
  ],
  "box.new": [
    "left",
    "top",
    "right",
    "bottom",
    "border_color",
    "border_width",
    "border_style",
    "extend",
    "xloc",
    "bgcolor",
    "text",
    "text_size",
    "text_color",
    "text_halign",
    "text_valign",
    "text_wrap",
    "text_font_family",
  ],
  "table.new": [
    "position",
    "columns",
    "rows",
    "bgcolor",
    "frame_color",
    "frame_width",
    "border_color",
    "border_width",
  ],
  "table.cell": [
    "table_id",
    "column",
    "row",
    "text",
    "width",
    "height",
    "text_color",
    "text_halign",
    "text_valign",
    "text_size",
    "bgcolor",
    "tooltip",
    "text_font_family",
  ],
  "linefill.new": ["line1", "line2", "color"],
  "chart.point.new": ["time", "index", "price"],
  "chart.point.from_index": ["index", "price"],
  "chart.point.from_time": ["time", "price"],
  "chart.point.now": ["price"],
  "strategy.entry": [
    "id",
    "direction",
    "qty",
    "limit",
    "stop",
    "oca_name",
    "oca_type",
    "comment",
    "alert_message",
    "disable_alert",
  ],
  "strategy.order": [
    "id",
    "direction",
    "qty",
    "limit",
    "stop",
    "oca_name",
    "oca_type",
    "comment",
    "alert_message",
    "disable_alert",
  ],
  "strategy.exit": [
    "id",
    "from_entry",
    "qty",
    "qty_percent",
    "profit",
    "limit",
    "loss",
    "stop",
    "trail_price",
    "trail_points",
    "trail_offset",
    "oca_name",
    "comment",
    "comment_profit",
    "comment_loss",
    "comment_trailing",
    "alert_message",
  ],
  "strategy.close": [
    "id",
    "comment",
    "qty",
    "qty_percent",
    "alert_message",
    "immediately",
    "disable_alert",
  ],
  "strategy.close_all": [
    "comment",
    "alert_message",
    "immediately",
    "disable_alert",
  ],
  "strategy.cancel": ["id"],
  "request.security": [
    "symbol",
    "timeframe",
    "expression",
    "gaps",
    "lookahead",
    "ignore_invalid_symbol",
    "currency",
    "calc_bars_count",
  ],
  "request.security_lower_tf": [
    "symbol",
    "timeframe",
    "expression",
    "ignore_invalid_symbol",
    "currency",
    "ignore_invalid_timeframe",
    "calc_bars_count",
  ],
  "str.tostring": ["value", "format"],
  "math.round": ["number", "precision"],
  "color.new": ["color", "transp"],
  "color.rgb": ["red", "green", "blue", "transp"],
  "color.from_gradient": [
    "value",
    "bottom_value",
    "top_value",
    "bottom_color",
    "top_color",
  ],
  "ta.pivothigh": ["source", "leftbars", "rightbars"],
  "ta.pivotlow": ["source", "leftbars", "rightbars"],
  "ta.alma": ["series", "length", "offset", "sigma", "floor"],
  "ta.linreg": ["source", "length", "offset"],
  "ta.supertrend": ["factor", "atrPeriod"],
  "ta.dmi": ["diLength", "adxSmoothing"],
  "ta.sar": ["start", "inc", "max"],
  "ta.kc": ["series", "length", "mult", "useTrueRange"],
};
/** Fallback order for `ta.*` named arguments not in SIGNATURES. */
const TA_ORDER: Record<string, number> = {
  source: 0,
  series: 0,
  length: 1,
  mult: 2,
};

/** request.* functions that need data SIW does not have. */
const UNSUPPORTED_REQUESTS = [
  "request.financial",
  "request.dividends",
  "request.earnings",
  "request.splits",
  "request.quandl",
  "request.economic",
  "request.seed",
  "request.currency_rate",
];

// --- compile: what the script declares --------------------------------------------

export interface PinePlot {
  key: string;
  title: string;
  color: string;
  kind: "line" | "histogram";
  width: number;
  /** `display=display.none`: computed (e.g. for fill) but not drawn at first. */
  hidden: boolean;
}

/** A request's symbol or timeframe: fixed, from an input, or the chart's own. */
export type RequestPart =
  | { kind: "current" }
  | { kind: "value"; value: string }
  | { kind: "input"; key: string };

export interface PineRequestSpec {
  id: number;
  lower: boolean;
  symbol: RequestPart;
  timeframe: RequestPart;
  gaps: boolean;
  /** `lookahead_on`, allowed only for `x[1]`-style expressions (no future values). */
  lookahead: boolean;
}

export interface PineProgram {
  body: Stmt[];
  title: string;
  short: string;
  overlay: boolean;
  inputs: (IndicatorInput & { source?: boolean; choices?: string[] })[];
  plots: PinePlot[];
  levels: number[];
  /** Input key per input call node, and plot key per plot call node. */
  inputKeys: Map<number, string>;
  plotKeys: Map<number, string>;
  /** hline index per hline call node, and each hline's price (null if not fixed). */
  hlines: Map<number, number>;
  hlinePrices: (number | null)[];
  requests: PineRequestSpec[];
  strategy: StrategySettings | null;
  limits: { line: number; label: number; box: number };
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
      return namedConstant(expr.name);
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
      case "block":
        stmts([e.statement], false);
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
        case "forin":
          expr(s.source, false);
          stmts(s.body, false);
          break;
        case "while":
          expr(s.test, false);
          stmts(s.body, false);
          break;
        case "switch":
          if (s.subject) expr(s.subject, false);
          for (const item of s.cases) {
            if (item.test) expr(item.test, false);
            stmts(item.body, false);
          }
          break;
        case "function":
          s.defaults.forEach((d) => d && expr(d, false));
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

/** Calls that draw something; a script needs at least one. */
const DRAWING_CALLS = new Set([
  "plot",
  "plotshape",
  "plotchar",
  "plotarrow",
  "bgcolor",
  "barcolor",
  "fill",
  "hline",
  "line.new",
  "label.new",
  "box.new",
  "table.new",
]);

const clampCount = (value: Value | undefined, fallback: number) => {
  const n = num(value ?? null);
  return n === null ? fallback : Math.max(1, Math.min(500, Math.round(n)));
};

function strategySettings(
  call: Extract<Expr, { kind: "call" }>,
): StrategySettings {
  const named = (name: string) => constant(call.named[name]);
  const text = (name: string, fallback: string) => {
    const value = named(name);
    return typeof value === "string" ? value : fallback;
  };
  const number = (name: string, fallback: number) => {
    const value = named(name);
    return typeof value === "number" ? value : fallback;
  };
  const qtyType = text("default_qty_type", "fixed");
  const commission = text("commission_type", "percent");
  return {
    initialCapital: number("initial_capital", DEFAULT_STRATEGY.initialCapital),
    qtyType:
      qtyType === "cash" || qtyType === "percent_of_equity" ? qtyType : "fixed",
    qtyValue: number("default_qty_value", DEFAULT_STRATEGY.qtyValue),
    commissionType:
      commission === "cash_per_contract" || commission === "cash_per_order"
        ? commission
        : "percent",
    commissionValue: number("commission_value", 0),
    slippage: number("slippage", 0),
    pyramiding: number("pyramiding", 0),
    processOnClose: named("process_orders_on_close") === true,
    closeEntriesRule:
      text("close_entries_rule", "FIFO") === "ANY" ? "ANY" : "FIFO",
    currency: text("currency", "").replace(/^currency\.(NONE)?/, ""),
  };
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
    hlines: new Map(),
    hlinePrices: [],
    requests: [],
    strategy: null,
    limits: { line: 50, label: 50, box: 50 },
  };
  // Top-level `name = value` declarations, to read a request's symbol or timeframe.
  const declared = new Map<string, Expr>();
  for (const stmt of body) {
    if (stmt.kind === "declare" && stmt.names.length === 1) {
      declared.set(stmt.names[0], stmt.value);
    }
  }
  const inputKeys = new Set<string>();
  let seenDeclaration = false;
  let draws = false;
  const requestCalls: Extract<Expr, { kind: "call" }>[] = [];
  walk(body, (expr, topLevel) => {
    if (expr.kind !== "call") return;
    if (DRAWING_CALLS.has(expr.name)) draws = true;
    if (expr.name.startsWith("strategy.risk.")) {
      throw new PineError(
        `${expr.name} is not supported`,
        expr.line,
        expr.column,
      );
    }
    if (UNSUPPORTED_REQUESTS.includes(expr.name)) {
      throw new PineError(
        `${expr.name} is not supported: SIW has no such data`,
        expr.line,
        expr.column,
      );
    }
    if (
      expr.name === "plotcandle" ||
      expr.name === "plotbar" ||
      expr.name.startsWith("polyline.")
    ) {
      throw new PineError(
        `${expr.name} is not supported`,
        expr.line,
        expr.column,
      );
    }
    if (
      expr.name === "request.security" ||
      expr.name === "request.security_lower_tf"
    ) {
      requestCalls.push(expr);
      return;
    }
    if (
      expr.name === "indicator" ||
      expr.name === "study" ||
      expr.name === "strategy"
    ) {
      seenDeclaration = true;
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
      program.limits = {
        line: clampCount(constant(expr.named.max_lines_count), 50),
        label: clampCount(constant(expr.named.max_labels_count), 50),
        box: clampCount(constant(expr.named.max_boxes_count), 50),
      };
      if (expr.name === "strategy") {
        program.strategy = strategySettings(expr);
        draws = true;
      }
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
      if (kind === "timeframe") {
        const wanted = typeof defval === "string" ? defval : "";
        const choice = wanted === "" ? "" : appTimeframe(wanted);
        const index = Math.max(
          0,
          TIMEFRAME_CHOICES.findIndex((item) => item.app === choice),
        );
        program.inputs.push({
          key,
          label,
          default: index,
          min: 0,
          max: TIMEFRAME_CHOICES.length - 1,
          step: 1,
          choices: TIMEFRAME_CHOICES.map((item) => item.pine),
          options: TIMEFRAME_CHOICES.map((item, value) => ({
            value,
            label: item.label,
          })),
        });
        return;
      }
      const optionsExpr = expr.named.options;
      if (
        (kind === "string" || kind === "auto") &&
        optionsExpr?.kind === "tuple" &&
        optionsExpr.items.every((item) => item.kind === "string")
      ) {
        // input.string(…, options=[…]) becomes a choice list.
        const choices = optionsExpr.items.map((item) =>
          item.kind === "string" ? item.value : "",
        );
        const index = Math.max(0, choices.indexOf(String(defval)));
        program.inputs.push({
          key,
          label,
          default: index,
          min: 0,
          max: choices.length - 1,
          step: 1,
          choices,
          options: choices.map((value, i) => ({ value: i, label: value })),
        });
        return;
      }
      if (
        kind === "color" ||
        kind === "string" ||
        kind === "symbol" ||
        kind === "session" ||
        kind === "text_area" ||
        kind === "time" ||
        (kind === "auto" && typeof defval === "string")
      ) {
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
      const display = constant(expr.named.display);
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
        hidden: display === "display.none",
      });
      return;
    }
    if (expr.name === "hline") {
      const price = constant(argument(expr, 0, "price"));
      program.hlines.set(expr.id, program.hlinePrices.length);
      program.hlinePrices.push(typeof price === "number" ? price : null);
      if (typeof price === "number") program.levels.push(price);
    }
  });
  if (!seenDeclaration) {
    throw new PineError('the script needs indicator("Title")', 1, 1);
  }
  if (!draws) {
    throw new PineError("the script draws nothing: add plot(…)", 1, 1);
  }
  for (const call of requestCalls) {
    program.requests.push(requestSpec(call, declared, program));
  }
  return program;
}

/** A request's symbol and timeframe, which must be fixed (or inputs) to fetch the data. */
function requestSpec(
  call: Extract<Expr, { kind: "call" }>,
  declared: Map<string, Expr>,
  program: PineProgram,
): PineRequestSpec {
  const lower = call.name === "request.security_lower_tf";
  const signature = SIGNATURES[call.name];
  const arg = (name: string) => argument(call, signature.indexOf(name), name);
  const part = (
    expr: Expr | undefined,
    what: string,
    depth = 0,
  ): RequestPart => {
    if (!expr || depth > 5) {
      throw new PineError(`request needs a ${what}`, call.line, call.column);
    }
    if (expr.kind === "string") return { kind: "value", value: expr.value };
    if (expr.kind === "name") {
      if (
        expr.name === "syminfo.tickerid" ||
        expr.name === "syminfo.ticker" ||
        expr.name === "timeframe.period"
      ) {
        return { kind: "current" };
      }
      const value = declared.get(expr.name);
      if (value) return part(value, what, depth + 1);
    }
    if (expr.kind === "call" && expr.name.startsWith("input")) {
      const key = program.inputKeys.get(expr.id);
      if (key) return { kind: "input", key };
      const fixed = constant(argument(expr, 0, "defval"));
      if (typeof fixed === "string") {
        return fixed === ""
          ? { kind: "current" }
          : { kind: "value", value: fixed };
      }
    }
    throw new PineError(
      `request.security needs a fixed ${what} (a text, an input or syminfo.tickerid/timeframe.period)`,
      expr.line,
      expr.column,
    );
  };
  const symbol = part(arg("symbol"), "symbol");
  const timeframe = part(arg("timeframe"), "timeframe");
  if (timeframe.kind === "value" && appTimeframe(timeframe.value) === null) {
    throw new PineError(
      `timeframe "${timeframe.value}" is not supported; use ${TIMEFRAME_CHOICES.slice(
        1,
      )
        .map((item) => item.pine)
        .join(", ")}`,
      call.line,
      call.column,
    );
  }
  const lookahead = constant(arg("lookahead")) === "barmerge.lookahead_on";
  if (lookahead) {
    // lookahead_on shows a larger bar's final value from its start: only safe for
    // values of earlier bars (x[1]), so other expressions are refused.
    const expression = arg("expression");
    const offset =
      expression?.kind === "index" ? constant(expression.offset) : null;
    if (typeof offset !== "number" || offset < 1) {
      const at = expression ?? call;
      throw new PineError(
        "lookahead_on would show future values; use it only with x[1] (or more bars back)",
        at.line,
        at.column,
      );
    }
  }
  return {
    id: call.id,
    lower,
    symbol,
    timeframe,
    gaps: !lower && constant(arg("gaps")) === "barmerge.gaps_on",
    lookahead,
  };
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

function rgbaParts(color: string): [number, number, number, number] {
  const match = /^rgba\((\d+), (\d+), (\d+), ([\d.]+)\)/.exec(color);
  if (match) {
    return [
      Number(match[1]),
      Number(match[2]),
      Number(match[3]),
      Number(match[4]),
    ];
  }
  const hex = /^#([0-9a-fA-F]{6})([0-9a-fA-F]{2})?/.exec(color);
  if (!hex) return [41, 98, 255, 1];
  const part = (offset: number) =>
    parseInt(hex[1].slice(offset, offset + 2), 16);
  const alpha = hex[2] ? parseInt(hex[2], 16) / 255 : 1;
  return [part(0), part(2), part(4), alpha];
}

function rgba(color: string, transparency: number): string {
  const [r, g, b] = rgbaParts(color);
  const alpha = Math.max(0, Math.min(100, 100 - transparency)) / 100;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

const isTransparent = (color: string) => /, 0\)$/.test(color);

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
  defaults: (Expr | null)[];
  method: boolean;
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

/** Bars of another timeframe or symbol for request.security, and when each was final. */
export interface PineRequestData {
  bars: OhlcvBar[];
  availableAt: number[];
}

/** What a Pine run needs besides bars; all optional. */
export interface PineContext extends IndicatorContext {
  /** Ticker (`syminfo.ticker`) and app timeframe of the chart ("5m", "1d"). */
  symbol?: string;
  timeframe?: string;
  /** Price decimals: `syminfo.mintick` is 10^-decimals. */
  priceDecimals?: number;
  /** Each bar's end (epoch seconds), to align request.security values. */
  ends?: number[];
  /** Data for request.security, by `pineRequestKey`. */
  requests?: Record<string, PineRequestData>;
}

interface Secondary {
  target: number;
  capture: Value[];
}

export interface PineResult {
  values: Record<string, Series>;
  colors: Record<string, (string | null)[]>;
  graphics: PineGraphics;
  strategy: StrategyReport | null;
  /** request.security data keys that were needed but not given. */
  missing: string[];
}

/** The key of a request's data: normalized symbol ("" = chart's) and app timeframe. */
export function pineRequestKey(symbol: string, timeframe: string): string {
  return `${symbol}|${timeframe}`;
}

function normalSymbol(symbol: string, current: string | undefined): string {
  const bare = symbol
    .trim()
    .toUpperCase()
    .replace(/^[A-Z_]+:/, "");
  return current && bare === current.toUpperCase() ? "" : bare;
}

/** The data a request needs, or "self" when it is the chart's own symbol and timeframe. */
function resolveRequest(
  spec: PineRequestSpec,
  program: PineProgram,
  params: Record<string, number>,
  context: PineContext | undefined,
): { symbol: string; timeframe: string } | "self" {
  const value = (part: RequestPart): string => {
    if (part.kind === "current") return "";
    if (part.kind === "value") return part.value;
    const input = program.inputs.find((item) => item.key === part.key);
    const index = Math.round(params[part.key] ?? input?.default ?? 0);
    return input?.choices?.[index] ?? "";
  };
  const symbol = normalSymbol(value(spec.symbol), context?.symbol);
  const timeframeText = value(spec.timeframe);
  const timeframe =
    timeframeText === "" ? "" : (appTimeframe(timeframeText) ?? "");
  const chartTimeframe = context?.timeframe ?? "";
  const sameTimeframe = timeframe === "" || timeframe === chartTimeframe;
  if (symbol === "" && sameTimeframe && !spec.lower) return "self";
  return { symbol, timeframe: timeframe || chartTimeframe };
}

/** The request.security data a script needs with these inputs, beyond the chart's bars. */
export function pineRequests(
  source: string,
  params: Record<string, number>,
  context: Pick<PineContext, "symbol" | "timeframe">,
): { key: string; symbol: string; timeframe: string }[] {
  let program: PineProgram;
  try {
    program = pineProgram(source);
  } catch {
    return [];
  }
  const out = new Map<
    string,
    { key: string; symbol: string; timeframe: string }
  >();
  for (const spec of program.requests) {
    const resolved = resolveRequest(
      spec,
      program,
      params,
      context as PineContext,
    );
    if (resolved === "self") continue;
    const key = pineRequestKey(resolved.symbol, resolved.timeframe);
    out.set(key, { key, ...resolved });
  }
  return [...out.values()];
}

const partsFormats = new Map<string, Intl.DateTimeFormat>();
function timeParts(ms: number, timeZone: string) {
  let format = partsFormats.get(timeZone);
  if (!format) {
    format = new Intl.DateTimeFormat("en-US", {
      timeZone,
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "numeric",
      minute: "numeric",
      second: "numeric",
      weekday: "short",
      hourCycle: "h23",
    });
    partsFormats.set(timeZone, format);
  }
  const parts = Object.fromEntries(
    format.formatToParts(new Date(ms)).map((part) => [part.type, part.value]),
  );
  return {
    year: Number(parts.year),
    month: Number(parts.month),
    day: Number(parts.day),
    hour: Number(parts.hour),
    minute: Number(parts.minute),
    second: Number(parts.second),
    weekday:
      ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].indexOf(parts.weekday) +
      1,
  };
}

/** Epoch ms of a wall-clock time in `timeZone` (`timestamp()`). */
function zonedTime(
  timeZone: string,
  year: number,
  month: number,
  day: number,
  hour: number,
  minute: number,
  second: number,
): number {
  const guess = Date.UTC(year, month - 1, day, hour, minute, second);
  const shown = timeParts(guess, timeZone);
  const asUtc = Date.UTC(
    shown.year,
    shown.month - 1,
    shown.day,
    shown.hour,
    shown.minute,
    shown.second,
  );
  return guess - (asUtc - guess);
}

/** Runs a compiled script over bars with the given input values. */
export function runPine(
  program: PineProgram,
  bars: OhlcvBar[],
  params: Record<string, number>,
  context?: PineContext,
  secondary?: Secondary,
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
  const graphics = emptyGraphics();
  const drawings = new DrawingStore(program.limits);
  const decimals = context?.priceDecimals ?? 2;
  const tick = 10 ** -decimals;
  const broker =
    program.strategy && !secondary
      ? new StrategyBroker(program.strategy, tick, decimals)
      : null;
  const fills = new Map<
    number,
    { a: Value; b: Value; colors: (string | null)[] }
  >();
  const missing: string[] = [];
  let steps = 0;
  let bar = 0;
  const timeZone = context?.timeZone ?? "America/New_York";
  const dayKey = (time: number) =>
    new Intl.DateTimeFormat("en-CA", { timeZone }).format(
      new Date(time * 1000),
    );
  const barSeconds = (() => {
    const gaps: number[] = [];
    for (let i = Math.max(1, bars.length - 20); i < bars.length; i += 1) {
      gaps.push(bars[i].time - bars[i - 1].time);
    }
    gaps.sort((a, b) => a - b);
    return gaps[Math.floor(gaps.length / 2)] ?? 60;
  })();
  const ends =
    context?.ends && context.ends.length === bars.length
      ? context.ends
      : bars.map((b, i) => bars[i + 1]?.time ?? b.time + barSeconds);

  const fail = (
    message: string,
    at: { line: number; column: number },
  ): never => {
    throw new PineError(message, at.line, at.column);
  };

  const field = (name: string, i: number): Value | undefined => {
    const b = bars[i];
    switch (name) {
      case "open":
        return b ? b.open : null;
      case "high":
        return b ? b.high : null;
      case "low":
        return b ? b.low : null;
      case "close":
        return b ? b.close : null;
      case "volume":
        return b ? b.volume : null;
      case "hl2":
        return b ? (b.high + b.low) / 2 : null;
      case "hlc3":
        return b ? (b.high + b.low + b.close) / 3 : null;
      case "ohlc4":
        return b ? (b.open + b.high + b.low + b.close) / 4 : null;
      case "hlcc4":
        return b ? (b.high + b.low + 2 * b.close) / 4 : null;
      case "bar_index":
        return b ? i : null;
      case "time":
        return b ? b.time * 1000 : null;
      case "time_close":
        return b ? ends[i] * 1000 : null;
      default:
        return undefined;
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

  const lengthOf = (value: Value | undefined, at: Expr): number => {
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

  /** Cumulative per-bar variables (`ta.obv`), computed once per bar. */
  const cumulative = new Map<
    string,
    { bar: number; value: number | null; total: number }
  >();
  const running = (
    name: string,
    step: (i: number) => number | null,
  ): number | null => {
    let entry = cumulative.get(name);
    if (!entry) {
      entry = { bar: -1, value: null, total: 0 };
      cumulative.set(name, entry);
    }
    while (entry.bar < bar) {
      entry.bar += 1;
      const change = step(entry.bar);
      entry.total += change ?? 0;
      entry.value = entry.total;
    }
    return entry.value;
  };

  /** The bar index of an epoch-ms time (last bar starting at or before it). */
  const barOfTime = (ms: number): number => {
    const seconds = ms / 1000;
    let low = 0;
    let high = bars.length - 1;
    if (bars.length === 0) return 0;
    if (seconds < bars[0].time) return (seconds - bars[0].time) / barSeconds;
    if (seconds > bars[high].time)
      return high + (seconds - bars[high].time) / barSeconds;
    while (low < high) {
      const middle = (low + high + 1) >> 1;
      if (bars[middle].time <= seconds) low = middle;
      else high = middle - 1;
    }
    return low;
  };
  /** Epoch seconds of a (possibly future or fractional) bar index. */
  const timeOfIndex = (index: number): number => {
    if (bars.length === 0) return 0;
    const whole = Math.round(index);
    if (whole >= 0 && whole < bars.length && whole === index)
      return bars[whole].time;
    if (index < 0) return bars[0].time + index * barSeconds;
    const last = bars.length - 1;
    if (index > last) return bars[last].time + (index - last) * barSeconds;
    const low = Math.floor(index);
    return (
      bars[low].time +
      (index - low) *
        ((bars[low + 1]?.time ?? bars[low].time + barSeconds) - bars[low].time)
    );
  };

  /** Variables such as `barstate.islast`, `syminfo.ticker` or `strategy.position_size`. */
  const builtinVariable = (name: string): Value | undefined => {
    const b = bars[bar];
    switch (name) {
      case "na":
        return null;
      case "barstate.isfirst":
        return bar === 0;
      case "barstate.islast":
      case "barstate.islastconfirmedhistory":
        return bar === bars.length - 1;
      case "barstate.ishistory":
      case "barstate.isconfirmed":
      case "barstate.isnew":
        return true;
      case "barstate.isrealtime":
        return false;
      case "last_bar_index":
        return bars.length - 1;
      case "last_bar_time":
        return bars.length ? bars[bars.length - 1].time * 1000 : null;
      case "timenow":
        return Date.now();
      case "syminfo.ticker":
      case "syminfo.tickerid":
      case "syminfo.root":
        return context?.symbol ?? "";
      case "syminfo.mintick":
        return tick;
      case "syminfo.pointvalue":
        return 1;
      case "syminfo.currency":
        return program.strategy?.currency ?? "USD";
      case "syminfo.timezone":
        return timeZone;
      case "syminfo.type":
        return "stock";
      case "timeframe.period":
        return pineTimeframe(context?.timeframe ?? "");
      case "timeframe.multiplier": {
        const match = /^(\d+)/.exec(context?.timeframe ?? "1");
        return match ? Number(match[1]) : 1;
      }
      case "timeframe.isintraday":
      case "timeframe.isminutes":
        return /m$/.test(context?.timeframe ?? "");
      case "timeframe.isdaily":
        return context?.timeframe === "1d";
      case "timeframe.isweekly":
        return context?.timeframe === "1w";
      case "timeframe.ismonthly":
        return context?.timeframe === "1M";
      case "timeframe.isdwm":
        return ["1d", "1w", "1M"].includes(context?.timeframe ?? "");
      case "timeframe.isseconds":
      case "timeframe.isticks":
        return false;
      case "ta.tr":
        return trueRangeAt(bar);
      case "ta.obv":
        return running("obv", (i) => {
          const previous = bars[i - 1];
          if (!previous) return 0;
          const change = bars[i].close - previous.close;
          return change > 0 ? bars[i].volume : change < 0 ? -bars[i].volume : 0;
        });
      case "ta.accdist":
        return running("accdist", (i) => {
          const x = bars[i];
          const range = x.high - x.low;
          return range === 0
            ? 0
            : ((x.close - x.low - (x.high - x.close)) / range) * x.volume;
        });
      case "ta.vwap":
        return vwapAt("hlc3");
      case "ta.pvt":
        return running("pvt", (i) => {
          const previous = bars[i - 1];
          return previous && previous.close !== 0
            ? ((bars[i].close - previous.close) / previous.close) *
                bars[i].volume
            : 0;
        });
      default:
        break;
    }
    if (!b) return undefined;
    if (
      [
        "year",
        "month",
        "weekofyear",
        "dayofmonth",
        "dayofweek",
        "hour",
        "minute",
        "second",
      ].includes(name)
    ) {
      return timeUnit(name, b.time * 1000, timeZone);
    }
    if (name.startsWith("strategy.")) {
      if (!broker) return secondary ? null : undefined;
      const vars = broker.variables();
      if (name in vars) return vars[name];
    }
    return undefined;
  };

  const timeUnit = (name: string, ms: number, zone: string): number => {
    const parts = timeParts(ms, zone);
    switch (name) {
      case "year":
        return parts.year;
      case "month":
        return parts.month;
      case "dayofmonth":
        return parts.day;
      case "dayofweek":
        return parts.weekday;
      case "hour":
        return parts.hour;
      case "minute":
        return parts.minute;
      case "second":
        return parts.second;
      default: {
        // ISO week number.
        const date = new Date(Date.UTC(parts.year, parts.month - 1, parts.day));
        const day = date.getUTCDay() || 7;
        date.setUTCDate(date.getUTCDate() + 4 - day);
        const start = Date.UTC(date.getUTCFullYear(), 0, 1);
        return Math.ceil(((date.getTime() - start) / 86_400_000 + 1) / 7);
      }
    }
  };

  const vwapState = new Map<
    string,
    { day: string; pv: number; v: number; bar: number; value: number | null }
  >();
  const vwapAt = (sourceName: string, price?: number | null): number | null => {
    let entry = vwapState.get(sourceName);
    if (!entry) {
      entry = { day: "", pv: 0, v: 0, bar: -1, value: null };
      vwapState.set(sourceName, entry);
    }
    if (entry.bar === bar) return entry.value;
    entry.bar = bar;
    const day = dayKey(bars[bar].time);
    if (entry.day !== day) Object.assign(entry, { day, pv: 0, v: 0 });
    const value = price ?? num(field(sourceName, bar) ?? null);
    if (value === null) return entry.value;
    entry.pv += value * bars[bar].volume;
    entry.v += bars[bar].volume;
    entry.value = entry.v === 0 ? null : entry.pv / entry.v;
    return entry.value;
  };

  const text = (value: Value | undefined): string =>
    typeof value === "string"
      ? value
      : value === null || value === undefined
        ? ""
        : toText(value);

  const colorArg = (
    value: Value | undefined,
    fallback: string,
  ): string | null =>
    typeof value === "string" ? value : value === null ? null : fallback;

  const directionOf = (value: Value | undefined, at: Expr): 1 | -1 => {
    if (value === "long" || value === true) return 1;
    if (value === "short" || value === false) return -1;
    return fail("direction must be strategy.long or strategy.short", at);
  };

  const strategyCall = (
    call: Extract<Expr, { kind: "call" }>,
    args: Value[],
  ): Value => {
    if (!broker) {
      if (secondary) return null;
      return fail(
        `${call.name}() needs strategy(…) instead of indicator(…)`,
        call,
      );
    }
    const b = bars[bar];
    const optional = (value: Value | undefined) => num(value ?? null);
    switch (call.name) {
      case "strategy.entry":
      case "strategy.order":
        broker.entry(
          call.name === "strategy.entry" ? "entry" : "order",
          text(args[0]),
          directionOf(args[1], call),
          optional(args[2]),
          optional(args[3]),
          optional(args[4]),
          text(args[7]),
          bar,
        );
        return null;
      case "strategy.exit":
        broker.exit(
          text(args[0]),
          typeof args[1] === "string" && args[1] !== "" ? args[1] : null,
          optional(args[2]),
          optional(args[3]),
          {
            profit: optional(args[4]),
            limit: optional(args[5]),
            loss: optional(args[6]),
            stop: optional(args[7]),
            trailPrice: optional(args[8]),
            trailPoints: optional(args[9]),
            trailOffset: optional(args[10]),
          },
          text(args[12]),
          bar,
        );
        return null;
      case "strategy.close":
        broker.close(
          text(args[0]),
          text(args[1]),
          optional(args[2]),
          optional(args[3]),
          args[5] === true,
          bar,
          b.close,
          b.time,
        );
        return null;
      case "strategy.close_all":
        broker.close(
          null,
          text(args[0]),
          null,
          null,
          args[2] === true,
          bar,
          b.close,
          b.time,
        );
        return null;
      case "strategy.cancel":
        broker.cancel(text(args[0]));
        return null;
      case "strategy.cancel_all":
        broker.cancel(null);
        return null;
      default:
        break;
    }
    const index = num(args[0] ?? null) ?? 0;
    if (call.name.startsWith("strategy.closedtrades.")) {
      const trade = broker.closedTrade(index);
      if (!trade) return null;
      switch (call.name.slice(22)) {
        case "profit":
          return Number(trade.profit);
        case "entry_price":
          return Number(trade.entryPrice);
        case "exit_price":
          return trade.exitPrice === null ? null : Number(trade.exitPrice);
        case "size":
          return Number(trade.qty) * (trade.direction === "long" ? 1 : -1);
        case "commission":
          return Number(trade.commission);
        case "entry_time":
          return trade.entryTime * 1000;
        case "exit_time":
          return trade.exitTime === null ? null : trade.exitTime * 1000;
        case "entry_bar_index":
          return Math.round(barOfTime(trade.entryTime * 1000));
        case "exit_bar_index":
          return trade.exitTime === null
            ? null
            : Math.round(barOfTime(trade.exitTime * 1000));
        case "entry_id":
          return trade.entryId;
        default:
          break;
      }
    }
    if (call.name.startsWith("strategy.opentrades.")) {
      const lot = broker.openLot(index);
      if (!lot) return null;
      switch (call.name.slice(20)) {
        case "entry_price":
          return lot.price;
        case "size":
          return lot.qty * lot.direction;
        case "profit":
          return (b.close - lot.price) * lot.qty * lot.direction;
        case "entry_time":
          return lot.time * 1000;
        case "entry_bar_index":
          return lot.bar;
        case "entry_id":
          return lot.entryId;
        default:
          break;
      }
    }
    return fail(`${call.name}() is not supported`, call);
  };

  const pointCall = (name: string, args: Value[]): Value => {
    switch (name) {
      case "chart.point.new":
        return new PinePoint(
          num(args[1] ?? null),
          num(args[0] ?? null),
          num(args[2] ?? null),
        );
      case "chart.point.from_index": {
        const index = num(args[0] ?? null);
        return new PinePoint(
          index,
          index === null ? null : timeOfIndex(index) * 1000,
          num(args[1] ?? null),
        );
      }
      case "chart.point.from_time": {
        const time = num(args[0] ?? null);
        return new PinePoint(
          time === null ? null : barOfTime(time),
          time,
          num(args[1] ?? null),
        );
      }
      case "chart.point.now":
        return new PinePoint(
          bar,
          bars[bar].time * 1000,
          num(args[0] ?? null) ?? bars[bar].close,
        );
      case "chart.point.copy": {
        const point = args[0];
        return point instanceof PinePoint
          ? new PinePoint(point.index, point.time, point.price)
          : null;
      }
      default:
        return undefined as never;
    }
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
    const name = call.name;
    try {
      if (name.startsWith("array.")) {
        const result = callArray(name, args);
        if (result !== undefined) return result;
      }
      if (name.startsWith("str.")) {
        const result = callString(name, args, decimals);
        if (result !== undefined) return result;
      }
      if (/^(line|label|box|table|linefill)\./.test(name)) {
        if (secondary) return null; // drawings come from the chart's own run only
        const result = drawings.call(name, args, bar, bars[bar].time * 1000);
        if (result !== undefined) return result;
      }
    } catch (error) {
      if (error instanceof PineValueError) return fail(error.message, call);
      throw error;
    }
    if (name.startsWith("chart.point.")) {
      const result = pointCall(name, args);
      if (result !== undefined) return result;
    }
    if (name.startsWith("strategy.")) return strategyCall(call, args);
    switch (name) {
      case "nz": {
        const value = args[0];
        return value === null ||
          value === undefined ||
          (typeof value === "number" && !Number.isFinite(value))
          ? (args[1] ?? 0)
          : value;
      }
      case "na":
        return isNa(args[0]);
      case "fixnan": {
        const s = st();
        const value = num(args[0]);
        if (value !== null) s.previous = value;
        return s.previous ?? null;
      }
      case "int":
        return a(0) === null ? null : Math.trunc(a(0)!);
      case "float":
        return a(0);
      case "bool":
        return truthy(args[0]);
      case "string":
        return text(args[0]);
      case "alert":
      case "alertcondition":
      case "log.info":
      case "log.warning":
      case "log.error":
      case "max_bars_back":
        return null; // alerts and logs: not run in the app
      case "runtime.error":
        return fail(text(args[0]) || "runtime.error", call);
      case "timestamp": {
        // timestamp([timezone,] year, month, day, hour, minute, second)
        const zoned = typeof args[0] === "string";
        const zone = zoned ? (args[0] as string) : timeZone;
        const n = (zoned ? args.slice(1) : args).map(
          (value) => num(value) ?? 0,
        );
        try {
          return zonedTime(
            zone === "UTC" ? "UTC" : zone,
            n[0],
            n[1] || 1,
            n[2] || 1,
            n[3],
            n[4],
            n[5],
          );
        } catch {
          return fail(`unknown time zone "${zone}"`, call);
        }
      }
      case "year":
      case "month":
      case "weekofyear":
      case "dayofmonth":
      case "dayofweek":
      case "hour":
      case "minute":
      case "second":
        return a(0) === null
          ? null
          : timeUnit(
              name,
              a(0)!,
              typeof args[1] === "string" ? args[1] : timeZone,
            );
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
      case "math.sin":
      case "math.cos":
      case "math.tan":
      case "math.asin":
      case "math.acos":
      case "math.atan": {
        if (a(0) === null) return null;
        const result = Math[name.slice(5) as "sin"](a(0)!);
        return Number.isFinite(result) ? result : null;
      }
      case "math.todegrees":
        return a(0) === null ? null : (a(0)! * 180) / Math.PI;
      case "math.toradians":
        return a(0) === null ? null : (a(0)! * Math.PI) / 180;
      case "math.round": {
        if (a(0) === null) return null;
        const digits = a(1) ?? 0;
        const factor = 10 ** digits;
        return Math.round(a(0)! * factor) / factor;
      }
      case "math.round_to_mintick":
        return a(0) === null ? null : Math.round(a(0)! / tick) * tick;
      case "math.max":
      case "math.min":
      case "math.avg": {
        const list = args.map(num);
        if (list.length === 0 || list.some((v) => v === null)) return null;
        const numbers = list as number[];
        if (name === "math.max") return Math.max(...numbers);
        if (name === "math.min") return Math.min(...numbers);
        return numbers.reduce((x, y) => x + y, 0) / numbers.length;
      }
      case "math.sum": {
        const window = windowOf(st(), 0, lengthOf(args[1], call));
        return window ? window.reduce((x, y) => x + y, 0) : null;
      }
      case "color.new":
        return typeof args[0] === "string" && a(1) !== null
          ? rgba(args[0], a(1)!)
          : typeof args[0] === "string"
            ? args[0]
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
      case "color.r":
      case "color.g":
      case "color.b":
      case "color.t": {
        if (typeof args[0] !== "string") return null;
        const [r, g, b, alpha] = rgbaParts(args[0]);
        const channels: Record<string, number> = {
          "color.r": r,
          "color.g": g,
          "color.b": b,
          "color.t": Math.round((1 - alpha) * 100),
        };
        return channels[name] ?? null;
      }
      case "color.from_gradient": {
        const value = a(0);
        const bottom = a(1);
        const top = a(2);
        if (value === null || bottom === null || top === null) return null;
        if (typeof args[3] !== "string" || typeof args[4] !== "string")
          return null;
        const t =
          top === bottom
            ? 1
            : Math.max(0, Math.min(1, (value - bottom) / (top - bottom)));
        const from = rgbaParts(args[3]);
        const to = rgbaParts(args[4]);
        const mix = (k: number) => Math.round(from[k] + (to[k] - from[k]) * t);
        const alpha = from[3] + (to[3] - from[3]) * t;
        return `rgba(${mix(0)}, ${mix(1)}, ${mix(2)}, ${Number(alpha.toFixed(3))})`;
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
      case "ta.swma": {
        const window = windowOf(st(), 0, 4);
        if (!window) return null;
        return (window[0] + 2 * window[1] + 2 * window[2] + window[3]) / 6;
      }
      case "ta.alma": {
        const length = lengthOf(args[1], call);
        const offset = a(2) ?? 0.85;
        const sigma = a(3) ?? 6;
        const window = windowOf(st(), 0, length);
        if (!window) return null;
        const m = offset * (length - 1);
        const s = length / sigma;
        let total = 0;
        let norm = 0;
        for (let k = 0; k < length; k += 1) {
          const weight = Math.exp(-((k - m) ** 2) / (2 * s * s));
          total += window[k] * weight;
          norm += weight;
        }
        return total / norm;
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
        const halfLength = Math.max(1, Math.floor(length / 2));
        const root = Math.max(1, Math.floor(Math.sqrt(length)));
        const diffs = (s.extra ??= { diff: [] as (number | null)[] }).diff as (
          number | null
        )[];
        const w1 = wmaOf(0, halfLength, bar);
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
      case "ta.stdev":
      case "ta.variance": {
        const window = windowOf(st(), 0, lengthOf(args[1], call));
        if (!window) return null;
        const mean = window.reduce((x, y) => x + y, 0) / window.length;
        const variance =
          window.reduce((sum, v) => sum + (v - mean) ** 2, 0) / window.length;
        return name === "ta.stdev" ? Math.sqrt(variance) : variance;
      }
      case "ta.dev": {
        const window = windowOf(st(), 0, lengthOf(args[1], call));
        if (!window) return null;
        const mean = window.reduce((x, y) => x + y, 0) / window.length;
        return (
          window.reduce((sum, v) => sum + Math.abs(v - mean), 0) / window.length
        );
      }
      case "ta.median": {
        const window = windowOf(st(), 0, lengthOf(args[1], call));
        if (!window) return null;
        const sorted = [...window].sort((x, y) => x - y);
        const middle = sorted.length >> 1;
        return sorted.length % 2
          ? sorted[middle]
          : (sorted[middle - 1] + sorted[middle]) / 2;
      }
      case "ta.highest":
      case "ta.lowest":
      case "ta.highestbars":
      case "ta.lowestbars":
      case "ta.range": {
        // ta.highest(length) reads high; ta.highest(source, length) the source.
        const single = args.length === 1 && name !== "ta.range";
        const length = lengthOf(single ? args[0] : args[1], call);
        const s = state(key);
        const high = name === "ta.highest" || name === "ta.highestbars";
        s.inputs[bar] = [
          single ? (high ? bars[bar].high : bars[bar].low) : args[0],
        ];
        const window = windowOf(s, 0, length);
        if (!window) return null;
        if (name === "ta.range")
          return Math.max(...window) - Math.min(...window);
        const best = high ? Math.max(...window) : Math.min(...window);
        if (name === "ta.highest" || name === "ta.lowest") return best;
        return window.lastIndexOf(best) - (window.length - 1);
      }
      case "ta.change":
      case "ta.mom": {
        const length = args.length > 1 ? lengthOf(args[1], call) : 1;
        const s = st();
        const now = args[0];
        const then = s.inputs[bar - length]?.[0];
        if (typeof now === "boolean") return now !== then && then !== undefined;
        const x = num(now);
        const y = num(then ?? null);
        return x === null || y === null ? null : x - y;
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
      case "ta.rising":
      case "ta.falling": {
        const length = lengthOf(args[1], call);
        const window = windowOf(st(), 0, length + 1);
        if (!window) return false;
        const now = window[window.length - 1];
        const before = window.slice(0, -1);
        return name === "ta.rising"
          ? before.every((v) => now > v)
          : before.every((v) => now < v);
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
      case "ta.kc": {
        const length = lengthOf(args[1], call);
        const mult = a(2) ?? 1.5;
        const useTrueRange = args[3] === undefined ? true : truthy(args[3]);
        const s = state(key);
        const extra = (s.extra ??= { mid: {}, range: {} }) as Record<
          string,
          CallState
        >;
        const alpha = 2 / (length + 1);
        const mid = smooth(extra.mid, a(0), length, alpha);
        const range = smooth(
          extra.range,
          useTrueRange ? trueRangeAt(bar) : bars[bar].high - bars[bar].low,
          length,
          alpha,
        );
        if (mid === null || range === null) return [null, null, null];
        return [mid, mid + mult * range, mid - mult * range];
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
      case "ta.wpr": {
        const length = lengthOf(args[0], call);
        const s = state(key);
        s.inputs[bar] = [bars[bar].high, bars[bar].low];
        const highs = windowOf(s, 0, length);
        const lows = windowOf(s, 1, length);
        if (!highs || !lows) return null;
        const hi = Math.max(...highs);
        const lo = Math.min(...lows);
        return hi === lo ? null : (-100 * (hi - bars[bar].close)) / (hi - lo);
      }
      case "ta.mfi": {
        const length = lengthOf(args[1], call);
        const s = st();
        const flows = (s.extra ??= {
          up: [] as number[],
          down: [] as number[],
        }) as {
          up: number[];
          down: number[];
        };
        const now = a(0);
        const before = num(s.inputs[bar - 1]?.[0]);
        const flow = now === null ? 0 : now * bars[bar].volume;
        flows.up[bar] =
          now !== null && before !== null && now > before ? flow : 0;
        flows.down[bar] =
          now !== null && before !== null && now < before ? flow : 0;
        if (bar < length) return null;
        let up = 0;
        let down = 0;
        for (let j = bar - length + 1; j <= bar; j += 1) {
          up += flows.up[j];
          down += flows.down[j];
        }
        return down === 0 ? 100 : 100 - 100 / (1 + up / down);
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
      case "ta.linreg": {
        const length = lengthOf(args[1], call);
        const offset = a(2) ?? 0;
        const window = windowOf(st(), 0, length);
        if (!window) return null;
        const n = length;
        let sumX = 0;
        let sumY = 0;
        let sumXY = 0;
        let sumXX = 0;
        window.forEach((y, x) => {
          sumX += x;
          sumY += y;
          sumXY += x * y;
          sumXX += x * x;
        });
        const slope =
          n * sumXX - sumX * sumX === 0
            ? 0
            : (n * sumXY - sumX * sumY) / (n * sumXX - sumX * sumX);
        const intercept = (sumY - slope * sumX) / n;
        return intercept + slope * (n - 1 - offset);
      }
      case "ta.correlation": {
        const length = lengthOf(args[2], call);
        const s = st();
        const xs = windowOf(s, 0, length);
        const ys = windowOf(s, 1, length);
        if (!xs || !ys) return null;
        const mx = xs.reduce((p, q) => p + q, 0) / length;
        const my = ys.reduce((p, q) => p + q, 0) / length;
        let cov = 0;
        let vx = 0;
        let vy = 0;
        for (let k = 0; k < length; k += 1) {
          cov += (xs[k] - mx) * (ys[k] - my);
          vx += (xs[k] - mx) ** 2;
          vy += (ys[k] - my) ** 2;
        }
        return vx === 0 || vy === 0 ? null : cov / Math.sqrt(vx * vy);
      }
      case "ta.percentrank": {
        const length = lengthOf(args[1], call);
        const window = windowOf(st(), 0, length + 1);
        if (!window) return null;
        const now = window[window.length - 1];
        const below = window.slice(0, -1).filter((v) => v <= now).length;
        return (100 * below) / length;
      }
      case "ta.supertrend": {
        const factor = a(0) ?? 3;
        const length = lengthOf(args[1], call);
        const s = state(key);
        const extra = (s.extra ??= {
          atr: {} as CallState,
          upper: null as number | null,
          lower: null as number | null,
          direction: 1,
          trend: null as number | null,
        }) as {
          atr: CallState;
          upper: number | null;
          lower: number | null;
          direction: number;
          trend: number | null;
        };
        const atr = smooth(extra.atr, trueRangeAt(bar), length, 1 / length);
        if (atr === null) return [null, null];
        const middle = (bars[bar].high + bars[bar].low) / 2;
        const previousClose = bars[bar - 1]?.close ?? bars[bar].close;
        let upper = middle + factor * atr;
        let lower = middle - factor * atr;
        if (
          extra.lower !== null &&
          !(lower > extra.lower || previousClose < extra.lower)
        )
          lower = extra.lower;
        if (
          extra.upper !== null &&
          !(upper < extra.upper || previousClose > extra.upper)
        )
          upper = extra.upper;
        let direction: number;
        if (extra.trend === null) direction = 1;
        else if (extra.trend === extra.upper)
          direction = bars[bar].close > upper ? -1 : 1;
        else direction = bars[bar].close < lower ? 1 : -1;
        const trend = direction === -1 ? lower : upper;
        Object.assign(extra, { upper, lower, direction, trend });
        return [trend, direction];
      }
      case "ta.dmi": {
        const diLength = lengthOf(args[0], call);
        const adxLength = lengthOf(args[1], call);
        const s = state(key);
        const extra = (s.extra ??= {
          tr: {},
          plus: {},
          minus: {},
          adx: {},
        }) as Record<string, CallState>;
        const previous = bars[bar - 1];
        const up = previous ? bars[bar].high - previous.high : 0;
        const down = previous ? previous.low - bars[bar].low : 0;
        const plusDM = previous && up > down && up > 0 ? up : 0;
        const minusDM = previous && down > up && down > 0 ? down : 0;
        const tr = smooth(extra.tr, trueRangeAt(bar), diLength, 1 / diLength);
        const plus = smooth(extra.plus, plusDM, diLength, 1 / diLength);
        const minus = smooth(extra.minus, minusDM, diLength, 1 / diLength);
        if (tr === null || plus === null || minus === null || tr === 0)
          return [null, null, null];
        const plusDI = (100 * plus) / tr;
        const minusDI = (100 * minus) / tr;
        const sum = plusDI + minusDI;
        const adx = smooth(
          extra.adx,
          sum === 0 ? 0 : (100 * Math.abs(plusDI - minusDI)) / sum,
          adxLength,
          1 / adxLength,
        );
        return [plusDI, minusDI, adx];
      }
      case "ta.sar": {
        const start = a(0) ?? 0.02;
        const increment = a(1) ?? 0.02;
        const maximum = a(2) ?? 0.2;
        const s = state(key);
        const extra = (s.extra ??= {
          sar: null,
          ep: 0,
          af: start,
          long: true,
        }) as {
          sar: number | null;
          ep: number;
          af: number;
          long: boolean;
        };
        const b = bars[bar];
        const previous = bars[bar - 1];
        if (!previous) return null;
        if (extra.sar === null) {
          extra.long = b.close >= previous.close;
          extra.sar = extra.long
            ? Math.min(previous.low, b.low)
            : Math.max(previous.high, b.high);
          extra.ep = extra.long ? b.high : b.low;
          extra.af = start;
          return extra.sar;
        }
        let sar = extra.sar + extra.af * (extra.ep - extra.sar);
        if (extra.long) {
          sar = Math.min(sar, previous.low, bars[bar - 2]?.low ?? previous.low);
          if (b.low < sar) {
            extra.long = false;
            sar = extra.ep;
            extra.ep = b.low;
            extra.af = start;
          } else if (b.high > extra.ep) {
            extra.ep = b.high;
            extra.af = Math.min(maximum, extra.af + increment);
          }
        } else {
          sar = Math.max(
            sar,
            previous.high,
            bars[bar - 2]?.high ?? previous.high,
          );
          if (b.high > sar) {
            extra.long = true;
            sar = extra.ep;
            extra.ep = b.high;
            extra.af = start;
          } else if (b.low < extra.ep) {
            extra.ep = b.low;
            extra.af = Math.min(maximum, extra.af + increment);
          }
        }
        extra.sar = sar;
        return sar;
      }
      case "ta.pivothigh":
      case "ta.pivotlow": {
        // ta.pivothigh(left, right) reads high/low; with a source first, the source.
        const withSource = args.length >= 3;
        const left = lengthOf(withSource ? args[1] : args[0], call);
        const right = lengthOf(withSource ? args[2] : args[1], call);
        const s = state(key);
        const high = name === "ta.pivothigh";
        s.inputs[bar] = [
          withSource ? args[0] : high ? bars[bar].high : bars[bar].low,
        ];
        // Known only `right` bars later: the value is reported on the bar that confirms it.
        const window = windowOf(s, 0, left + right + 1);
        if (!window) return null;
        const candidate = window[left];
        const others = window.filter((_, k) => k !== left);
        return others.every((v) => (high ? candidate > v : candidate < v))
          ? candidate
          : null;
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
        return name === "ta.crossover"
          ? over
          : name === "ta.crossunder"
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
        return fail(`${name}() is not supported`, call);
    }
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
        const value = field(expr.name, bar);
        if (value !== undefined) return value;
        const builtin = builtinVariable(expr.name);
        if (builtin !== undefined) return builtin;
        const named = namedConstant(expr.name);
        if (named !== undefined) return named;
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
              : left instanceof PineRef || right instanceof PineRef
                ? left instanceof PineRef &&
                  right instanceof PineRef &&
                  left.kind === right.kind &&
                  left.id === right.id
                : num(left) !== null && num(left) === num(right);
          return expr.op === "=="
            ? same
            : !same && left !== null && right !== null;
        }
        if (
          expr.op === "+" &&
          (typeof left === "string" || typeof right === "string")
        ) {
          // String concatenation; na is "NaN" on TradingView.
          return (
            text(left === null ? "NaN" : left) +
            text(right === null ? "NaN" : right)
          );
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

  /** Arguments in the built-in's documented order, named ones placed by name. */
  const builtinArgs = (
    expr: Extract<Expr, { kind: "call" }>,
    scopes: Scope[],
    path: string,
    depth: number,
    prefix: Value[] = [],
  ): Value[] => {
    const args: Value[] = [
      ...prefix,
      ...expr.args.map((arg) => evaluate(arg, scopes, path, depth)),
    ];
    const signature = SIGNATURES[expr.name];
    for (const [name, arg] of Object.entries(expr.named)) {
      const index = signature
        ? signature.indexOf(name)
        : (TA_ORDER[name] ?? args.length);
      if (index < 0) continue; // e.g. editable=, tooltip=: nothing to do in the app
      args[index] = evaluate(arg, scopes, path, depth);
    }
    return args;
  };

  /** Values of request.security calls, aligned to these bars before the run. */
  const requested = new Map<number, Value[] | "self">();

  const shapeAt = (
    i: number,
    location: Value | undefined,
    price: number | null,
  ): {
    price: number | null;
    location: "above" | "below" | "absolute" | "top" | "bottom";
  } => {
    const name = typeof location === "string" ? location : "location.abovebar";
    if (name === "location.absolute") return { price, location: "absolute" };
    if (name === "location.top") return { price: null, location: "top" };
    if (name === "location.bottom") return { price: null, location: "bottom" };
    const below = name === "location.belowbar";
    if (!program.overlay)
      return { price: null, location: below ? "bottom" : "top" };
    return {
      price: below ? bars[i].low : bars[i].high,
      location: below ? "below" : "above",
    };
  };

  /** plotshape/plotchar/plotarrow/bgcolor/barcolor/fill: what they draw this bar. */
  const graphicCall = (
    expr: Extract<Expr, { kind: "call" }>,
    args: Value[],
  ): Value => {
    if (secondary) return null;
    const offsetBar = (index: number) => {
      const offset = Math.round(num(args[index] ?? null) ?? 0);
      return Math.max(0, Math.min(bars.length - 1, bar + offset));
    };
    switch (expr.name) {
      case "plotshape":
      case "plotchar": {
        const series = args[0];
        const char = expr.name === "plotchar";
        const location =
          args[3] ?? (char ? "location.abovebar" : "location.abovebar");
        const absolute = location === "location.absolute";
        if (absolute ? num(series ?? null) === null : !truthy(series))
          return null;
        const i = offsetBar(5);
        const color = colorArg(args[4], DEFAULT_PLOT_COLOR);
        if (color === null) return null;
        const place = shapeAt(i, location, num(series ?? null));
        graphics.shapes.push({
          time: bars[i].time,
          ...place,
          shape: char
            ? `char:${text(args[2]) || "★"}`
            : text(args[2]) || "shape.xcross",
          text: text(args[6]),
          color,
          textColor: colorArg(args[7], color) ?? color,
          size: sizeOf(args[9], 10),
        });
        return null;
      }
      case "plotarrow": {
        const value = num(args[0] ?? null);
        if (value === null || value === 0) return null;
        const i = offsetBar(4);
        const up = value > 0;
        const color = colorArg(
          up ? args[2] : args[3],
          up ? "#089981" : "#f23645",
        );
        if (color === null) return null;
        graphics.shapes.push({
          time: bars[i].time,
          ...shapeAt(i, up ? "location.belowbar" : "location.abovebar", null),
          shape: up ? "shape.arrowup" : "shape.arrowdown",
          text: "",
          color,
          textColor: color,
          size: 12,
        });
        return null;
      }
      case "bgcolor":
      case "barcolor": {
        const color = colorArg(args[0], DEFAULT_PLOT_COLOR);
        if (color === null || isTransparent(color)) return null;
        const i = offsetBar(1);
        (expr.name === "bgcolor"
          ? graphics.backgrounds
          : graphics.barColors
        ).push({
          time: bars[i].time,
          color,
        });
        return null;
      }
      case "fill": {
        let entry = fills.get(expr.id);
        if (!entry) {
          entry = { a: args[0], b: args[1], colors: [] };
          fills.set(expr.id, entry);
        }
        // fill(p1, p2, top_value, bottom_value, top_color, bottom_color): a gradient; the
        // top colour is used.
        const gradient =
          typeof args[2] === "number" ||
          (args[2] === null && typeof args[4] === "string");
        entry.colors[bar] = colorArg(
          gradient ? args[4] : args[2],
          "rgba(41, 98, 255, 0.2)",
        );
        return null;
      }
      default:
        return null;
    }
  };

  const callUser = (
    expr: Extract<Expr, { kind: "call" }>,
    user: FunctionDef,
    scopes: Scope[],
    path: string,
    depth: number,
    receiver?: Value,
  ): Value => {
    if (depth >= MAX_CALL_DEPTH)
      return fail("functions call each other too deeply", expr);
    const given: (Value | undefined)[] = [];
    if (receiver !== undefined) given.push(receiver);
    expr.args.forEach((arg) => given.push(evaluate(arg, scopes, path, depth)));
    for (const [name, arg] of Object.entries(expr.named)) {
      const index = user.params.indexOf(name);
      if (index < 0)
        return fail(`${expr.name}() has no parameter "${name}"`, expr);
      given[index] = evaluate(arg, scopes, path, depth);
    }
    if (given.length > user.params.length) {
      return fail(`${expr.name}() takes ${user.params.length} arguments`, expr);
    }
    const local: Scope = new Map();
    const callPath = `${path}/${expr.id}`;
    user.params.forEach((param, k) => {
      const slot = slotFor(`${callPath}/param:${param}`, false);
      let value = given[k];
      if (value === undefined) {
        const fallback = user.defaults[k];
        if (!fallback) {
          return fail(`${expr.name}() needs a value for "${param}"`, expr);
        }
        value = evaluate(fallback, [scopes[0]], path, depth);
      }
      slot.values[bar] = value;
      local.set(param, slot);
    });
    // A function sees globals and its own parameters, not the caller's locals.
    return runBlock(user.body, [scopes[0], local], callPath, depth + 1);
  };

  const callExpr = (
    expr: Extract<Expr, { kind: "call" }>,
    scopes: Scope[],
    path: string,
    depth: number,
  ): Value => {
    const name = expr.name;
    if (name === "indicator" || name === "study" || name === "strategy") {
      return null; // read at compile time
    }
    if (name === "hline") {
      const index = program.hlines.get(expr.id);
      return index === undefined ? null : new PineRef("hline", index);
    }
    if (name === "input" || name.startsWith("input.")) {
      const key = program.inputKeys.get(expr.id);
      if (key === undefined)
        return evaluate(argument(expr, 0, "defval")!, scopes, path, depth);
      const declared = program.inputs.find((item) => item.key === key);
      const value = params[key] ?? declared?.default ?? null;
      if (declared?.source) {
        return (
          field(SOURCES[Math.round(num(value) ?? 0)] ?? "close", bar) ?? null
        );
      }
      if (declared?.choices) {
        return (
          declared.choices[Math.round(num(value) ?? 0)] ?? declared.choices[0]
        );
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
    if (name === "plot") {
      const key = program.plotKeys.get(expr.id);
      if (!key) return null;
      const series = argument(expr, 0, "series");
      const value = series ? num(evaluate(series, scopes, path, depth)) : null;
      values[key][bar] = value;
      const colorExpr = argument(expr, 2, "color");
      if (colorExpr && constant(colorExpr) === undefined) {
        const color = evaluate(colorExpr, scopes, path, depth);
        // color=na hides the line on this bar.
        colors[key][bar] = typeof color === "string" ? color : TRANSPARENT;
      }
      return new PineRef("plot", Number(key.slice(4)));
    }
    if (name === "request.security" || name === "request.security_lower_tf") {
      const expression = argument(expr, 2, "expression");
      if (!expression)
        return fail("request.security needs an expression", expr);
      if (secondary) {
        const value = evaluate(expression, scopes, path, depth);
        if (secondary.target === expr.id) secondary.capture[bar] = value;
        return name === "request.security_lower_tf"
          ? new PineArray([value])
          : value;
      }
      const aligned = requested.get(expr.id);
      if (aligned === "self") {
        const value = evaluate(expression, scopes, path, depth);
        return name === "request.security_lower_tf"
          ? new PineArray([value])
          : value;
      }
      return (
        aligned?.[bar] ??
        (name === "request.security_lower_tf" ? new PineArray([]) : null)
      );
    }
    if (
      [
        "plotshape",
        "plotchar",
        "plotarrow",
        "bgcolor",
        "barcolor",
        "fill",
      ].includes(name)
    ) {
      return graphicCall(expr, builtinArgs(expr, scopes, path, depth));
    }
    const user = functions.get(name);
    if (user) return callUser(expr, user, scopes, path, depth);
    // A method call on a variable: `xs.push(1)` is `array.push(xs, 1)`.
    const dot = name.indexOf(".");
    if (dot > 0) {
      const head = name.slice(0, dot);
      const slot = lookup(scopes, head);
      if (slot) {
        const receiver = slot.values[bar] ?? null;
        const method = name.slice(dot + 1);
        const own = functions.get(method);
        if (own?.method)
          return callUser(expr, own, scopes, path, depth, receiver);
        const namespace = methodNamespace(receiver);
        if (!namespace) {
          return fail(
            receiver === null
              ? `"${head}" is na`
              : `"${head}" has no method ${method}()`,
            expr,
          );
        }
        const target = { ...expr, name: `${namespace}.${method}` };
        return callBuiltin(
          target,
          builtinArgs(target, scopes, path, depth, [receiver]),
          path,
        );
      }
    }
    return callBuiltin(expr, builtinArgs(expr, scopes, path, depth), path);
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

  const loopBody = (
    body: Stmt[],
    scopes: Scope[],
    path: string,
    depth: number,
  ): { value: Value; stop: boolean } => {
    steps += 1;
    if (steps > MAX_LOOP_STEPS) throw new Stop();
    try {
      return { value: runBlock(body, scopes, path, depth), stop: false };
    } catch (signal) {
      if (signal instanceof BreakSignal) return { value: null, stop: true };
      if (signal instanceof ContinueSignal) return { value: null, stop: false };
      throw signal;
    }
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
        functions.set(stmt.name, {
          params: stmt.params,
          defaults: stmt.defaults,
          method: stmt.method,
          body: stmt.body,
        });
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
        } else if (
          stmt.op === "+=" &&
          (typeof slot.values[bar] === "string" || typeof value === "string")
        ) {
          slot.values[bar] = text(slot.values[bar]) + text(value);
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
      case "switch": {
        const subject = stmt.subject
          ? evaluate(stmt.subject, scopes, path, depth)
          : undefined;
        for (const item of stmt.cases) {
          if (item.test === null)
            return runBlock(item.body, scopes, path, depth);
          const test = evaluate(item.test, scopes, path, depth);
          const matches =
            subject === undefined
              ? truthy(test)
              : typeof subject === "string" || typeof test === "string"
                ? subject === test
                : num(subject) !== null && num(subject) === num(test);
          if (matches) return runBlock(item.body, scopes, path, depth);
        }
        return null;
      }
      case "for": {
        const from = num(evaluate(stmt.from, scopes, path, depth));
        const to = num(evaluate(stmt.to, scopes, path, depth));
        const stepValue = stmt.step
          ? num(evaluate(stmt.step, scopes, path, depth))
          : null;
        if (from === null || to === null) return null;
        const step =
          stepValue === null
            ? to >= from
              ? 1
              : -1
            : to >= from
              ? Math.abs(stepValue)
              : -Math.abs(stepValue);
        if (step === 0) return fail("a for loop's step cannot be 0", stmt);
        const counter = slotFor(`${path}/${stmt.id}:${stmt.name}`, false);
        const loopScope: Scope = new Map([[stmt.name, counter]]);
        let last: Value = null;
        for (let i = from; step > 0 ? i <= to : i >= to; i += step) {
          counter.values[bar] = i;
          const result = loopBody(
            stmt.body,
            [...scopes, loopScope],
            path,
            depth,
          );
          if (result.stop) break;
          last = result.value;
        }
        return last;
      }
      case "forin": {
        const source = evaluate(stmt.source, scopes, path, depth);
        const items =
          source instanceof PineArray
            ? [...source.items]
            : Array.isArray(source)
              ? source
              : source === null
                ? []
                : fail("for … in needs an array", stmt.source);
        const named = stmt.names.map((name) =>
          slotFor(`${path}/${stmt.id}:${name}`, false),
        );
        const loopScope: Scope = new Map(
          stmt.names.map((name, k) => [name, named[k]]),
        );
        let last: Value = null;
        for (let k = 0; k < items.length; k += 1) {
          if (named.length === 2) {
            named[0].values[bar] = k;
            named[1].values[bar] = items[k];
          } else {
            named[0].values[bar] = items[k];
          }
          const result = loopBody(
            stmt.body,
            [...scopes, loopScope],
            path,
            depth,
          );
          if (result.stop) break;
          last = result.value;
        }
        return last;
      }
      case "while": {
        let last: Value = null;
        while (truthy(evaluate(stmt.test, scopes, path, depth))) {
          const result = loopBody(stmt.body, scopes, path, depth);
          if (result.stop) break;
          last = result.value;
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

  // request.security: run the script on the other bars, then align the values to
  // these bars by when each other bar was final (no value before then).
  if (!secondary) {
    for (const spec of program.requests) {
      const resolved = resolveRequest(spec, program, params, context);
      if (resolved === "self") {
        requested.set(spec.id, "self");
        continue;
      }
      const key = pineRequestKey(resolved.symbol, resolved.timeframe);
      const data = context?.requests?.[key];
      if (!data) {
        if (!missing.includes(key)) missing.push(key);
        continue;
      }
      const capture: Value[] = [];
      runPine(
        program,
        data.bars,
        params,
        {
          ...context,
          timeframe: resolved.timeframe,
          symbol: resolved.symbol || context?.symbol,
          ends: data.availableAt,
          requests: undefined,
        },
        { target: spec.id, capture },
      );
      requested.set(spec.id, alignRequest(spec, data, capture, bars, ends));
    }
  }

  try {
    for (bar = 0; bar < bars.length; bar += 1) {
      // `var` values carry over to this bar before the script runs.
      for (const slot of slots.values()) {
        if (slot.persistent && bar > 0 && slot.values[bar] === undefined) {
          slot.values[bar] = slot.values[bar - 1] ?? null;
        }
      }
      broker?.beforeBar(bar, bars[bar]);
      runBlock(program.body, [], "", 0);
      broker?.afterBar(bar, bars[bar]);
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

  if (!secondary) {
    const snapshot = drawings.snapshot(
      (x, xloc) => (xloc === "bar_time" ? x / 1000 : timeOfIndex(x)),
      (time) => {
        const i = Math.round(barOfTime(time * 1000));
        return bars[i] ? { high: bars[i].high, low: bars[i].low } : null;
      },
    );
    Object.assign(graphics, snapshot);
    graphics.fills = [...fills.values()].flatMap((entry) => fillOf(entry));
    if (broker) graphics.trades = broker.marks;
  }
  return {
    values,
    colors,
    graphics,
    strategy: broker ? broker.report() : null,
    missing,
  };

  function fillOf(entry: {
    a: Value;
    b: Value;
    colors: (string | null)[];
  }): PineFill[] {
    const edge = (
      ref: Value,
    ): ((i: number) => number | null) | number | null => {
      if (!(ref instanceof PineRef)) return null;
      if (ref.kind === "hline") return program.hlinePrices[ref.id] ?? null;
      if (ref.kind === "plot") {
        const series = values[`plot${ref.id}`];
        return series ? (i: number) => series[i] : null;
      }
      return null;
    };
    const a = edge(entry.a);
    const b = edge(entry.b);
    if (a === null || b === null) return [];
    if (typeof a === "number" && typeof b === "number") {
      const color = [...entry.colors].reverse().find((c) => c) ?? null;
      return color ? [{ kind: "band", y1: a, y2: b, color }] : [];
    }
    const at = (side: typeof a, i: number) =>
      typeof side === "number" ? side : side(i);
    const points = bars.flatMap((bar_, i) => {
      const y1 = at(a, i);
      const y2 = at(b, i);
      const color = entry.colors[i];
      return y1 === null || y2 === null || !color
        ? []
        : [{ time: bar_.time, y1, y2, color }];
    });
    return points.length ? [{ kind: "series", points }] : [];
  }
}

/** A request's values per chart bar: the last other bar final by each chart bar's end. */
function alignRequest(
  spec: PineRequestSpec,
  data: PineRequestData,
  capture: Value[],
  bars: OhlcvBar[],
  ends: number[],
): Value[] {
  const out: Value[] = [];
  if (spec.lower) {
    // Smaller bars inside each chart bar, final by its end, as an array.
    let k = 0;
    for (let j = 0; j < bars.length; j += 1) {
      const items: Value[] = [];
      while (k < data.bars.length && data.bars[k].time < bars[j].time) k += 1;
      let m = k;
      while (m < data.bars.length && data.availableAt[m] <= ends[j]) {
        items.push(capture[m] ?? null);
        m += 1;
      }
      k = m;
      out.push(new PineArray(items));
    }
    return out;
  }
  let i = -1;
  let previous = -1;
  for (let j = 0; j < bars.length; j += 1) {
    if (spec.lookahead) {
      // The other bar that contains this one; the expression reads its earlier bars.
      while (i + 1 < data.bars.length && data.bars[i + 1].time <= bars[j].time)
        i += 1;
    } else {
      while (
        i + 1 < data.availableAt.length &&
        data.availableAt[i + 1] <= ends[j]
      )
        i += 1;
    }
    if (i < 0 || (spec.gaps && i === previous)) {
      out.push(null);
    } else {
      out.push(capture[i] ?? null);
    }
    previous = i;
  }
  return out;
}

/** Daily bars grouped into calendar months (exchange time), for `request.security(…, "M")`. */
export function monthlyBars(
  daily: PineRequestData,
  timeZone: string,
): PineRequestData {
  const bars: OhlcvBar[] = [];
  const availableAt: number[] = [];
  const monthOf = (time: number) => {
    const parts = timeParts(time * 1000, timeZone);
    return parts.year * 12 + parts.month;
  };
  let current = -1;
  daily.bars.forEach((bar, k) => {
    const month = monthOf(bar.time);
    if (month !== current) {
      current = month;
      bars.push({ ...bar });
      availableAt.push(daily.availableAt[k]);
      return;
    }
    const last = bars[bars.length - 1];
    last.high = Math.max(last.high, bar.high);
    last.low = Math.min(last.low, bar.low);
    last.close = bar.close;
    last.volume += bar.volume;
    availableAt[availableAt.length - 1] = daily.availableAt[k];
  });
  return { bars, availableAt };
}

// --- as an indicator ----------------------------------------------------------------

/** Per-bar colours of the latest run, by its values object (dynamic plot colours). */
export const PINE_COLORS = new WeakMap<
  Record<string, Series>,
  Record<string, (string | null)[]>
>();

/** Drawings, strategy results and missing request data of the latest run, by values. */
export const PINE_EXTRAS = new WeakMap<
  Record<string, Series>,
  {
    graphics: PineGraphics;
    strategy: StrategyReport | null;
    missing: string[];
    error?: string;
  }
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
    ...(plot.hidden ? { hidden: true } : {}),
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
      delete copy.choices;
      return copy;
    }),
    outputs,
    ...(compiled.levels.length > 0 ? { levels: compiled.levels } : {}),
    compute: (bars, params, context) => {
      try {
        const result = runPine(compiled, bars, params, context as PineContext);
        PINE_COLORS.set(result.values, result.colors);
        PINE_EXTRAS.set(result.values, {
          graphics: result.graphics,
          strategy: result.strategy,
          missing: result.missing,
        });
        return result.values;
      } catch (error) {
        const values = Object.fromEntries(
          outputs.map((o) => [o.key, bars.map(() => null)]),
        );
        PINE_EXTRAS.set(values, {
          graphics: emptyGraphics(),
          strategy: null,
          missing: [],
          error:
            error instanceof PineError
              ? `${error.message} (line ${error.line})`
              : String(error),
        });
        return values;
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

/** A strategy example: an EMA cross with a stop and target, as a chart backtest. */
export const PINE_STRATEGY_EXAMPLE = `//@version=5
strategy("EMA Cross Strategy", overlay=true, initial_capital=10000, default_qty_type=strategy.percent_of_equity, default_qty_value=50)
fast = ta.ema(close, input.int(9, "Fast length"))
slow = ta.ema(close, input.int(21, "Slow length"))
plot(fast, "Fast", color=color.orange)
plot(slow, "Slow", color=color.blue)
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long)
if ta.crossunder(fast, slow)
    strategy.close("Long")
strategy.exit("Exit", "Long", stop=strategy.position_avg_price * 0.98, limit=strategy.position_avg_price * 1.04)
`;
