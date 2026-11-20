/**
 * Formula indicators: a small, safe expression language over bars, evaluated in the
 * browser without `eval` (roadmap Phase 4: "a safe expression DSL; no arbitrary Python
 * from UI expressions"; ADR 0026, widened by ADR 0058 §2).
 *
 * One expression is one line on the chart, as before:
 *
 *   (ema(close, 10) - sma(close, 30)) / atr(14)
 *
 * Several statements (one per line, or separated by `;`) name values, declare settings
 * and draw several lines:
 *
 *   fast = input(12, "Fast length")
 *   slow = input(26, "Slow length")
 *   line = ema(close, fast) - ema(close, slow)
 *   plot(line, "MACD", line > 0 ? "#26a69a" : "#ef5350", 2)
 *   plot(ema(line, 9), "Signal")
 *   hline(0)
 *
 * - Values: numbers, true/false, na, colours ("#rrggbb" or "#rrggbbaa"), the bar fields.
 * - Operators: + - * / %, comparisons, and/or/not, `cond ? a : b`, and `x[n]` for the
 *   value n bars back (n a whole-number constant).
 * - Functions come from an allow-list; window lengths are whole-number constants or
 *   number settings (1 to 500). Every function uses the current bar and earlier ones,
 *   so a formula cannot read the future.
 * - A missing value (warm-up, division by zero, non-finite result) stays missing.
 *   Comparing with a missing value is false, as in Pine Script.
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
  type IndicatorInput,
  type IndicatorOutput,
  type OhlcvBar,
  type Series,
} from "./indicators";

export const MAX_FORMULA_LENGTH = 4_000;
const MAX_WINDOW = 500;
const MAX_NODES = 1_000;
const MAX_INPUTS = 32;
const MAX_PLOTS = 16;
const MAX_LEVELS = 16;
const MAX_WIDTH = 4;
const DEFAULT_COLOR = "#e6c547";
const PALETTE = [
  "#e6c547",
  "#4f9cf9",
  "#ef5350",
  "#26a69a",
  "#ab47bc",
  "#ff9800",
  "#90a4ae",
];

export class FormulaError extends Error {
  constructor(
    message: string,
    readonly position: number,
  ) {
    super(message);
    this.name = "FormulaError";
  }
}

/** The line and column (both from 1) of a character position in the text. */
export function formulaPosition(
  text: string,
  position: number,
): { line: number; column: number } {
  const before = text.slice(0, position).split("\n");
  return { line: before.length, column: before[before.length - 1].length + 1 };
}

type Type = "num" | "bool" | "color" | "na";

type BinaryOp =
  | "+"
  | "-"
  | "*"
  | "/"
  | "%"
  | "<"
  | ">"
  | "<="
  | ">="
  | "=="
  | "!="
  | "and"
  | "or";

type Node = { at: number; type: Type } & (
  | { kind: "number"; value: number }
  | { kind: "bool"; value: boolean }
  | { kind: "na" }
  | { kind: "color"; value: string }
  | { kind: "field"; name: Field }
  | { kind: "ref"; name: string }
  | { kind: "unary"; op: "-" | "not"; operand: Node }
  | { kind: "binary"; op: BinaryOp; left: Node; right: Node }
  | { kind: "ternary"; test: Node; then: Node; otherwise: Node }
  | { kind: "call"; name: string; args: Node[] }
);

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
const isField = (name: string): name is Field =>
  (FIELDS as readonly string[]).includes(name);

/**
 * Each function: its arguments (s = number series, n = window length) and result.
 * `optional` trailing arguments may be left out; `lower` is the least length (shift
 * takes 0). The Pine Script function each one matches is in the comment.
 */
const FUNCTIONS: Record<
  string,
  { args: string; optional?: number; result?: "bool"; lower?: number }
> = {
  sma: { args: "sn" }, // ta.sma
  ema: { args: "sn" }, // ta.ema
  wma: { args: "sn" }, // ta.wma
  rma: { args: "sn" }, // ta.rma
  stdev: { args: "sn" }, // ta.stdev
  highest: { args: "sn" }, // ta.highest
  lowest: { args: "sn" }, // ta.lowest
  sum: { args: "sn" }, // math.sum
  change: { args: "sn", optional: 1 }, // ta.change
  roc: { args: "sn" }, // ta.roc
  shift: { args: "sn", lower: 0 }, // x[n]
  rsi: { args: "sn" }, // ta.rsi
  atr: { args: "n" }, // ta.atr
  tr: { args: "" }, // ta.tr
  vwap: { args: "" }, // ta.vwap(hlc3)
  obv: { args: "" }, // ta.obv
  cum: { args: "s" }, // ta.cum
  macd: { args: "snnn" }, // ta.macd, first value
  macd_signal: { args: "snnn" }, // ta.macd, second value
  macd_hist: { args: "snnn" }, // ta.macd, third value
  bb_upper: { args: "sns" }, // ta.bb, second value (basis is sma)
  bb_lower: { args: "sns" }, // ta.bb, third value
  stoch: { args: "sssn" }, // ta.stoch(source, high, low, length)
  cci: { args: "sn" }, // ta.cci
  mfi: { args: "sn" }, // ta.mfi
  crossover: { args: "ss", result: "bool" }, // ta.crossover
  crossunder: { args: "ss", result: "bool" }, // ta.crossunder
  nz: { args: "ss", optional: 1 }, // nz
  na: { args: "s", result: "bool" }, // na()
  abs: { args: "s" }, // math.abs
  sqrt: { args: "s" }, // math.sqrt
  log: { args: "s" }, // math.log
  max: { args: "ss" }, // math.max
  min: { args: "ss" }, // math.min
};
export const FORMULA_FUNCTIONS = Object.keys(FUNCTIONS);
export const FORMULA_FIELDS: readonly string[] = FIELDS;

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

