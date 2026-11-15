/**
 * Pine Script indicators turned into formula indicators when they are added (ADR 0058).
 *
 * Pine is the way TradingView scripts come in; the chart runs formulas, which are an
 * order of magnitude faster on long charts. The conversion is code, not a model: the
 * script's tree is written out as formula statements, and only the parts with a formula
 * equivalent are accepted (§3). Before a conversion is used, both are run on the same
 * bars and every plotted value and colour must agree (§4); "agreed on these bars" is all
 * that check shows. What is not converted stays a Pine indicator, with the reason.
 */
import {
  evaluateFormula,
  formulaDefinition,
  FORMULA_FIELDS,
  MAX_FORMULA_LENGTH,
  parseFormula,
  FormulaError,
  formulaPosition,
} from "./formula";
import {
  cleanParams,
  type IndicatorContext,
  type IndicatorInstance,
  type OhlcvBar,
  type Series,
} from "./indicators";
import {
  COLORS,
  compilePine,
  DEFAULT_PLOT_COLOR,
  PineError,
  runPine,
  SOURCES,
  type PineContext,
  type PineProgram,
} from "./pine";
import type { Expr, Stmt } from "./pine-parser";
import { sha256 } from "./sha256";

/** Saved with each converted indicator; a new version may convert more. */
export const CONVERTER_VERSION = "pine-formula/1";

export type PineConversion =
  | {
      ok: true;
      formula: string;
      label: string;
      pane: "price" | "separate";
      /** The Pine input key → the formula setting it became. */
      keys: Record<string, string>;
    }
  | { ok: false; reason: string; line: number; column: number };

/** What a converted indicator keeps of where it came from (ADR 0058 §5). */
export interface PineOrigin {
  pine: string;
  sha256: string;
  converter: string;
  /** Bars on which the formula and the script agreed. */
  bars: number;
  /** The formula was changed after the conversion. */
  edited?: boolean;
}

class Refuse extends Error {
  constructor(
    message: string,
    readonly line: number,
    readonly column: number,
  ) {
    super(message);
  }
}

const refuse = (
  message: string,
  at: { line: number; column: number },
): never => {
  throw new Refuse(message, at.line, at.column);
};

const RESERVED = new Set([
  "and",
  "or",
  "not",
  "true",
  "false",
  "na",
  "input",
  "plot",
  "hline",
]);

/** Pine function → formula function, with Pine's argument names in order. */
const FUNCTIONS: Record<string, { to: string; args: string[] }> = {
  "ta.sma": { to: "sma", args: ["source", "length"] },
  "ta.ema": { to: "ema", args: ["source", "length"] },
  "ta.wma": { to: "wma", args: ["source", "length"] },
  "ta.rma": { to: "rma", args: ["source", "length"] },
  "ta.stdev": { to: "stdev", args: ["source", "length", "biased"] },
  "ta.highest": { to: "highest", args: ["source", "length"] },
  "ta.lowest": { to: "lowest", args: ["source", "length"] },
  "math.sum": { to: "sum", args: ["source", "length"] },
  "ta.change": { to: "change", args: ["source", "length"] },
  "ta.roc": { to: "roc", args: ["source", "length"] },
  "ta.rsi": { to: "rsi", args: ["source", "length"] },
  "ta.atr": { to: "atr", args: ["length"] },
  "ta.tr": { to: "tr", args: ["handle_na"] },
  "ta.cum": { to: "cum", args: ["source"] },
  "ta.vwap": { to: "vwap", args: ["source"] },
  "ta.stoch": { to: "stoch", args: ["source", "high", "low", "length"] },
  "ta.cci": { to: "cci", args: ["source", "length"] },
  "ta.mfi": { to: "mfi", args: ["series", "length"] },
  "ta.crossover": { to: "crossover", args: ["source1", "source2"] },
  "ta.crossunder": { to: "crossunder", args: ["source1", "source2"] },
  nz: { to: "nz", args: ["source", "replacement"] },
  na: { to: "na", args: ["x"] },
  "math.abs": { to: "abs", args: ["number"] },
  "math.sqrt": { to: "sqrt", args: ["number"] },
  "math.log": { to: "log", args: ["number"] },
  "math.max": { to: "max", args: ["number0", "number1"] },
  "math.min": { to: "min", args: ["number0", "number1"] },
};
const TUPLES: Record<string, { args: string[] }> = {
  "ta.macd": { args: ["source", "fastlen", "slowlen", "siglen"] },
  "ta.bb": { args: ["series", "length", "mult"] },
};
const OPERATORS = new Set([
  "+",
  "-",
  "*",
  "/",
  "%",
  "<",
  ">",
  "<=",
  ">=",
  "==",
  "!=",
  "and",
  "or",
]);
/** Statements with no effect in the app (ADR 0040), left out of the formula. */
const IGNORED = new Set(["indicator", "study", "alertcondition", "alert"]);
/** plot() settings the app does not use, so they change nothing. */
const IGNORED_PLOT_SETTINGS = new Set([
  "editable",
  "trackprice",
  "precision",
  "format",
  "join",
  "force_overlay",
]);

