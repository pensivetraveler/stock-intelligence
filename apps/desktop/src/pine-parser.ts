/**
 * Pine Script (v5) parser for SIW's Pine indicators (ADR 0040): a subset of the
 * language TradingView documents, read into a tree the interpreter in pine.ts runs.
 * Nothing here evaluates code; parsing only.
 */

export const MAX_PINE_LENGTH = 50_000;
const MAX_NODES = 30_000;

export class PineError extends Error {
  constructor(
    message: string,
    readonly line: number,
    readonly column: number,
  ) {
    super(message);
    this.name = "PineError";
  }
}

// --- tokens -------------------------------------------------------------------------

type TokenKind =
  "number" | "string" | "name" | "op" | "newline" | "indent" | "dedent" | "end";

interface Token {
  kind: TokenKind;
  text: string;
  line: number;
  column: number;
}

const KEYWORDS = new Set([
  "var",
  "varip",
  "if",
  "else",
  "for",
  "to",
  "by",
  "while",
  "switch",
  "break",
  "continue",
  "and",
  "or",
  "not",
  "true",
  "false",
  "na",
]);
/** Type words that may start a declaration (`float x = …`); they change nothing. */
const TYPE_WORDS = new Set([
  "int",
  "float",
  "bool",
  "color",
  "string",
  "line",
  "label",
  "box",
  "table",
  "linefill",
  "chart.point",
]);
/** Qualifiers before a type (`simple int`); they change nothing either. */
const QUALIFIERS = new Set(["series", "simple", "const"]);
/** Generic collection types: `array<float>`. */
const GENERICS = new Set(["array", "matrix", "map"]);
/** Script kinds and statements SIW does not run, with the reason shown. */
const UNSUPPORTED_STATEMENTS: Record<string, string> = {
  import: "libraries (import) are not supported",
  export: "libraries (export) are not supported",
  type: "user-defined types (type) are not supported",
  enum: "enums are not supported",
  library: "libraries are not supported",
};
const OPERATORS = [
  ":=",
  "+=",
  "-=",
  "*=",
  "/=",
  "%=",
  "==",
  "!=",
  "<=",
  ">=",
  "=>",
  "+",
  "-",
  "*",
  "/",
  "%",
  "<",
  ">",
  "=",
  "?",
  ":",
  "(",
  ")",
  "[",
  "]",
  ",",
];
/** A line ending in one of these continues on the next line. */
const CONTINUES = new Set([
  "+",
  "-",
  "*",
  "/",
  "%",
  "<",
  ">",
  "==",
  "!=",
  "<=",
  ">=",
  "and",
  "or",
  "?",
  ":",
  ",",
  "=",
  ":=",
  "(",
  "[",
]);

function lineTokens(text: string, line: number): Token[] {
  const tokens: Token[] = [];
  let i = 0;
  while (i < text.length) {
    const char = text[i];
    if (char === " " || char === "\t") {
      i += 1;
      continue;
    }
    if (text.startsWith("//", i)) break;
    const column = i + 1;
    if (char === '"' || char === "'") {
      let j = i + 1;
      let value = "";
      while (j < text.length && text[j] !== char) {
        if (text[j] === "\\" && j + 1 < text.length) {
          value += text[j + 1];
          j += 2;
        } else {
          value += text[j];
          j += 1;
        }
      }
      if (j >= text.length)
        throw new PineError("unclosed string", line, column);
      tokens.push({ kind: "string", text: value, line, column });
      i = j + 1;
      continue;
    }
    const number = /^(\d+\.?\d*|\.\d+)(e[+-]?\d+)?/i.exec(text.slice(i));
    if (number) {
      tokens.push({ kind: "number", text: number[0], line, column });
      i += number[0].length;
      continue;
    }
    if (char === "#") {
      const hex = /^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?/.exec(text.slice(i));
      if (!hex) throw new PineError("bad colour literal", line, column);
      tokens.push({ kind: "string", text: hex[0], line, column });
      i += hex[0].length;
      continue;
    }
    const name = /^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*/.exec(
      text.slice(i),
    );
    if (name) {
      tokens.push({ kind: "name", text: name[0], line, column });
      i += name[0].length;
      continue;
    }
    const op = OPERATORS.find((item) => text.startsWith(item, i));
    if (!op) throw new PineError(`unexpected "${char}"`, line, column);
    tokens.push({ kind: "op", text: op, line, column });
    i += op.length;
  }
  return tokens;
}