export interface FormulaInput {
  key: string;
  label: string;
  kind: "number" | "bool" | "source";
  default: number;
  min: number;
  max: number;
  step?: number;
  /** Used as a window length, so a whole number within the window range. */
  length: boolean;
}

export interface FormulaPlot {
  key: string;
  title: string;
  value: Node;
  color: Node | null;
  width: number;
  kind: "line" | "histogram";
}

export interface FormulaProgram {
  inputs: FormulaInput[];
  bindings: { name: string; value: Node }[];
  plots: FormulaPlot[];
  levels: number[];
  /** A single expression without plot(): drawn as the one output "value". */
  single: boolean;
}

interface Token {
  type:
    | "number"
    | "name"
    | "string"
    | "op"
    | "("
    | ")"
    | "["
    | "]"
    | ","
    | "?"
    | ":"
    | "="
    | "end";
  text: string;
  at: number;
}

const OPERATORS = ["<=", ">=", "==", "!=", "+", "-", "*", "/", "%", "<", ">"];

function tokenize(text: string): Token[] {
  const tokens: Token[] = [];
  let depth = 0;
  let i = 0;
  // A line break ends a statement, unless inside brackets or after an operator.
  const breakStatement = (at: number) => {
    const last = tokens[tokens.length - 1];
    if (depth > 0 || !last || last.type === "end") return;
    if (["op", ",", "?", ":", "=", "("].includes(last.type)) return;
    tokens.push({ type: "end", text: "line break", at });
  };
  while (i < text.length) {
    const char = text[i];
    if (char === "\n" || char === ";") {
      if (char === ";") {
        const last = tokens[tokens.length - 1];
        if (last && last.type !== "end")
          tokens.push({ type: "end", text: ";", at: i });
      } else {
        breakStatement(i);
      }
      i += 1;
    } else if (/\s/.test(char)) {
      i += 1;
    } else if (char === "/" && text[i + 1] === "/") {
      while (i < text.length && text[i] !== "\n") i += 1;
    } else if (/[0-9.]/.test(char)) {
      const match = /^\d*\.?\d+(?:[eE][+-]?\d+)?|^\d+\.?/.exec(text.slice(i));
      if (!match) throw new FormulaError(`bad number`, i);
      tokens.push({ type: "number", text: match[0], at: i });
      i += match[0].length;
    } else if (/[A-Za-z_]/.test(char)) {
      const match = /^[A-Za-z_][A-Za-z0-9_]*/.exec(text.slice(i));
      tokens.push({ type: "name", text: match![0].toLowerCase(), at: i });
      i += match![0].length;
    } else if (char === '"' || char === "'") {
      const close = text.indexOf(char, i + 1);
      if (close < 0 || text.slice(i + 1, close).includes("\n")) {
        throw new FormulaError("a text is not closed", i);
      }
      tokens.push({ type: "string", text: text.slice(i + 1, close), at: i });
      i = close + 1;
    } else {
      const op = OPERATORS.find((item) => text.startsWith(item, i));
      if (op) {
        tokens.push({ type: "op", text: op, at: i });
        i += op.length;
      } else if ("()[],?:=".includes(char)) {
        if (char === "(" || char === "[") depth += 1;
        if (char === ")" || char === "]") depth = Math.max(0, depth - 1);
        tokens.push({ type: char as Token["type"], text: char, at: i });
        i += 1;
      } else {
        throw new FormulaError(`unexpected "${char}"`, i);
      }
    }
  }
  return tokens;
}

const COLOR = /^#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/;

/** "#rrggbbaa" as the chart's rgba(); "#rrggbb" as it is. */
function cssColor(hex: string): string {
  if (hex.length === 7) return hex.toLowerCase();
  const part = (offset: number) => parseInt(hex.slice(offset, offset + 2), 16);
  return `rgba(${part(1)}, ${part(3)}, ${part(5)}, ${Number((part(7) / 255).toFixed(3))})`;
}

/** Both sides' type, or why they do not go together. */
function unify(a: Type, b: Type): Type | null {
  if (a === "na") return b;
  if (b === "na") return a;
  return a === b ? a : null;
}

const TYPE_NAMES: Record<Type, string> = {
  num: "a number",
  bool: "true/false",
  color: "a colour",
  na: "na",
};