/** "#rrggbbaa" for a constant Pine colour, or undefined when it is not one. */
function colorConstant(expr: Expr): string | undefined {
  const hex = (r: number, g: number, b: number, alpha: number) =>
    "#" +
    [r, g, b, Math.round(Math.max(0, Math.min(1, alpha)) * 255)]
      .map((v) =>
        Math.max(0, Math.min(255, Math.round(v)))
          .toString(16)
          .padStart(2, "0"),
      )
      .join("");
  const parts = (
    text: string,
  ): [number, number, number, number] | undefined => {
    const long = /^#([0-9a-f]{6})([0-9a-f]{2})?$/i.exec(text);
    if (long) {
      const n = parseInt(long[1], 16);
      return [
        n >> 16,
        (n >> 8) & 255,
        n & 255,
        long[2] ? parseInt(long[2], 16) / 255 : 1,
      ];
    }
    return undefined;
  };
  if (expr.kind === "name" && COLORS[expr.name]) {
    const [r, g, b, a] = parts(COLORS[expr.name])!;
    return hex(r, g, b, a);
  }
  if (expr.kind === "string") {
    const value = parts(expr.value);
    return value ? hex(...value) : undefined;
  }
  if (expr.kind === "call" && expr.name === "color.new") {
    const base = expr.args[0] ?? expr.named.color;
    const transp = expr.args[1] ?? expr.named.transp;
    const color = base ? colorConstant(base) : undefined;
    if (!color || transp?.kind !== "number") return undefined;
    const [r, g, b] = parts(color)!;
    return hex(r, g, b, 1 - transp.value / 100);
  }
  if (expr.kind === "call" && expr.name === "color.rgb") {
    const names = ["red", "green", "blue", "transp"];
    const values = names.map((name, i) => expr.args[i] ?? expr.named[name]);
    if (values.slice(0, 3).some((v) => v?.kind !== "number")) return undefined;
    const transp = values[3];
    if (transp && transp.kind !== "number") return undefined;
    const [r, g, b] = values
      .slice(0, 3)
      .map((v) => (v as { value: number }).value);
    return hex(
      r,
      g,
      b,
      1 - (transp ? (transp as { value: number }).value : 0) / 100,
    );
  }
  return undefined;
}

/** Whether running this expression keeps state across bars (any ta.* call). */
function stateful(expr: Expr): boolean {
  switch (expr.kind) {
    case "call":
      return (
        expr.name.startsWith("ta.") ||
        [...expr.args, ...Object.values(expr.named)].some(stateful)
      );
    case "name":
      return expr.name.startsWith("ta.");
    case "index":
      return stateful(expr.target) || stateful(expr.offset);
    case "unary":
      return stateful(expr.operand);
    case "binary":
      return stateful(expr.left) || stateful(expr.right);
    case "ternary":
      return (
        stateful(expr.test) || stateful(expr.then) || stateful(expr.otherwise)
      );
    case "tuple":
      return expr.items.some(stateful);
    default:
      return false;
  }
}