/** Tokens with Python-like indentation: blocks are lines indented by 4 spaces or a tab. */
export function tokenize(source: string): Token[] {
  if (source.length > MAX_PINE_LENGTH) {
    throw new PineError(
      `the script is longer than ${MAX_PINE_LENGTH} characters`,
      1,
      1,
    );
  }
  const out: Token[] = [];
  const indents = [0];
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  let pending: Token[] | null = null; // a logical line still continuing
  let depth = 0; // open ( and [
  lines.forEach((raw, index) => {
    const line = index + 1;
    const tokens = lineTokens(raw, line);
    if (tokens.length === 0) return;
    const spaces = /^[ \t]*/.exec(raw)![0].replace(/\t/g, "    ").length;
    if (pending) {
      pending.push(...tokens);
    } else if (spaces % 4 !== 0 && out[out.length - 1]?.kind === "newline") {
      // A line indented by other than a multiple of 4 continues the line before it.
      out.pop();
      pending = [...tokens];
    } else {
      const level = Math.floor(spaces / 4);
      if (level > indents.length - 1 + 1) {
        throw new PineError("unexpected indentation", line, 1);
      }
      if (level > indents.length - 1) {
        indents.push(level);
        out.push({ kind: "indent", text: "", line, column: 1 });
      }
      while (level < indents.length - 1) {
        indents.pop();
        out.push({ kind: "dedent", text: "", line, column: 1 });
      }
      pending = [...tokens];
    }
    for (const token of tokens) {
      if (token.kind !== "op") continue;
      if (token.text === "(" || token.text === "[") depth += 1;
      if (token.text === ")" || token.text === "]") depth -= 1;
    }
    const last = tokens[tokens.length - 1];
    const continues =
      depth > 0 ||
      ((last.kind === "op" || last.kind === "name") &&
        CONTINUES.has(last.text));
    if (!continues) {
      out.push(...pending, {
        kind: "newline",
        text: "",
        line,
        column: raw.length + 1,
      });
      pending = null;
    }
  });
  if (pending) {
    const tail = pending as Token[];
    const last = tail[tail.length - 1];
    throw new PineError(
      "the script ends in the middle of a line",
      last.line,
      last.column,
    );
  }
  const lastLine = lines.length;
  while (indents.length > 1) {
    indents.pop();
    out.push({ kind: "dedent", text: "", line: lastLine, column: 1 });
  }
  out.push({ kind: "end", text: "", line: lastLine + 1, column: 1 });
  return out;
}

// --- tree ---------------------------------------------------------------------------

export interface Position {
  id: number;
  line: number;
  column: number;
}

export type Expr = Position &
  (
    | { kind: "number"; value: number }
    | { kind: "string"; value: string }
    | { kind: "bool"; value: boolean }
    | { kind: "na" }
    | { kind: "name"; name: string }
    | {
        kind: "call";
        name: string;
        args: Expr[];
        named: Record<string, Expr>;
      }
    | { kind: "index"; target: Expr; offset: Expr }
    | { kind: "unary"; op: "-" | "+" | "not"; operand: Expr }
    | { kind: "binary"; op: string; left: Expr; right: Expr }
    | { kind: "ternary"; test: Expr; then: Expr; otherwise: Expr }
    | { kind: "tuple"; items: Expr[] }
    /** `x = if …` and `x = switch`-like blocks: the value of the branch that ran. */
    | { kind: "block"; statement: Stmt }
  );

