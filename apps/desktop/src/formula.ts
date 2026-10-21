/**
 * Formula indicators: a small, safe expression language over bars, evaluated in the
 * browser without `eval` (roadmap Phase 4: "a safe expression DSL; no arbitrary Python
 * from UI expressions").
 *
 *   (ema(close, 10) - sma(close, 30)) / atr(14)
 *
 * - Values: numbers, the bar fields open, high, low, close, volume, hl2, hlc3, ohlc4.
 * - Operators: + - * / and unary minus, with parentheses.
 * - Functions come from an allow-list; window lengths must be whole-number constants
 *   (1 to 500), and shift only looks back. Every function uses the current bar and
 *   earlier ones, so a formula cannot read the future.
 * - A missing value (warm-up, division by zero, non-finite result) stays missing.
 */
import {
  change,
  ema,
  highest,
  INDICATOR_BY_ID,
  lowest,
  rma,
  rsi,
  shift,
  sma,
  stdev,
  trueRange,
  wma,
  type IndicatorContext,
  type IndicatorDefinition,
  type OhlcvBar,
  type Series,
} from "./indicators";

export const MAX_FORMULA_LENGTH = 500;
const MAX_WINDOW = 500;
const MAX_NODES = 200;

export class FormulaError extends Error {
  constructor(
    message: string,
    readonly position: number,
  ) {
    super(message);
    this.name = "FormulaError";
  }
}

type Node =
  | { kind: "number"; value: number; at: number }
  | { kind: "field"; name: Field; at: number }
  | { kind: "unary"; operand: Node; at: number }
  | {
      kind: "binary";
      op: "+" | "-" | "*" | "/";
      left: Node;
      right: Node;
      at: number;
    }
  | { kind: "call"; name: string; args: Node[]; at: number };

const FIELDS = [
  "open",
  "high",
  "low",
  "close",
  "volume",
  "hl2",
  "hlc3",
  "ohlc4",
] as const;
type Field = (typeof FIELDS)[number];

/** Each function: its arguments (s = series, n = window length constant). */
const FUNCTIONS: Record<string, { args: string; optional?: number }> = {
  sma: { args: "sn" },
  ema: { args: "sn" },
  wma: { args: "sn" },
  rma: { args: "sn" },
  stdev: { args: "sn" },
  highest: { args: "sn" },
  lowest: { args: "sn" },
  sum: { args: "sn" },
  change: { args: "sn", optional: 1 },
  roc: { args: "sn" },
  shift: { args: "sn" },
  rsi: { args: "sn" },
  atr: { args: "n" },
  vwap: { args: "" },
  obv: { args: "" },
  abs: { args: "s" },
  sqrt: { args: "s" },
  log: { args: "s" },
  max: { args: "ss" },
  min: { args: "ss" },
};
export const FORMULA_FUNCTIONS = Object.keys(FUNCTIONS);
export const FORMULA_FIELDS: readonly string[] = FIELDS;

interface Token {
  type: "number" | "name" | "op" | "(" | ")" | ",";
  text: string;
  at: number;
}

function tokenize(text: string): Token[] {
  const tokens: Token[] = [];
  let i = 0;
  while (i < text.length) {
    const char = text[i];
    if (/\s/.test(char)) {
      i += 1;
    } else if (/[0-9.]/.test(char)) {
      const match = /^\d*\.?\d+(?:[eE][+-]?\d+)?|^\d+\.?/.exec(text.slice(i));
      if (!match) throw new FormulaError(`bad number`, i);
      tokens.push({ type: "number", text: match[0], at: i });
      i += match[0].length;
    } else if (/[A-Za-z_]/.test(char)) {
      const match = /^[A-Za-z_][A-Za-z0-9_]*/.exec(text.slice(i));
      tokens.push({ type: "name", text: match![0].toLowerCase(), at: i });
      i += match![0].length;
    } else if ("+-*/".includes(char)) {
      tokens.push({ type: "op", text: char, at: i });
      i += 1;
    } else if (char === "(" || char === ")" || char === ",") {
      tokens.push({ type: char, text: char, at: i });
      i += 1;
    } else {
      throw new FormulaError(`unexpected "${char}"`, i);
    }
  }
  return tokens;
}