const quote = (text: string) => `"${text.replace(/["\n]/g, "'").slice(0, 40)}"`;
const numberText = (value: number) =>
  Number.isFinite(value) ? String(value) : "na";

/** The formula for a Pine indicator, or why it stays Pine. */
export function convertPine(source: string): PineConversion {
  let program: PineProgram;
  try {
    program = compilePine(source);
  } catch (error) {
    if (error instanceof PineError) {
      return {
        ok: false,
        reason: error.message,
        line: error.line,
        column: error.column,
      };
    }
    throw error;
  }
  try {
    return convertProgram(program);
  } catch (error) {
    if (error instanceof Refuse) {
      return {
        ok: false,
        reason: error.message,
        line: error.line,
        column: error.column,
      };
    }
    throw error;
  }
}

function convertProgram(program: PineProgram): PineConversion {
  const top = { line: 1, column: 1 };
  if (program.strategy)
    refuse("strategies stay Pine Script (they run a chart backtest)", top);
  if (program.requests.length > 0)
    refuse("request.security is not converted yet", top);

  const taken = new Set<string>([...RESERVED, ...FORMULA_FIELDS]);
  const names = new Map<string, string>(); // Pine variable → formula name
  const colorNames = new Set<string>(); // Pine variables that hold a colour
  const keys: Record<string, string> = {};
  const lines: string[] = [];
  let hoisted = 0;

  const fresh = (wanted: string): string => {
    let base = wanted.toLowerCase().replace(/[^a-z0-9_]/g, "_") || "v";
    if (/^[0-9]/.test(base)) base = `_${base}`;
    let name = base;
    for (let n = 2; taken.has(name) || isFunctionName(name); n += 1)
      name = `${base}_${n}`;
    taken.add(name);
    return name;
  };

  /** A Pine input call as a formula setting; returns the setting's name. */
  const input = (
    call: Extract<Expr, { kind: "call" }>,
    wanted: string | null,
  ): string => {
    if (
      ![
        "input",
        "input.int",
        "input.float",
        "input.bool",
        "input.source",
      ].includes(call.name)
    ) {
      refuse(
        `${call.name}: text, colour and time settings are not converted yet`,
        call,
      );
    }
    const key = program.inputKeys.get(call.id);
    const declared = key
      ? program.inputs.find((item) => item.key === key)
      : undefined;
    if (!key || !declared || declared.choices)
      refuse("text settings are not converted yet", call);
    const name = fresh(wanted ?? key!);
    keys[key!] = name;
    const title = quote(declared!.label.en);
    if (declared!.source) {
      lines.push(
        `${name} = input(${SOURCES[declared!.default] ?? "close"}, ${title})`,
      );
    } else if (declared!.options) {
      lines.push(
        `${name} = input(${declared!.default === 1 ? "true" : "false"}, ${title})`,
      );
    } else {
      const decimal =
        declared!.step !== undefined && !Number.isInteger(declared!.step);
      const value = numberText(declared!.default);
      lines.push(
        `${name} = input(${decimal && !value.includes(".") ? `${value}.0` : value}, ${title}, minval = ${numberText(declared!.min)}, maxval = ${numberText(declared!.max)}${declared!.step !== undefined ? `, step = ${numberText(declared!.step)}` : ""})`,
      );
    }
    return name;
  };

  /** Arguments in Pine's order: positional first, then named ones by name. */
  const ordered = (
    call: Extract<Expr, { kind: "call" }>,
    args: string[],
  ): (Expr | undefined)[] => {
    if (call.args.length > args.length)
      refuse(`${call.name} takes at most ${args.length} values`, call);
    const out: (Expr | undefined)[] = [...call.args];
    for (const [name, value] of Object.entries(call.named)) {
      const at = args.indexOf(name);
      if (at < 0) refuse(`${call.name}(${name}=…) is not converted yet`, value);
      out[at] = value;
    }
    return out;
  };

  const colorExpr = (expr: Expr): string => {
    const constant = colorConstant(expr);
    if (constant) return `"${constant}"`;
    if (expr.kind === "na") return "na";
    if (expr.kind === "name" && colorNames.has(expr.name))
      return names.get(expr.name)!;
    if (expr.kind === "ternary") {
      if (stateful(expr.then) || stateful(expr.otherwise)) {
        refuse(
          "ta.* inside one side of ?: runs on only some bars in Pine",
          expr,
        );
      }
      return `(${value(expr.test)} ? ${colorExpr(expr.then)} : ${colorExpr(expr.otherwise)})`;
    }
    return refuse(
      "this colour is not converted yet (constants, na and ?: are)",
      expr,
    );
  };
  const isColor = (expr: Expr): boolean =>
    colorConstant(expr) !== undefined ||
    (expr.kind === "name" &&
      (colorNames.has(expr.name) || expr.name.startsWith("color."))) ||
    (expr.kind === "call" && expr.name.startsWith("color.")) ||
    (expr.kind === "ternary" &&
      (isColor(expr.then) || isColor(expr.otherwise)) &&
      [expr.then, expr.otherwise].every(
        (side) => side.kind === "na" || isColor(side),
      ));

  const value = (expr: Expr): string => {
    switch (expr.kind) {
      case "number":
        return numberText(expr.value);
      case "bool":
        return expr.value ? "true" : "false";
      case "na":
        return "na";
      case "string":
        return refuse("text values are not converted", expr);
      case "name": {
        const mapped = names.get(expr.name);
        if (mapped) {
          if (colorNames.has(expr.name))
            refuse(`"${expr.name}" is a colour here`, expr);
          return mapped;
        }
        if ((FORMULA_FIELDS as readonly string[]).includes(expr.name))
          return expr.name;
        if (expr.name === "hlcc4") return "((high + low + 2 * close) / 4)";
        if (expr.name === "ta.tr") return "tr()";
        if (expr.name === "ta.obv") return "obv()";
        if (expr.name === "ta.vwap") return "vwap()";
        return refuse(`"${expr.name}" is not converted yet`, expr);
      }
      case "index": {
        const offset = expr.offset;
        if (
          offset.kind !== "number" ||
          !Number.isInteger(offset.value) ||
          offset.value < 0 ||
          offset.value > 500
        ) {
          refuse(
            "only [n] with a whole number from 0 to 500 is converted",
            offset,
          );
        }
        return `(${value(expr.target)})[${(offset as { value: number }).value}]`;
      }
      case "unary":
        if (expr.op === "not") return `(not ${value(expr.operand)})`;
        return expr.op === "-"
          ? `(-${value(expr.operand)})`
          : value(expr.operand);
      case "binary": {
        if (!OPERATORS.has(expr.op))
          refuse(`the operator ${expr.op} is not converted yet`, expr);
        if (
          (expr.op === "and" || expr.op === "or") &&
          program.version >= 6 &&
          stateful(expr.right)
        ) {
          refuse(
            `from version 6, ta.* on the right of ${expr.op} runs on only some bars`,
            expr.right,
          );
        }
        return `(${value(expr.left)} ${expr.op} ${value(expr.right)})`;
      }
      case "ternary":
        if (stateful(expr.then) || stateful(expr.otherwise)) {
          refuse(
            "ta.* inside one side of ?: runs on only some bars in Pine",
            expr,
          );
        }
        return `(${value(expr.test)} ? ${value(expr.then)} : ${value(expr.otherwise)})`;
      case "call":
        return call(expr);
      default:
        return refuse("this is not converted yet", expr);
    }
  };

  const call = (expr: Extract<Expr, { kind: "call" }>): string => {
    if (expr.name === "input" || expr.name.startsWith("input.")) {
      hoisted += 1;
      return input(expr, `input_${hoisted}`);
    }
    if (TUPLES[expr.name])
      refuse(
        `${expr.name} gives several values; write [a, b, c] = ${expr.name}(…)`,
        expr,
      );
    const spec = FUNCTIONS[expr.name];
    if (!spec) return refuse(`${expr.name}() is not converted yet`, expr);
    let args = ordered(expr, spec.args);
    if (expr.name === "ta.highest" || expr.name === "ta.lowest") {
      // ta.highest(length) reads high; ta.highest(source, length) the source.
      if (args.length === 1 && Object.keys(expr.named).length === 0) {
        args = [
          {
            ...expr,
            kind: "name",
            name: expr.name === "ta.highest" ? "high" : "low",
          } as Expr,
          args[0],
        ];
      }
    }
    if (
      expr.name === "ta.stdev" &&
      args[2] &&
      !(args[2].kind === "bool" && args[2].value)
    ) {
      refuse("ta.stdev with biased=false is not converted yet", args[2]);
    }
    if (
      expr.name === "ta.vwap" &&
      (args.length !== 1 || args[0]?.kind !== "name" || args[0].name !== "hlc3")
    ) {
      refuse("only ta.vwap(hlc3) is converted", expr);
    }
    if (
      expr.name === "ta.tr" &&
      args[0] &&
      !(args[0].kind === "bool" && !args[0].value)
    ) {
      refuse("ta.tr(true) is not converted yet", expr);
    }
    if (expr.name === "math.max" || expr.name === "math.min") {
      if (args.length !== 2)
        refuse(
          `${expr.name} with other than two values is not converted yet`,
          expr,
        );
    }
    const kept =
      expr.name === "ta.stdev" ||
      expr.name === "ta.vwap" ||
      expr.name === "ta.tr"
        ? args.slice(0, expr.name === "ta.stdev" ? 2 : 0)
        : args;
    if (kept.some((arg) => arg === undefined))
      refuse(`${expr.name}: a value is missing`, expr);
    return `${spec.to}(${(kept as Expr[]).map(value).join(", ")})`;
  };

  const plot = (expr: Extract<Expr, { kind: "call" }>) => {
    const key = program.plotKeys.get(expr.id);
    const meta = key
      ? program.plots.find((item) => item.key === key)
      : undefined;
    if (!meta) return refuse("this plot() is not converted", expr);
    const order = [
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
    ];
    const args = ordered(expr, order);
    order.forEach((name, i) => {
      const given = args[i];
      if (!given || i < 5 || IGNORED_PLOT_SETTINGS.has(name)) return;
      if (name === "offset" && given.kind === "number" && given.value === 0)
        return;
      if (
        name === "display" &&
        given.kind === "name" &&
        given.name === "display.all"
      )
        return;
      refuse(`plot(${name}=…) is not converted yet`, given);
    });
    if (meta.hidden)
      refuse("plot(display=display.none) is not converted yet", expr);
    const series = args[0];
    if (!series) return refuse("plot needs a value", expr);
    const color = args[2];
    const colorText = color
      ? colorExpr(color)
      : `"${colorConstant({ ...expr, kind: "string", value: meta.color } as Expr) ?? DEFAULT_PLOT_COLOR}"`;
    lines.push(
      `plot(${value(series)}, ${quote(meta.title)}, ${colorText}, linewidth = ${meta.width}${meta.kind === "histogram" ? ', style = "histogram"' : ""})`,
    );
  };

  for (const stmt of program.body as Stmt[]) {
    switch (stmt.kind) {
      case "expr": {
        const expr = stmt.value;
        if (expr.kind !== "call") refuse("this line is not converted", stmt);
        const name = (expr as { name: string }).name;
        if (IGNORED.has(name) || name.startsWith("log.")) break;
        if (name === "plot") {
          plot(expr as Extract<Expr, { kind: "call" }>);
          break;
        }
        if (name === "hline") {
          const index = program.hlines.get(expr.id);
          const price = index === undefined ? null : program.hlinePrices[index];
          if (price === null || price === undefined)
            refuse(
              "hline with a price that is not fixed is not converted",
              stmt,
            );
          lines.push(`hline(${numberText(price!)})`);
          break;
        }
        refuse(`${name}() is not converted yet`, stmt);
        break;
      }
      case "declare": {
        if (stmt.persistent) refuse("var is not converted yet", stmt);
        const expr = stmt.value;
        if (stmt.names.length > 1) {
          if (expr.kind !== "call" || !TUPLES[expr.name])
            refuse("only [..] = ta.macd(…) or ta.bb(…) is converted", stmt);
          const callExpr = expr as Extract<Expr, { kind: "call" }>;
          const args = ordered(callExpr, TUPLES[callExpr.name].args).map(
            (arg) =>
              arg
                ? value(arg)
                : refuse(`${callExpr.name}: a value is missing`, callExpr),
          );
          const parts =
            callExpr.name === "ta.macd"
              ? [
                  `macd(${args.join(", ")})`,
                  `macd_signal(${args.join(", ")})`,
                  `macd_hist(${args.join(", ")})`,
                ]
              : [
                  `sma(${args[0]}, ${args[1]})`,
                  `bb_upper(${args.join(", ")})`,
                  `bb_lower(${args.join(", ")})`,
                ];
          stmt.names.forEach((pineName, i) => {
            if (pineName === "_" || i >= parts.length) return;
            const name = fresh(pineName);
            names.set(pineName, name);
            lines.push(`${name} = ${parts[i]}`);
          });
          break;
        }
        const pineName = stmt.names[0];
        if (
          expr.kind === "call" &&
          (expr.name === "input" || expr.name.startsWith("input."))
        ) {
          names.set(pineName, input(expr, pineName));
          break;
        }
        if (isColor(expr)) {
          const text = colorExpr(expr);
          const name = fresh(pineName);
          names.set(pineName, name);
          colorNames.add(pineName);
          lines.push(`${name} = ${text}`);
          break;
        }
        const text = value(expr);
        const name = fresh(pineName);
        names.set(pineName, name);
        lines.push(`${name} = ${text}`);
        break;
      }
      case "assign":
        refuse(":= (changing a value) is not converted yet", stmt);
        break;
      case "function":
        refuse("functions you write are not converted yet", stmt);
        break;
      case "if":
      case "switch":
        refuse(`${stmt.kind} blocks are not converted yet`, stmt);
        break;
      default:
        refuse("loops are not converted yet", stmt);
    }
  }
  if (!lines.some((line) => line.startsWith("plot(")))
    refuse("the script draws no plot() line to convert", top);
  const formula = lines.join("\n");
  if (formula.length > MAX_FORMULA_LENGTH)
    refuse(
      `the formula would be longer than ${MAX_FORMULA_LENGTH} characters`,
      top,
    );
  try {
    parseFormula(formula);
  } catch (error) {
    if (error instanceof FormulaError) {
      const at = formulaPosition(formula, error.position);
      refuse(
        `the formula would not be valid: ${error.message} (formula line ${at.line})`,
        top,
      );
    }
    throw error;
  }
  return {
    ok: true,
    formula,
    label: (program.short || program.title).slice(0, 40),
    pane: program.overlay ? "price" : "separate",
    keys,
  };
}