export type Stmt = Position &
  (
    | { kind: "declare"; names: string[]; persistent: boolean; value: Expr }
    | { kind: "assign"; name: string; op: string; value: Expr }
    | { kind: "if"; test: Expr; body: Stmt[]; otherwise: Stmt[] }
    | {
        kind: "for";
        name: string;
        from: Expr;
        to: Expr;
        step: Expr | null;
        body: Stmt[];
      }
    | { kind: "forin"; names: string[]; source: Expr; body: Stmt[] }
    | { kind: "while"; test: Expr; body: Stmt[] }
    | {
        kind: "switch";
        subject: Expr | null;
        cases: { test: Expr | null; body: Stmt[] }[];
      }
    | { kind: "break" }
    | { kind: "continue" }
    | {
        kind: "function";
        name: string;
        params: string[];
        defaults: (Expr | null)[];
        method: boolean;
        body: Stmt[];
      }
    | { kind: "expr"; value: Expr }
  );

// --- parser -------------------------------------------------------------------------

const PRECEDENCE: Record<string, number> = {
  or: 1,
  and: 2,
  "==": 3,
  "!=": 3,
  "<": 4,
  ">": 4,
  "<=": 4,
  ">=": 4,
  "+": 5,
  "-": 5,
  "*": 6,
  "/": 6,
  "%": 6,
};

class Parser {
  private at = 0;
  private nodes = 0;

  constructor(private readonly tokens: Token[]) {}

  private peek(offset = 0): Token {
    return this.tokens[Math.min(this.at + offset, this.tokens.length - 1)];
  }

  private next(): Token {
    const token = this.peek();
    this.at += 1;
    return token;
  }

  private is(text: string, offset = 0): boolean {
    const token = this.peek(offset);
    return (
      (token.kind === "op" || token.kind === "name") && token.text === text
    );
  }

  private expect(text: string): Token {
    const token = this.next();
    if ((token.kind === "op" || token.kind === "name") && token.text === text) {
      return token;
    }
    throw this.error(`expected "${text}"`, token);
  }

  private error(message: string, token = this.peek()): PineError {
    return new PineError(message, token.line, token.column);
  }

  private node(token: Token): Position {
    this.nodes += 1;
    if (this.nodes > MAX_NODES) {
      throw this.error(`the script has more than ${MAX_NODES} parts`, token);
    }
    return { id: this.nodes, line: token.line, column: token.column };
  }

  program(): Stmt[] {
    const body: Stmt[] = [];
    while (this.peek().kind !== "end") {
      if (this.peek().kind === "newline") {
        this.next();
        continue;
      }
      body.push(this.statement());
    }
    return body;
  }

  private block(): Stmt[] {
    if (this.peek().kind !== "newline") throw this.error("expected a new line");
    this.next();
    if (this.peek().kind !== "indent") {
      throw this.error("expected an indented block");
    }
    this.next();
    const body: Stmt[] = [];
    while (this.peek().kind !== "dedent" && this.peek().kind !== "end") {
      if (this.peek().kind === "newline") {
        this.next();
        continue;
      }
      body.push(this.statement());
    }
    if (this.peek().kind === "dedent") this.next();
    return body;
  }

  private endOfLine(): void {
    const token = this.peek();
    if (token.kind === "newline") {
      this.next();
      return;
    }
    if (token.kind === "dedent" || token.kind === "end") return;
    throw this.error(`unexpected "${token.text}"`);
  }