/** The parsed formula, or a FormulaError saying what is wrong and where. */
export function parseFormula(text: string): Node {
  if (text.length > MAX_FORMULA_LENGTH) {
    throw new FormulaError(
      `at most ${MAX_FORMULA_LENGTH} characters`,
      MAX_FORMULA_LENGTH,
    );
  }
  const tokens = tokenize(text);
  let index = 0;
  let nodes = 0;
  const peek = () => tokens[index];
  const end = text.length;
  const counted = (node: Node): Node => {
    nodes += 1;
    if (nodes > MAX_NODES)
      throw new FormulaError("the formula is too long", node.at);
    return node;
  };
  const expect = (type: Token["type"], what: string) => {
    const token = peek();
    if (!token || token.type !== type) {
      throw new FormulaError(`expected ${what}`, token?.at ?? end);
    }
    index += 1;
    return token;
  };

  function expression(): Node {
    let left = term();
    while (
      peek()?.type === "op" &&
      (peek().text === "+" || peek().text === "-")
    ) {
      const op = tokens[index++];
      left = counted({
        kind: "binary",
        op: op.text as "+" | "-",
        left,
        right: term(),
        at: op.at,
      });
    }
    return left;
  }
  function term(): Node {
    let left = unary();
    while (
      peek()?.type === "op" &&
      (peek().text === "*" || peek().text === "/")
    ) {
      const op = tokens[index++];
      left = counted({
        kind: "binary",
        op: op.text as "*" | "/",
        left,
        right: unary(),
        at: op.at,
      });
    }
    return left;
  }
  function unary(): Node {
    const token = peek();
    if (token?.type === "op" && (token.text === "-" || token.text === "+")) {
      index += 1;
      const operand = unary();
      return token.text === "-"
        ? counted({ kind: "unary", operand, at: token.at })
        : operand;
    }
    return primary();
  }
  function primary(): Node {
    const token = peek();
    if (!token) throw new FormulaError("the formula ends too early", end);
    if (token.type === "number") {
      index += 1;
      const value = Number(token.text);
      if (!Number.isFinite(value))
        throw new FormulaError("bad number", token.at);
      return counted({ kind: "number", value, at: token.at });
    }
    if (token.type === "(") {
      index += 1;
      const inner = expression();
      expect(")", '")"');
      return inner;
    }
    if (token.type === "name") {
      index += 1;
      if (peek()?.type === "(") {
        const spec = FUNCTIONS[token.text];
        if (!spec)
          throw new FormulaError(`unknown function "${token.text}"`, token.at);
        index += 1;
        const args: Node[] = [];
        if (peek()?.type !== ")") {
          args.push(expression());
          while (peek()?.type === ",") {
            index += 1;
            args.push(expression());
          }
        }
        expect(")", '")"');
        const least = spec.args.length - (spec.optional ?? 0);
        if (args.length < least || args.length > spec.args.length) {
          throw new FormulaError(
            `${token.text} takes ${least === spec.args.length ? least : `${least} or ${spec.args.length}`} arguments`,
            token.at,
          );
        }
        args.forEach((arg, i) => {
          if (spec.args[i] !== "n") return;
          if (
            arg.kind !== "number" ||
            !Number.isInteger(arg.value) ||
            arg.value < (token.text === "shift" ? 0 : 1) ||
            arg.value > MAX_WINDOW
          ) {
            throw new FormulaError(
              `${token.text}: the length must be a whole number up to ${MAX_WINDOW}`,
              arg.at,
            );
          }
        });
        return counted({ kind: "call", name: token.text, args, at: token.at });
      }
      if ((FIELDS as readonly string[]).includes(token.text)) {
        return counted({
          kind: "field",
          name: token.text as Field,
          at: token.at,
        });
      }
      throw new FormulaError(
        `unknown name "${token.text}" (use ${FIELDS.join(", ")} or a function)`,
        token.at,
      );
    }
    throw new FormulaError(`unexpected "${token.text}"`, token.at);
  }

  if (tokens.length === 0) throw new FormulaError("write a formula", 0);
  const tree = expression();
  if (index < tokens.length) {
    throw new FormulaError(
      `unexpected "${tokens[index].text}"`,
      tokens[index].at,
    );
  }
  return tree;
}

/** How many earlier bars the formula reads at most, for warm-up. */
export function formulaLookback(node: Node): number {
  switch (node.kind) {
    case "number":
    case "field":
      return 0;
    case "unary":
      return formulaLookback(node.operand);
    case "binary":
      return Math.max(formulaLookback(node.left), formulaLookback(node.right));
    case "call": {
      const inner = Math.max(0, ...node.args.map(formulaLookback));
      const at = FUNCTIONS[node.name].args.indexOf("n");
      const length = at >= 0 ? node.args[at] : undefined;
      const own =
        length?.kind === "number"
          ? length.value
          : node.name === "change"
            ? 1
            : 0;
      return inner + own;
    }
  }
}