const FORMULA_FUNCTION_NAMES = new Set([
  "sma",
  "ema",
  "wma",
  "rma",
  "stdev",
  "highest",
  "lowest",
  "sum",
  "change",
  "roc",
  "shift",
  "rsi",
  "atr",
  "tr",
  "vwap",
  "obv",
  "cum",
  "macd",
  "macd_signal",
  "macd_hist",
  "bb_upper",
  "bb_lower",
  "stoch",
  "cci",
  "mfi",
  "crossover",
  "crossunder",
  "nz",
  "na",
  "abs",
  "sqrt",
  "log",
  "max",
  "min",
]);
const isFunctionName = (name: string) => FORMULA_FUNCTION_NAMES.has(name);

/** The formula's settings for the script's settings (same values, price choices mapped). */
export function formulaParams(
  conversion: Extract<PineConversion, { ok: true }>,
  pineSource: string,
  params: Record<string, number>,
): Record<string, number> {
  const program = compilePine(pineSource);
  const out: Record<string, number> = {};
  for (const [pineKey, formulaKey] of Object.entries(conversion.keys)) {
    const declared = program.inputs.find((item) => item.key === pineKey);
    const given = params[pineKey] ?? declared?.default;
    if (given === undefined) continue;
    out[formulaKey] = declared?.source
      ? FORMULA_FIELDS.indexOf(SOURCES[Math.round(given)] ?? "close")
      : given;
  }
  return out;
}