  private statement(): Stmt {
    const token = this.peek();
    if (token.kind === "name") {
      const unsupported = UNSUPPORTED_STATEMENTS[token.text];
      if (unsupported && this.peek(1).kind === "name") {
        throw this.error(unsupported, token);
      }
      if (
        token.text === "method" &&
        this.peek(1).kind === "name" &&
        this.is("(", 2)
      ) {
        this.next();
        return this.functionDef(true);
      }
      switch (token.text) {
        case "if":
          return this.ifStatement();
        case "switch":
          return this.switchStatement();
        case "for":
          return this.forStatement();
        case "while": {
          this.next();
          const test = this.expression();
          return {
            ...this.node(token),
            kind: "while",
            test,
            body: this.block(),
          };
        }
        case "break":
        case "continue":
          this.next();
          this.endOfLine();
          return { ...this.node(token), kind: token.text };
        case "var":
        case "varip": {
          this.next();
          const typed = this.typeLength(0);
          if (typed > 0 && this.peek(typed).kind === "name") this.at += typed;
          const name = this.name();
          this.expect("=");
          const value = this.value();
          return {
            ...this.node(token),
            kind: "declare",
            names: [name],
            persistent: true,
            value,
          };
        }
      }
      // Function definition: name(params) =>
      if (this.is("(", 1) && this.isFunctionHead()) return this.functionDef();
      // Typed declaration: float x = …, float[] xs = …, array<line> ls = …
      const typed = this.typeLength(0);
      if (
        typed > 0 &&
        this.peek(typed).kind === "name" &&
        this.is("=", typed + 1)
      ) {
        this.at += typed;
      }
      const head = this.peek();
      if (head.kind === "name" && !KEYWORDS.has(head.text)) {
        if (this.is("=", 1)) {
          this.next();
          this.next();
          const value = this.value();
          return {
            ...this.node(head),
            kind: "declare",
            names: [head.text],
            persistent: false,
            value,
          };
        }
        const op = this.peek(1).text;
        if ([":=", "+=", "-=", "*=", "/=", "%="].includes(op)) {
          this.next();
          this.next();
          const value = this.value();
          return {
            ...this.node(head),
            kind: "assign",
            name: head.text,
            op,
            value,
          };
        }
      }
    }
    // Tuple declaration: [a, b] = f()
    if (
      token.kind === "op" &&
      token.text === "[" &&
      this.isTupleDeclaration()
    ) {
      this.next();
      const names = [this.name()];
      while (this.is(",")) {
        this.next();
        names.push(this.name());
      }
      this.expect("]");
      this.expect("=");
      const value = this.expression();
      this.endOfLine();
      return {
        ...this.node(token),
        kind: "declare",
        names,
        persistent: false,
        value,
      };
    }
    const value = this.expression();
    this.endOfLine();
    return { ...this.node(token), kind: "expr", value };
  }

  /** How many tokens a type takes at `offset` (`simple int`, `float[]`, `array<box>`), or 0. */
  private typeLength(offset: number): number {
    const token = this.peek(offset);
    if (token.kind !== "name") return 0;
    if (QUALIFIERS.has(token.text)) {
      const inner = this.typeLength(offset + 1);
      return inner > 0 ? inner + 1 : 0;
    }
    if (GENERICS.has(token.text) && this.is("<", offset + 1)) {
      let k = offset + 2;
      for (;;) {
        const inner = this.typeLength(k);
        if (inner === 0) return 0;
        k += inner;
        if (this.is(",", k)) {
          k += 1;
          continue;
        }
        return this.is(">", k) ? k + 1 - offset : 0;
      }
    }
    if (!TYPE_WORDS.has(token.text)) return 0;
    return this.is("[", offset + 1) && this.is("]", offset + 2) ? 3 : 1;
  }

  private isFunctionHead(): boolean {
    let depth = 0;
    for (let k = 1; this.at + k < this.tokens.length; k += 1) {
      const token = this.peek(k);
      if (token.kind === "newline" || token.kind === "end") return false;
      if (token.text === "(") depth += 1;
      if (token.text === ")") {
        depth -= 1;
        if (depth === 0) return this.is("=>", k + 1);
      }
    }
    return false;
  }

  private isTupleDeclaration(): boolean {
    for (let k = 1; this.at + k < this.tokens.length; k += 1) {
      const token = this.peek(k);
      if (token.kind === "newline" || token.kind === "end") return false;
      if (token.text === "]") return this.is("=", k + 1);
    }
    return false;
  }