/** The parsed formula, or a FormulaError saying what is wrong and where. */
export function parseFormula(text: string): FormulaProgram {
  if (text.length > MAX_FORMULA_LENGTH) {
    throw new FormulaError(
      `at most ${MAX_FORMULA_LENGTH} characters`,
      MAX_FORMULA_LENGTH,
    );
  }
  const tokens = tokenize(text);
  while (tokens[tokens.length - 1]?.type === "end") tokens.pop();
  let index = 0;
  let nodes = 0;
  const end = text.length;
  const peek = (ahead = 0): Token | undefined => tokens[index + ahead];
  const fail = (message: string, at: number): never => {
    throw new FormulaError(message, at);
  };
  const make = (node: Node): Node => {
    nodes += 1;
    if (nodes > MAX_NODES) fail("the formula is too long", node.at);
    return node;
  };
  const expect = (type: Token["type"], what: string): Token => {
    const token = peek();
    if (!token || token.type !== type) {
      return fail(`expected ${what}`, token?.at ?? end);
    }
    index += 1;
    return token;
  };
  const isOp = (text: string) => {
    const token = peek();
    return (
      (token?.type === "op" || token?.type === "name") && token.text === text
    );
  };

  const inputs: FormulaInput[] = [];
  const bindings: { name: string; value: Node }[] = [];
  const types = new Map<string, Type>();
  const plots: FormulaPlot[] = [];
  const levels: number[] = [];
  const bare: Node[] = [];

  const want = (node: Node, type: Type, what: string) => {
    if (unify(node.type, type) !== type) {
      fail(`${what} must be ${TYPE_NAMES[type]}`, node.at);
    }
  };

  // --- expressions, lowest precedence first (as in Pine Script) ---
  function expression(): Node {
    const test = or();
    if (peek()?.type !== "?") return test;
    const at = expect("?", '"?"').at;
    const then = expression();
    expect(":", '":"');
    const otherwise = expression();
    want(test, "bool", "the condition before ?");
    const type = unify(then.type, otherwise.type);
    if (type === null) {
      fail("both sides of : must be the same kind of value", at);
    }
    return make({ kind: "ternary", test, then, otherwise, at, type: type! });
  }
  function logical(next: () => Node, op: "and" | "or"): Node {
    let left = next();
    while (isOp(op)) {
      const at = peek()!.at;
      index += 1;
      const right = next();
      want(left, "bool", `the left side of ${op}`);
      want(right, "bool", `the right side of ${op}`);
      left = make({ kind: "binary", op, left, right, at, type: "bool" });
    }
    return left;
  }
  const or = (): Node => logical(and, "or");
  const and = (): Node => logical(not, "and");
  function not(): Node {
    if (isOp("not")) {
      const at = peek()!.at;
      index += 1;
      const operand = not();
      want(operand, "bool", "the value after not");
      return make({ kind: "unary", op: "not", operand, at, type: "bool" });
    }
    return equality();
  }
  function equality(): Node {
    let left = comparison();
    while (isOp("==") || isOp("!=")) {
      const token = tokens[index++];
      const right = comparison();
      const type = unify(left.type, right.type);
      if (type === null || type === "color") {
        fail(`cannot compare these with ${token.text}`, token.at);
      }
      left = make({
        kind: "binary",
        op: token.text as BinaryOp,
        left,
        right,
        at: token.at,
        type: "bool",
      });
    }
    return left;
  }
  function comparison(): Node {
    let left = additive();
    while (isOp("<") || isOp(">") || isOp("<=") || isOp(">=")) {
      const token = tokens[index++];
      const right = additive();
      want(left, "num", `the left side of ${token.text}`);
      want(right, "num", `the right side of ${token.text}`);
      left = make({
        kind: "binary",
        op: token.text as BinaryOp,
        left,
        right,
        at: token.at,
        type: "bool",
      });
    }
    return left;
  }
  function arithmetic(next: () => Node, ops: string[]): Node {
    let left = next();
    while (ops.some(isOp)) {
      const token = tokens[index++];
      const right = next();
      want(left, "num", `the left side of ${token.text}`);
      want(right, "num", `the right side of ${token.text}`);
      left = make({
        kind: "binary",
        op: token.text as BinaryOp,
        left,
        right,
        at: token.at,
        type: "num",
      });
    }
    return left;
  }
  const additive = (): Node => arithmetic(term, ["+", "-"]);
  const term = (): Node => arithmetic(unary, ["*", "/", "%"]);
  function unary(): Node {
    const token = peek();
    if (token?.type === "op" && (token.text === "-" || token.text === "+")) {
      index += 1;
      const operand = unary();
      want(operand, "num", `the value after ${token.text}`);
      return token.text === "-"
        ? make({ kind: "unary", op: "-", operand, at: token.at, type: "num" })
        : operand;
    }
    return postfix();
  }
  function postfix(): Node {
    let node = primary();
    while (peek()?.type === "[") {
      const at = expect("[", '"["').at;
      const offset = expect("number", "a whole number of bars");
      expect("]", '"]"');
      const n = Number(offset.text);
      if (!Number.isInteger(n) || n < 0 || n > MAX_WINDOW) {
        fail(
          `[n]: n must be a whole number from 0 to ${MAX_WINDOW}`,
          offset.at,
        );
      }
      want(node, "num", "the value before [n]");
      const length = make({
        kind: "number",
        value: n,
        at: offset.at,
        type: "num",
      });
      node = make({
        kind: "call",
        name: "shift",
        args: [node, length],
        at,
        type: "num",
      });
    }
    return node;
  }
  function primary(): Node {
    const token = peek();
    if (!token || token.type === "end")
      return fail("the formula ends too early", token?.at ?? end);
    if (token.type === "number") {
      index += 1;
      const value = Number(token.text);
      if (!Number.isFinite(value)) fail("bad number", token.at);
      return make({ kind: "number", value, at: token.at, type: "num" });
    }
    if (token.type === "string") {
      index += 1;
      if (!COLOR.test(token.text)) {
        fail('a colour is written "#rrggbb" or "#rrggbbaa"', token.at);
      }
      return make({
        kind: "color",
        value: cssColor(token.text),
        at: token.at,
        type: "color",
      });
    }
    if (token.type === "(") {
      index += 1;
      const inner = expression();
      expect(")", '")"');
      return inner;
    }
    if (token.type !== "name")
      return fail(`unexpected "${token.text}"`, token.at);
    index += 1;
    const name = token.text;
    if (name === "true" || name === "false") {
      return make({
        kind: "bool",
        value: name === "true",
        at: token.at,
        type: "bool",
      });
    }
    if (name === "na" && peek()?.type !== "(") {
      return make({ kind: "na", at: token.at, type: "na" });
    }
    if (peek()?.type === "(") return call(token);
    if (isField(name)) {
      return make({ kind: "field", name, at: token.at, type: "num" });
    }
    const type = types.get(name);
    if (type) return make({ kind: "ref", name, at: token.at, type });
    if (name === "input" || name === "plot" || name === "hline") {
      return fail(`${name}(...) is written on its own line`, token.at);
    }
    return fail(
      `unknown name "${name}" (use ${FIELDS.join(", ")}, a name set above, or a function)`,
      token.at,
    );
  }
  function call(token: Token): Node {
    const name = token.text;
    if (name === "input" || name === "plot" || name === "hline") {
      return fail(`${name}(...) is written on its own line`, token.at);
    }
    const spec = FUNCTIONS[name];
    if (!spec) return fail(`unknown function "${name}"`, token.at);
    expect("(", '"("');
    const args: Node[] = [];
    if (peek()?.type !== ")") {
      args.push(argument(name, spec, 0));
      while (peek()?.type === ",") {
        index += 1;
        args.push(argument(name, spec, args.length));
      }
    }
    expect(")", '")"');
    const least = spec.args.length - (spec.optional ?? 0);
    if (args.length < least || args.length > spec.args.length) {
      fail(
        `${name} takes ${least === spec.args.length ? least : `${least} or ${spec.args.length}`} arguments`,
        token.at,
      );
    }
    return make({
      kind: "call",
      name,
      args,
      at: token.at,
      type: spec.result ?? "num",
    });
  }
  function argument(
    name: string,
    spec: (typeof FUNCTIONS)[string],
    position: number,
  ): Node {
    const node = expression();
    if (position >= spec.args.length) return node; // counted after the call
    if (spec.args[position] === "s") {
      want(node, "num", `${name}'s argument ${position + 1}`);
      return node;
    }
    // A window length: a whole-number constant, or a number setting.
    const lower = spec.lower ?? 1;
    const problem = `${name}: the length must be a whole number from ${lower} to ${MAX_WINDOW}`;
    if (node.kind === "number") {
      if (
        !Number.isInteger(node.value) ||
        node.value < lower ||
        node.value > MAX_WINDOW
      ) {
        fail(problem, node.at);
      }
      return node;
    }
    const input =
      node.kind === "ref"
        ? inputs.find((item) => item.key === node.name)
        : undefined;
    if (!input || input.kind !== "number") {
      return fail(`${problem}, or a number setting`, node.at);
    }
    input.min = Math.max(input.min, lower);
    input.max = Math.min(input.max, MAX_WINDOW);
    input.length = true;
    input.step = 1;
    if (
      !Number.isInteger(input.default) ||
      input.default < input.min ||
      input.default > input.max
    ) {
      fail(
        `the setting "${input.key}" is a length here, so its default must be a whole number from ${input.min} to ${input.max}`,
        node.at,
      );
    }
    return node;
  }

  // --- arguments of input(), plot() and hline(): positional, then named ---
  function callArgs(
    keyword: string,
    names: string[],
  ): Map<string, { node: Node | null; token: Token }> {
    const found = new Map<string, { node: Node | null; token: Token }>();
    expect("(", '"("');
    let position = 0;
    while (peek()?.type !== ")") {
      if (found.size > 0 || position > 0) expect(",", '","');
      const token = peek();
      if (!token || token.type === "end")
        return fail(`${keyword}(...) is not closed`, token?.at ?? end);
      let key: string;
      if (token.type === "name" && peek(1)?.type === "=") {
        key = token.text;
        index += 2;
        if (!names.includes(key))
          fail(`${keyword} has no setting "${key}"`, token.at);
      } else {
        key = names[position];
        if (!key)
          fail(`${keyword} takes at most ${names.length} values`, token.at);
      }
      position += 1;
      if (found.has(key)) fail(`${key} is given twice`, token.at);
      const at = peek()!;
      if (at.type === "string") {
        index += 1;
        found.set(key, { node: null, token: at });
      } else {
        found.set(key, { node: expression(), token: at });
      }
    }
    expect(")", '")"');
    return found;
  }
  const constantNumber = (
    entry: { node: Node | null; token: Token } | undefined,
    what: string,
  ): number | undefined => {
    if (!entry) return undefined;
    const node = entry.node;
    const value =
      node?.kind === "number"
        ? node.value
        : node?.kind === "unary" &&
            node.op === "-" &&
            node.operand.kind === "number"
          ? -node.operand.value
          : undefined;
    if (value === undefined) fail(`${what} must be a number`, entry.token.at);
    return value;
  };
  const textOf = (
    entry: { node: Node | null; token: Token } | undefined,
    what: string,
  ): string | undefined => {
    if (!entry) return undefined;
    if (entry.node !== null)
      fail(`${what} must be a text in quotes`, entry.token.at);
    return entry.token.text.slice(0, 40);
  };

  function inputStatement(key: string, at: number): void {
    if (inputs.length >= MAX_INPUTS) fail(`at most ${MAX_INPUTS} settings`, at);
    const args = callArgs("input", [
      "defval",
      "title",
      "minval",
      "maxval",
      "step",
    ]);
    const first = args.get("defval");
    if (!first) return fail("input needs a default value", at);
    const label = textOf(args.get("title"), "the title") ?? key;
    const node = first.node;
    if (node?.kind === "bool") {
      inputs.push({
        key,
        label,
        kind: "bool",
        default: node.value ? 1 : 0,
        min: 0,
        max: 1,
        length: false,
      });
      types.set(key, "bool");
      return;
    }
    if (node?.kind === "field") {
      inputs.push({
        key,
        label,
        kind: "source",
        default: FIELDS.indexOf(node.name),
        min: 0,
        max: FIELDS.length - 1,
        length: false,
      });
      types.set(key, "num");
      return;
    }
    const value = constantNumber(first, "the default value");
    const min = constantNumber(args.get("minval"), "minval") ?? -1_000_000;
    const max = constantNumber(args.get("maxval"), "maxval") ?? 1_000_000;
    const step = constantNumber(args.get("step"), "step");
    if (value! < min || value! > max)
      fail("the default is outside minval..maxval", first.token.at);
    // `input(2.0)` is a decimal setting, `input(2)` a whole-number one (input.float/int).
    const written = tokens.find(
      (item) => item.at >= first.token.at && item.type === "number",
    );
    const whole =
      Number.isInteger(value!) &&
      !written?.text.includes(".") &&
      (step === undefined || Number.isInteger(step));
    inputs.push({
      key,
      label,
      kind: "number",
      default: value!,
      min,
      max,
      step: step ?? (whole ? 1 : undefined),
      length: false,
    });
    types.set(key, "num");
  }

  function plotStatement(at: number): void {
    if (plots.length >= MAX_PLOTS)
      fail(`at most ${MAX_PLOTS} plot(...) lines`, at);
    const args = callArgs("plot", [
      "series",
      "title",
      "color",
      "linewidth",
      "style",
    ]);
    const series = args.get("series");
    if (!series?.node) return fail("plot needs a value", at);
    want(series.node, "num", "plot's value");
    const color = args.get("color");
    if (color && color.node === null) {
      if (!COLOR.test(color.token.text))
        fail('a colour is written "#rrggbb" or "#rrggbbaa"', color.token.at);
    } else if (color?.node) {
      want(color.node, "color", "plot's colour");
    }
    const colorNode: Node | null = color
      ? (color.node ?? {
          kind: "color",
          value: cssColor(color.token.text),
          at: color.token.at,
          type: "color",
        })
      : null;
    const width = constantNumber(args.get("linewidth"), "linewidth") ?? 1;
    if (!Number.isInteger(width) || width < 1 || width > MAX_WIDTH) {
      fail(
        `linewidth must be a whole number from 1 to ${MAX_WIDTH}`,
        args.get("linewidth")!.token.at,
      );
    }
    const style = textOf(args.get("style"), "the style") ?? "line";
    if (style !== "line" && style !== "histogram") {
      fail('style is "line" or "histogram"', args.get("style")!.token.at);
    }
    plots.push({
      key: `plot${plots.length}`,
      title:
        textOf(args.get("title"), "the title") ?? `Plot ${plots.length + 1}`,
      value: series.node,
      color: colorNode,
      width,
      kind: style as "line" | "histogram",
    });
  }

  function hlineStatement(at: number): void {
    if (levels.length >= MAX_LEVELS)
      fail(`at most ${MAX_LEVELS} hline(...) lines`, at);
    const args = callArgs("hline", ["price", "title"]);
    textOf(args.get("title"), "the title");
    const value = constantNumber(args.get("price"), "hline's price");
    if (value === undefined) fail("hline needs a price", at);
    levels.push(value!);
  }

  // --- statements ---
  if (tokens.length === 0) fail("write a formula", 0);
  while (index < tokens.length) {
    const token = peek()!;
    if (token.type === "name" && peek(1)?.type === "=") {
      const name = token.text;
      if (RESERVED.has(name) || isField(name) || FUNCTIONS[name]) {
        fail(`"${name}" is already a word of the formula language`, token.at);
      }
      if (types.has(name)) fail(`"${name}" is already set above`, token.at);
      index += 2;
      if (
        peek()?.type === "name" &&
        peek()!.text === "input" &&
        peek(1)?.type === "("
      ) {
        index += 1;
        inputStatement(name, token.at);
      } else {
        const value = expression();
        bindings.push({ name, value });
        types.set(name, value.type === "na" ? "num" : value.type);
      }
    } else if (
      token.type === "name" &&
      (token.text === "plot" || token.text === "hline") &&
      peek(1)?.type === "("
    ) {
      index += 1;
      if (token.text === "plot") plotStatement(token.at);
      else hlineStatement(token.at);
    } else {
      bare.push(expression());
    }
    const next = peek();
    if (next && next.type !== "end") fail(`unexpected "${next.text}"`, next.at);
    index += 1;
  }

  if (plots.length > 0 || levels.length > 0) {
    if (bare.length > 0)
      fail("write plot(...) for each line to draw", bare[0].at);
    if (plots.length === 0) fail("add plot(...) to draw a line", end);
    return { inputs, bindings, plots, levels, single: false };
  }
  if (bare.length !== 1) {
    return fail(
      bare.length === 0
        ? "add the value to draw, or plot(...)"
        : "write plot(...) for each line to draw",
      bare[1]?.at ?? end,
    );
  }
  want(bare[0], "num", "the formula");
  return {
    inputs,
    bindings,
    plots: [
      {
        key: "value",
        title: "",
        value: bare[0],
        color: null,
        width: 1,
        kind: "line",
      },
    ],
    levels,
    single: true,
  };
}