/** Fixed sample bars for the check: several days, a flat stretch and quiet bars. */
export function sampleBars(): OhlcvBar[] {
  return Array.from({ length: 600 }, (_, i) => {
    const flat = i >= 200 && i < 215;
    const close = flat
      ? 52
      : 50 + 4 * Math.sin(i / 13) + 1.5 * Math.cos(i / 3.7) + (i > 420 ? 3 : 0);
    return {
      time: 1_760_000_000 + i * 900,
      open: flat ? 52 : close - 0.2 * Math.sin(i * 1.3),
      high: flat ? 52 : close + 0.3 + 0.25 * Math.abs(Math.sin(i / 2.3)),
      low: flat ? 52 : close - 0.35 - 0.2 * Math.abs(Math.cos(i / 4.1)),
      close,
      volume: i % 97 === 0 ? 0 : 500 + ((i * 53) % 700),
    };
  });
}

/** [r, g, b, a] of "#rrggbb", "#rrggbbaa" or "rgba(…)"; null when it is not a colour. */
function colorParts(color: string | null | undefined): number[] | null {
  if (!color) return null;
  const hex = /^#([0-9a-f]{6})([0-9a-f]{2})?$/i.exec(color);
  if (hex) {
    const n = parseInt(hex[1], 16);
    return [
      n >> 16,
      (n >> 8) & 255,
      n & 255,
      hex[2] ? parseInt(hex[2], 16) / 255 : 1,
    ];
  }
  const rgba = /^rgba?\(([^)]+)\)$/.exec(color);
  if (rgba) {
    const [r, g, b, a] = rgba[1].split(",").map((part) => Number(part.trim()));
    return [r, g, b, a ?? 1];
  }
  return null;
}