  private functionDef(method = false): Stmt {
    const token = this.next();
    const params: string[] = [];
    const defaults: (Expr | null)[] = [];
    this.expect("(");
    while (!this.is(")")) {
      const typed = this.typeLength(0);
      if (typed > 0 && this.peek(typed).kind === "name") this.at += typed;
      params.push(this.name());
      if (this.is("=")) {
        this.next();
        defaults.push(this.expression());
      } else {
        defaults.push(null);
      }
      if (!this.is(")")) this.expect(",");
    }
    this.expect(")");
    this.expect("=>");
    let body: Stmt[];
    if (this.peek().kind === "newline") {
      body = this.block();
    } else {
      const start = this.peek();
      const value = this.expression();
      this.endOfLine();
      body = [{ ...this.node(start), kind: "expr", value }];
    }
    return {
      ...this.node(token),
      kind: "function",
      name: token.text,
      params,
      defaults,
      method,
      body,
    };
  }

  private ifStatement(): Stmt {
    const token = this.expect("if");
    const test = this.expression();
    const body = this.block();
    let otherwise: Stmt[] = [];
    if (this.is("else")) {
      const elseToken = this.next();
      otherwise = this.is("if") ? [this.ifStatement()] : this.block();
      void elseToken;
    }
    return { ...this.node(token), kind: "if", test, body, otherwise };
  }

  /** `switch [subject]` with `test => …` arms and a `=> …` default. */
  private switchStatement(): Stmt {
    const token = this.expect("switch");
    const subject = this.peek().kind === "newline" ? null : this.expression();
    if (this.peek().kind !== "newline") throw this.error("expected a new line");
    this.next();
    if (this.peek().kind !== "indent") {
      throw this.error("expected the switch's indented cases");
    }
    this.next();
    const cases: { test: Expr | null; body: Stmt[] }[] = [];
    while (this.peek().kind !== "dedent" && this.peek().kind !== "end") {
      if (this.peek().kind === "newline") {
        this.next();
        continue;
      }
      const test = this.is("=>") ? null : this.expression();
      this.expect("=>");
      let body: Stmt[];
      if (this.peek().kind === "newline") {
        body = this.block();
      } else {
        const start = this.peek();
        body = [{ ...this.node(start), kind: "expr", value: this.value() }];
      }
      cases.push({ test, body });
    }
    if (this.peek().kind === "dedent") this.next();
    return { ...this.node(token), kind: "switch", subject, cases };
  }

  private forStatement(): Stmt {
    const token = this.expect("for");
    if (this.is("[")) {
      // for [index, item] in items
      this.next();
      const names = [this.name()];
      this.expect(",");
      names.push(this.name());
      this.expect("]");
      this.expect("in");
      const source = this.expression();
      return {
        ...this.node(token),
        kind: "forin",
        names,
        source,
        body: this.block(),
      };
    }
    const name = this.name();
    if (this.is("in")) {
      this.next();
      const source = this.expression();
      return {
        ...this.node(token),
        kind: "forin",
        names: [name],
        source,
        body: this.block(),
      };
    }
    this.expect("=");
    const from = this.expression();
    this.expect("to");
    const to = this.expression();
    let step: Expr | null = null;
    if (this.is("by")) {
      this.next();
      step = this.expression();
    }
    return {
      ...this.node(token),
      kind: "for",
      name,
      from,
      to,
      step,
      body: this.block(),
    };
  }

  private name(): string {
    const token = this.next();
    if (token.kind !== "name" || KEYWORDS.has(token.text)) {
      throw this.error("expected a name", token);
    }
    return token.text;
  }

  /** The right side of `=` or `:=`: an expression, or an `if` whose branch gives it. */
  private value(): Expr {
    if (this.is("if") || this.is("switch")) {
      const token = this.peek();
      const statement = this.is("if")
        ? this.ifStatement()
        : this.switchStatement();
      return { ...this.node(token), kind: "block", statement };
    }
    const value = this.expression();
    this.endOfLine();
    return value;
  }