/** A setting's value: the saved one, or the default. */
const paramOf = (
  input: FormulaInput,
  params: Record<string, number> | undefined,
): number => {
  const value = params?.[input.key];
  return typeof value === "number" &&
    Number.isFinite(value) &&
    value >= input.min &&
    value <= input.max
    ? input.length
      ? Math.round(value)
      : value
    : input.default;
};

/** How many earlier bars the formula reads at most, for warm-up. */
export function formulaLookback(
  program: FormulaProgram,
  params?: Record<string, number>,
): number {
  const own = new Map<string, number>();
  const lengthOf = (node: Node | undefined): number => {
    if (!node) return 0;
    if (node.kind === "number") return node.value;
    if (node.kind === "ref") {
      const input = program.inputs.find((item) => item.key === node.name);
      return input ? paramOf(input, params) : 0;
    }
    return 0;
  };
  const back = (node: Node): number => {
    switch (node.kind) {
      case "number":
      case "bool":
      case "na":
      case "color":
      case "field":
        return 0;
      case "ref":
        return own.get(node.name) ?? 0;
      case "unary":
        return back(node.operand);
      case "binary":
        return Math.max(back(node.left), back(node.right));
      case "ternary":
        return Math.max(back(node.test), back(node.then), back(node.otherwise));
      case "call": {
        const spec = FUNCTIONS[node.name];
        const inner = Math.max(
          0,
          ...node.args.filter((_, i) => spec.args[i] !== "n").map(back),
        );
        const lengths = node.args
          .filter((_, i) => spec.args[i] === "n")
          .map(lengthOf);
        let mine = 0;
        switch (node.name) {
          case "macd":
          case "macd_signal":
          case "macd_hist":
            mine = Math.max(lengths[0], lengths[1]) + lengths[2];
            break;
          case "change":
            mine = node.args.length > 1 ? lengths[0] : 1;
            break;
          case "mfi":
            mine = lengths[0] + 1;
            break;
          case "crossover":
          case "crossunder":
          case "tr":
            mine = 1;
            break;
          default:
            mine = lengths[0] ?? 0;
        }
        return inner + mine;
      }
    }
  };
  for (const binding of program.bindings)
    own.set(binding.name, back(binding.value));
  return Math.max(
    0,
    ...program.plots.flatMap((plot) => [
      back(plot.value),
      plot.color ? back(plot.color) : 0,
    ]),
  );
}