const sameColor = (
  a: string | null | undefined,
  b: string | null | undefined,
) => {
  const x = colorParts(a);
  const y = colorParts(b);
  if (!x || !y) return x === y;
  // A fully transparent colour hides the line whatever its channels.
  if (x[3] < 0.01 && y[3] < 0.01) return true;
  return x.every((v, i) => Math.abs(v - y[i]) <= (i === 3 ? 0.01 : 1));
};

/** Whether the script and its formula drew the same on these bar sets (ADR 0058 §4). */
export function compareConversion(
  pineSource: string,
  conversion: Extract<PineConversion, { ok: true }>,
  params: Record<string, number>,
  barSets: OhlcvBar[][],
  context?: IndicatorContext,
): { same: true; bars: number } | { same: false; reason: string } {
  const program = compilePine(pineSource);
  const formula = parseFormula(conversion.formula);
  const settings = formulaParams(conversion, pineSource, params);
  const pineColorOf = new Map(
    program.plots.map((plot) => [plot.key, plot.color]),
  );
  const ourColorOf = new Map(
    formulaDefinition(
      conversion.formula,
      conversion.label,
      conversion.pane,
    ).outputs.map((output) => [output.key, output.color]),
  );
  let bars = 0;
  for (const set of barSets) {
    if (set.length === 0) continue;
    const pine = runPine(
      program,
      set,
      params,
      context as PineContext | undefined,
    );
    const ours = evaluateFormula(formula, set, settings, context);
    for (const key of Object.keys(pine.values)) {
      const a: Series = ours.values[key];
      const b: Series = pine.values[key];
      if (!a)
        return { same: false, reason: `${key} is missing from the formula` };
      for (let i = 0; i < b.length; i += 1) {
        const x = a[i];
        const y = b[i];
        const equal =
          x === null || y === null
            ? x === y
            : Math.abs(x - y) <= 1e-9 * Math.max(1, Math.abs(y));
        if (!equal) {
          return {
            same: false,
            reason: `${key} differs at bar ${i} of ${set.length} (${x} vs ${y})`,
          };
        }
        // A bar without its own colour draws in the plot's colour.
        const pineColor = pine.colors[key]?.[i] ?? pineColorOf.get(key);
        const ourColor = ours.colors[key]?.[i] ?? ourColorOf.get(key);
        if (x !== null && !sameColor(ourColor, pineColor)) {
          return {
            same: false,
            reason: `${key}'s colour differs at bar ${i} of ${set.length} (${ourColor} vs ${pineColor})`,
          };
        }
      }
    }
    bars += set.length;
  }
  return { same: true, bars };
}