const finite = (value: number): number | null =>
  Number.isFinite(value) ? value : null;

function map2(
  a: Series,
  b: Series,
  op: (x: number, y: number) => number,
): Series {
  return a.map((x, i) => {
    const y = b[i];
    return x === null || y === null ? null : finite(op(x, y));
  });
}

/** Evaluates a parsed formula over bars: one value per bar, null when missing. */
/** A formula as an indicator definition, so the chart draws it like any other. */
export function formulaDefinition(
  formula: string,
  label: string,
  pane: "price" | "separate",
): IndicatorDefinition {
  let tree: Node | null = null;
  try {
    tree = parseFormula(formula);
  } catch {
    tree = null; // drawn as missing; the settings show why
  }
  return {
    id: "formula",
    name: { en: label, ko: label },
    short: label,
    category: "custom",
    pane,
    inputs: [],
    outputs: [{ key: "value", label, color: "#e6c547" }],
    compute: (bars, _params, context) =>
      tree
        ? { value: evaluateFormula(tree, bars, context) }
        : { value: bars.map(() => null) },
  };
}

export function evaluateFormula(
  node: Node,
  bars: OhlcvBar[],
  context?: IndicatorContext,
): Series {
  const count = bars.length;
  const constant = (value: number): Series => bars.map(() => value);
  const run = (item: Node): Series => {
    switch (item.kind) {
      case "number":
        return constant(item.value);
      case "field":
        return bars.map((bar) => {
          switch (item.name) {
            case "hl2":
              return (bar.high + bar.low) / 2;
            case "hlc3":
              return (bar.high + bar.low + bar.close) / 3;
            case "ohlc4":
              return (bar.open + bar.high + bar.low + bar.close) / 4;
            default:
              return bar[item.name];
          }
        });
      case "unary":
        return run(item.operand).map((value) =>
          value === null ? null : -value,
        );
      case "binary": {
        const left = run(item.left);
        const right = run(item.right);
        switch (item.op) {
          case "+":
            return map2(left, right, (x, y) => x + y);
          case "-":
            return map2(left, right, (x, y) => x - y);
          case "*":
            return map2(left, right, (x, y) => x * y);
          case "/":
            return map2(left, right, (x, y) => (y === 0 ? NaN : x / y));
        }
        break;
      }
      case "call": {
        const [first, second] = item.args;
        const n = (arg: Node | undefined, fallback = 1) =>
          arg && arg.kind === "number" ? arg.value : fallback;
        switch (item.name) {
          case "sma":
            return sma(run(first), n(second));
          case "ema":
            return ema(run(first), n(second));
          case "wma":
            return wma(run(first), n(second));
          case "rma":
            return rma(run(first), n(second));
          case "stdev":
            return stdev(run(first), n(second));
          case "highest":
            return highest(run(first), n(second));
          case "lowest":
            return lowest(run(first), n(second));
          case "sum":
            return sma(run(first), n(second)).map((value) =>
              value === null ? null : value * n(second),
            );
          case "change":
            return change(run(first), n(second));
          case "roc": {
            const source = run(first);
            const before = shift(source, n(second));
            return map2(source, before, (x, y) =>
              y === 0 ? NaN : ((x - y) / y) * 100,
            );
          }
          case "shift":
            return shift(run(first), n(second));
          case "rsi":
            return rsi(run(first), n(second));
          case "atr":
            return rma(trueRange(bars), n(first));
          case "vwap":
            return INDICATOR_BY_ID.get("vwap")!.compute(bars, {}, context)
              .value;
          case "obv":
            return INDICATOR_BY_ID.get("obv")!.compute(bars, {}, context).value;
          case "abs":
            return run(first).map((value) =>
              value === null ? null : Math.abs(value),
            );
          case "sqrt":
            return run(first).map((value) =>
              value === null || value < 0 ? null : Math.sqrt(value),
            );
          case "log":
            return run(first).map((value) =>
              value === null || value <= 0 ? null : Math.log(value),
            );
          case "max":
            return map2(run(first), run(second), Math.max);
          case "min":
            return map2(run(first), run(second), Math.min);
        }
      }
    }
    return Array.from({ length: count }, () => null);
  };
  return run(node);
}