const finite = (value: number): number | null =>
  Number.isFinite(value) ? value : null;

type Column = Series | boolean[] | (string | null)[];

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

/** Per-bar plot colours of a formula's values (null: no colour on that bar). */
export const FORMULA_COLORS = new WeakMap<
  Record<string, Series>,
  Record<string, (string | null)[]>
>();

const TRANSPARENT = "rgba(0, 0, 0, 0)";

/** Evaluates a parsed formula over bars: one series per plot, null when missing. */
export function evaluateFormula(
  program: FormulaProgram,
  bars: OhlcvBar[],
  params?: Record<string, number>,
  context?: IndicatorContext,
): {
  values: Record<string, Series>;
  colors: Record<string, (string | null)[]>;
} {
  const count = bars.length;
  const env = new Map<string, Column>();
  const fieldOf = (name: Field): Series =>
    bars.map((bar) => {
      switch (name) {
        case "hl2":
          return (bar.high + bar.low) / 2;
        case "hlc3":
          return (bar.high + bar.low + bar.close) / 3;
        case "ohlc4":
          return (bar.open + bar.high + bar.low + bar.close) / 4;
        default:
          return bar[name];
      }
    });
  for (const input of program.inputs) {
    const value = paramOf(input, params);
    env.set(
      input.key,
      input.kind === "bool"
        ? bars.map(() => value === 1)
        : input.kind === "source"
          ? fieldOf(FIELDS[Math.round(value)] ?? "close")
          : bars.map(() => value),
    );
  }
  const nulls = (): Series => Array.from({ length: count }, () => null);
  const num = (node: Node): Series => run(node) as Series;
  const bool = (node: Node): boolean[] => run(node) as boolean[];
  const lengthOf = (node: Node | undefined, fallback = 1): number => {
    if (!node) return fallback;
    if (node.kind === "number") return node.value;
    if (node.kind === "ref") {
      const input = program.inputs.find((item) => item.key === node.name);
      if (input) return paramOf(input, params);
    }
    return fallback;
  };
  const run = (node: Node): Column => {
    switch (node.kind) {
      case "number":
        return bars.map(() => node.value);
      case "bool":
        return bars.map(() => node.value);
      case "na":
        return nulls();
      case "color":
        return bars.map(() => node.value);
      case "field":
        return fieldOf(node.name);
      case "ref":
        return env.get(node.name) ?? nulls();
      case "unary":
        return node.op === "not"
          ? bool(node.operand).map((value) => !value)
          : num(node.operand).map((value) => (value === null ? null : -value));
      case "binary": {
        if (node.op === "and" || node.op === "or") {
          const left = bool(node.left);
          const right = bool(node.right);
          return left.map((x, i) =>
            node.op === "and" ? x && right[i] : x || right[i],
          );
        }
        if (node.op === "==" || node.op === "!=") {
          const left = run(node.left);
          const right = run(node.right);
          return left.map((x, i) => {
            const y = right[i];
            if (x === null || y === null) return false; // as Pine: na compares false
            return node.op === "==" ? x === y : x !== y;
          });
        }
        const left = num(node.left);
        const right = num(node.right);
        switch (node.op) {
          case "<":
          case ">":
          case "<=":
          case ">=":
            return left.map((x, i) => {
              const y = right[i];
              if (x === null || y === null) return false;
              return node.op === "<"
                ? x < y
                : node.op === ">"
                  ? x > y
                  : node.op === "<="
                    ? x <= y
                    : x >= y;
            });
          case "+":
            return map2(left, right, (x, y) => x + y);
          case "-":
            return map2(left, right, (x, y) => x - y);
          case "*":
            return map2(left, right, (x, y) => x * y);
          case "/":
            return map2(left, right, (x, y) => (y === 0 ? NaN : x / y));
          case "%":
            return map2(left, right, (x, y) => (y === 0 ? NaN : x % y));
        }
        return nulls();
      }
      case "ternary": {
        const test = bool(node.test);
        const then = run(node.then);
        const otherwise = run(node.otherwise);
        return test.map((value, i) =>
          value ? then[i] : otherwise[i],
        ) as Column;
      }
      case "call":
        return callOf(node);
    }
  };
  const callOf = (node: Extract<Node, { kind: "call" }>): Column => {
    const [first, second, third, fourth] = node.args;
    switch (node.name) {
      case "sma":
        return sma(num(first), lengthOf(second));
      case "ema":
        return ema(num(first), lengthOf(second));
      case "wma":
        return wma(num(first), lengthOf(second));
      case "rma":
        return rma(num(first), lengthOf(second));
      case "stdev":
        return stdev(num(first), lengthOf(second));
      case "highest":
        return highest(num(first), lengthOf(second));
      case "lowest":
        return lowest(num(first), lengthOf(second));
      case "sum": {
        const n = lengthOf(second);
        return sma(num(first), n).map((value) =>
          value === null ? null : value * n,
        );
      }
      case "change":
        return change(num(first), lengthOf(second));
      case "roc": {
        const source = num(first);
        const before = shift(source, lengthOf(second));
        return map2(source, before, (x, y) =>
          y === 0 ? NaN : ((x - y) / y) * 100,
        );
      }
      case "shift":
        return shift(num(first), lengthOf(second, 0));
      case "rsi":
        return rsi(num(first), lengthOf(second));
      case "atr":
        return rma(trueRange(bars), lengthOf(first));
      case "tr":
        return trueRange(bars);
      case "vwap":
        return INDICATOR_BY_ID.get("vwap")!.compute(bars, {}, context).value;
      case "obv":
        return INDICATOR_BY_ID.get("obv")!.compute(bars, {}, context).value;
      case "cum": {
        let total = 0;
        return num(first).map((value) => (total += value ?? 0)); // na adds 0, as ta.cum
      }
      case "macd":
      case "macd_signal":
      case "macd_hist": {
        const source = num(first);
        const line = map2(
          ema(source, lengthOf(second)),
          ema(source, lengthOf(third)),
          (x, y) => x - y,
        );
        if (node.name === "macd") return line;
        const signal = ema(line, lengthOf(fourth));
        return node.name === "macd_signal"
          ? signal
          : map2(line, signal, (x, y) => x - y);
      }
      case "bb_upper":
      case "bb_lower": {
        const source = num(first);
        const n = lengthOf(second);
        const mult = num(third);
        const basis = sma(source, n);
        const deviation = stdev(source, n);
        const sign = node.name === "bb_upper" ? 1 : -1;
        return basis.map((mean, i) => {
          const dev = deviation[i];
          const k = mult[i];
          return mean === null || dev === null || k === null
            ? null
            : finite(mean + sign * k * dev);
        });
      }
      case "stoch": {
        const source = num(first);
        const n = lengthOf(fourth);
        const hi = highest(num(second), n);
        const lo = lowest(num(third), n);
        return source.map((value, i) => {
          const h = hi[i];
          const l = lo[i];
          return value === null || h === null || l === null || h === l
            ? null
            : (100 * (value - l)) / (h - l);
        });
      }
      case "cci": {
        const source = num(first);
        const n = lengthOf(second);
        const mean = sma(source, n);
        return source.map((value, i) => {
          const m = mean[i];
          if (value === null || m === null) return null;
          let dev = 0;
          for (let j = i - n + 1; j <= i; j += 1)
            dev += Math.abs((source[j] ?? NaN) - m);
          dev /= n;
          return dev === 0 || !Number.isFinite(dev)
            ? null
            : (value - m) / (0.015 * dev);
        });
      }
      case "mfi": {
        // As ta.mfi: money flow is price × volume, up or down by the price's change.
        const source = num(first);
        const n = lengthOf(second);
        const up: number[] = [];
        const down: number[] = [];
        return source.map((now, i) => {
          const before = i > 0 ? source[i - 1] : null;
          const flow = now === null ? 0 : now * bars[i].volume;
          up[i] = now !== null && before !== null && now > before ? flow : 0;
          down[i] = now !== null && before !== null && now < before ? flow : 0;
          if (i < n) return null;
          let u = 0;
          let d = 0;
          for (let j = i - n + 1; j <= i; j += 1) {
            u += up[j];
            d += down[j];
          }
          return d === 0 ? 100 : 100 - 100 / (1 + u / d);
        });
      }
      case "crossover":
      case "crossunder": {
        const a = num(first);
        const b = num(second);
        return a.map((x, i) => {
          const y = b[i];
          const px = i > 0 ? a[i - 1] : null;
          const py = i > 0 ? b[i - 1] : null;
          if (x === null || y === null || px === null || py === null)
            return false;
          return node.name === "crossover"
            ? x > y && px <= py
            : x < y && px >= py;
        });
      }
      case "nz": {
        const fallback = second ? num(second) : bars.map(() => 0);
        return num(first).map((value, i) => value ?? fallback[i]);
      }
      case "na":
        return num(first).map((value) => value === null);
      case "abs":
        return num(first).map((value) =>
          value === null ? null : Math.abs(value),
        );
      case "sqrt":
        return num(first).map((value) =>
          value === null || value < 0 ? null : Math.sqrt(value),
        );
      case "log":
        return num(first).map((value) =>
          value === null || value <= 0 ? null : Math.log(value),
        );
      case "max":
        return map2(num(first), num(second), Math.max);
      case "min":
        return map2(num(first), num(second), Math.min);
    }
    return nulls();
  };
  for (const binding of program.bindings)
    env.set(binding.name, run(binding.value));
  const values: Record<string, Series> = {};
  const colors: Record<string, (string | null)[]> = {};
  for (const plot of program.plots) {
    values[plot.key] = num(plot.value);
    if (plot.color && plot.color.kind !== "color") {
      colors[plot.key] = (run(plot.color) as (string | null)[]).map(
        (color) => color ?? TRANSPARENT, // na hides the line on that bar, as in Pine
      );
    }
  }
  return { values, colors };
}