/**
 * A Pine indicator as a formula indicator, checked on the chart's bars and the sample
 * bars, or the reason it stays Pine. The instance keeps its key, settings and style.
 */
export function convertInstance(
  instance: IndicatorInstance,
  chartBars: OhlcvBar[],
  context?: IndicatorContext,
): { instance: IndicatorInstance } | { reason: string } {
  const source = instance.pine ?? "";
  const conversion = convertPine(source);
  if (!conversion.ok) {
    return { reason: `${conversion.reason} (line ${conversion.line})` };
  }
  let check: ReturnType<typeof compareConversion>;
  try {
    check = compareConversion(
      source,
      conversion,
      instance.params,
      [chartBars, sampleBars()],
      context,
    );
  } catch (error) {
    // The script failed on these bars (a limit, say): nothing to compare, it stays Pine.
    return {
      reason: `the script did not run on the bars to compare: ${error instanceof Error ? error.message : String(error)}`,
    };
  }
  if (!check.same)
    return { reason: `the formula and the script differ: ${check.reason}` };
  const definition = formulaDefinition(
    conversion.formula,
    conversion.label,
    conversion.pane,
  );
  const converted: IndicatorInstance = {
    key: instance.key,
    type: "formula",
    params: cleanParams(
      definition,
      formulaParams(conversion, source, instance.params),
    ),
    formula: conversion.formula,
    label: conversion.label,
    pane: conversion.pane,
    converted: {
      pine: source,
      sha256: sha256(source),
      converter: CONVERTER_VERSION,
      bars: check.bars,
    },
  };
  if (instance.hidden) converted.hidden = true;
  if (instance.style) converted.style = instance.style;
  if (instance.visibility) converted.visibility = instance.visibility;
  if (instance.timeframe) converted.timeframe = instance.timeframe;
  return { instance: converted };
}