  expression(): Expr {
    const test = this.binary(1);
    if (!this.is("?")) return test;
    const token = this.next();
    const then = this.expression();
    this.expect(":");
    const otherwise = this.expression();
    return { ...this.node(token), kind: "ternary", test, then, otherwise };
  }

  private binary(level: number): Expr {
    let left = this.unary();
    for (;;) {
      const token = this.peek();
      const precedence =
        token.kind === "op" || token.kind === "name"
          ? PRECEDENCE[token.text]
          : undefined;
      if (precedence === undefined || precedence < level) return left;
      this.next();
      const right = this.binary(precedence + 1);
      left = {
        ...this.node(token),
        kind: "binary",
        op: token.text,
        left,
        right,
      };
    }
  }

  private unary(): Expr {
    const token = this.peek();
    if (this.is("-") || this.is("+") || this.is("not")) {
      this.next();
      const operand = this.unary();
      return {
        ...this.node(token),
        kind: "unary",
        op: token.text as "-" | "+" | "not",
        operand,
      };
    }
    return this.postfix(this.primary());
  }

  private postfix(expr: Expr): Expr {
    let current = expr;
    while (this.is("[")) {
      const token = this.next();
      const offset = this.expression();
      this.expect("]");
      current = { ...this.node(token), kind: "index", target: current, offset };
    }
    return current;
  }

  private primary(): Expr {
    const token = this.next();
    switch (token.kind) {
      case "number":
        return {
          ...this.node(token),
          kind: "number",
          value: Number(token.text),
        };
      case "string":
        return { ...this.node(token), kind: "string", value: token.text };
      case "name": {
        if (token.text === "true" || token.text === "false") {
          return {
            ...this.node(token),
            kind: "bool",
            value: token.text === "true",
          };
        }
        if (token.text === "na" && !this.is("(")) {
          return { ...this.node(token), kind: "na" };
        }
        if (KEYWORDS.has(token.text) && token.text !== "na") {
          throw this.error(`unexpected "${token.text}"`, token);
        }
        if (
          (token.text === "array.new" || token.text === "matrix.new") &&
          this.is("<")
        ) {
          // array.new<float>(…) is array.new_float(…); the type changes nothing else.
          this.next();
          const type = this.next();
          while (!this.is(">")) this.next();
          this.next();
          return this.call({
            ...token,
            text: `${token.text}_${type.text.replace("chart.point", "point")}`,
          });
        }
        if (this.is("(")) return this.call(token);
        return { ...this.node(token), kind: "name", name: token.text };
      }
      case "op":
        if (token.text === "(") {
          const inner = this.expression();
          this.expect(")");
          return inner;
        }
        if (token.text === "[") {
          const items: Expr[] = [];
          while (!this.is("]")) {
            items.push(this.expression());
            if (!this.is("]")) this.expect(",");
          }
          this.expect("]");
          return { ...this.node(token), kind: "tuple", items };
        }
        break;
      default:
        break;
    }
    throw this.error(
      token.kind === "end" || token.kind === "newline"
        ? "the line ends too early"
        : `unexpected "${token.text}"`,
      token,
    );
  }

  private call(token: Token): Expr {
    this.expect("(");
    const args: Expr[] = [];
    const named: Record<string, Expr> = {};
    while (!this.is(")")) {
      if (this.peek().kind === "name" && this.is("=", 1)) {
        const key = this.next().text;
        this.next();
        named[key] = this.expression();
      } else {
        if (Object.keys(named).length > 0) {
          throw this.error("a positional argument after a named one");
        }
        args.push(this.expression());
      }
      if (!this.is(")")) this.expect(",");
    }
    this.expect(")");
    return { ...this.node(token), kind: "call", name: token.text, args, named };
  }
}

/** The script as statements, or a PineError with the line and column. */
export function parsePine(source: string): Stmt[] {
  return new Parser(tokenize(source)).program();
}