/** The first constant colour in a plot's colour, for its legend and style. */
function firstColor(node: Node | null): string | null {
  if (!node) return null;
  if (node.kind === "color") return node.value;
  if (node.kind === "ternary")
    return firstColor(node.then) ?? firstColor(node.otherwise);
  return null;
}

/** Line widths other than 1, for a new instance's style (like a Pine script's). */
export function formulaWidths(formula: string): Record<string, number> {
  try {
    const program = parseFormula(formula);
    return Object.fromEntries(
      program.plots
        .filter((plot) => plot.width !== 1)
        .map((plot) => [plot.key, plot.width]),
    );
  } catch {
    return {};
  }
}

/** A formula as an indicator definition, so the chart draws it like any other. */
export function formulaDefinition(
  formula: string,
  label: string,
  pane: "price" | "separate",
): IndicatorDefinition {
  let program: FormulaProgram | null = null;
  try {
    program = parseFormula(formula);
  } catch {
    program = null; // drawn as missing; the settings show why
  }
  const inputs: IndicatorInput[] = (program?.inputs ?? []).map((input) => ({
    key: input.key,
    label: { en: input.label, ko: input.label },
    default: input.default,
    min: input.min,
    max: input.max,
    ...(input.step !== undefined ? { step: input.step } : {}),
    lookback: input.length,
    ...(input.kind === "bool"
      ? {
          options: [
            { value: 0, label: "false" },
            { value: 1, label: "true" },
          ],
        }
      : input.kind === "source"
        ? { options: FIELDS.map((field, value) => ({ value, label: field })) }
        : {}),
  }));
  const outputs: IndicatorOutput[] =
    program && !program.single
      ? program.plots.map((plot, i) => ({
          key: plot.key,
          label: plot.title,
          color: firstColor(plot.color) ?? PALETTE[i % PALETTE.length],
          ...(plot.kind === "histogram" ? { kind: "histogram" as const } : {}),
        }))
      : [{ key: "value", label, color: DEFAULT_COLOR }];
  return {
    id: "formula",
    name: { en: label, ko: label },
    short: label,
    category: "custom",
    pane,
    inputs,
    outputs,
    ...(program && program.levels.length > 0 ? { levels: program.levels } : {}),
    compute: (bars, params, context) => {
      if (!program) {
        return Object.fromEntries(
          outputs.map((output) => [output.key, bars.map(() => null)]),
        );
      }
      const result = evaluateFormula(program, bars, params, context);
      if (Object.keys(result.colors).length > 0)
        FORMULA_COLORS.set(result.values, result.colors);
      return result.values;
    },
  };
}