/** The Pine indicator a converted formula came from, with its settings mapped back. */
export function revertInstance(
  instance: IndicatorInstance,
  pineParams: Record<string, number>,
): IndicatorInstance {
  const origin = instance.converted!;
  const reverted: IndicatorInstance = {
    key: instance.key,
    type: "pine",
    params: pineParams,
    pine: origin.pine,
  };
  if (instance.hidden) reverted.hidden = true;
  if (instance.style) reverted.style = instance.style;
  if (instance.visibility) reverted.visibility = instance.visibility;
  if (instance.timeframe) reverted.timeframe = instance.timeframe;
  return reverted;
}

/** Pine settings for a converted formula's settings (the reverse of formulaParams). */
export function pineParamsFor(
  instance: IndicatorInstance,
): Record<string, number> {
  const source = instance.converted?.pine ?? "";
  const conversion = convertPine(source);
  if (!conversion.ok) return {};
  const program = compilePine(source);
  const out: Record<string, number> = {};
  for (const [pineKey, formulaKey] of Object.entries(conversion.keys)) {
    const declared = program.inputs.find((item) => item.key === pineKey);
    const value = instance.params[formulaKey];
    if (value === undefined || !declared) continue;
    out[pineKey] = declared.source
      ? Math.max(
          0,
          SOURCES.indexOf(
            FORMULA_FIELDS[Math.round(value)] as (typeof SOURCES)[number],
          ),
        )
      : value;
  }
  return out;
}
