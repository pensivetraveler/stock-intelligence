/**
 * Values the Pine interpreter (ADR 0040) passes around besides numbers: arrays,
 * references to drawings, chart points, and the functions on arrays and strings.
 * Nothing here reads bars, so nothing here can see the future.
 */

/** A Pine array (`array.new_float()`); a reference, like on TradingView. */
export class PineArray {
  constructor(public items: Value[]) {}
}

export type RefKind =
  "line" | "label" | "box" | "table" | "linefill" | "plot" | "hline";

/** A drawing, plot or hline, as returned by `line.new()`, `plot()` or `hline()`. */
export class PineRef {
  constructor(
    readonly kind: RefKind,
    readonly id: number,
  ) {}
}

/** `chart.point.*`: a place on the chart by bar index and/or time, and price. */
export class PinePoint {
  constructor(
    public index: number | null,
    public time: number | null,
    public price: number | null,
  ) {}
}

/** A tuple (`[a, b]`) is a plain JS array; a Pine array is a PineArray. */
export type Value =
  number | boolean | string | null | Value[] | PineArray | PineRef | PinePoint;

/** Arrays may not grow past this, so a script cannot fill the memory. */
export const MAX_ARRAY_SIZE = 100_000;

export const num = (value: Value | undefined): number | null =>
  typeof value === "number" && Number.isFinite(value)
    ? value
    : typeof value === "boolean"
      ? value
        ? 1
        : 0
      : null;

export const truthy = (value: Value | undefined): boolean =>
  value === true ||
  (typeof value === "number" && value !== 0 && Number.isFinite(value));

export const isNa = (value: Value | undefined): boolean =>
  value === null ||
  value === undefined ||
  (typeof value === "number" && !Number.isFinite(value));

/** Thrown with the message only; the interpreter adds the line and column. */
export class PineValueError extends Error {}

const fail = (message: string): never => {
  throw new PineValueError(message);
};

// --- named constants ----------------------------------------------------------------

/** Names whose value is the name itself (styles, sizes, positions, …). */
const SELF_NAMED = [
  "plot.style_",
  "hline.style_",
  "line.style_",
  "label.style_",
  "size.",
  "text.align_",
  "text.wrap_",
  "position.",
  "shape.",
  "location.",
  "xloc.",
  "yloc.",
  "extend.",
  "display.",
  "barmerge.",
  "format.",
  "font.",
  "order.",
  "scale.",
  "currency.",
  "alert.freq_",
  "adjustment.",
  "session.",
];

const STRATEGY_CONSTANTS: Record<string, string> = {
  "strategy.long": "long",
  "strategy.short": "short",
  "strategy.fixed": "fixed",
  "strategy.cash": "cash",
  "strategy.percent_of_equity": "percent_of_equity",
  "strategy.commission.percent": "percent",
  "strategy.commission.cash_per_contract": "cash_per_contract",
  "strategy.commission.cash_per_order": "cash_per_order",
  "strategy.direction.all": "all",
  "strategy.direction.long": "long",
  "strategy.direction.short": "short",
  "strategy.oca.none": "none",
  "strategy.oca.cancel": "cancel",
  "strategy.oca.reduce": "reduce",
};

const DAYS = [
  "sunday",
  "monday",
  "tuesday",
  "wednesday",
  "thursday",
  "friday",
  "saturday",
];

/** The value of a constant such as `shape.triangleup` or `dayofweek.monday`. */
export function namedConstant(name: string): Value | undefined {
  if (SELF_NAMED.some((prefix) => name.startsWith(prefix))) return name;
  if (name in STRATEGY_CONSTANTS) return STRATEGY_CONSTANTS[name];
  if (name.startsWith("dayofweek.")) {
    const day = DAYS.indexOf(name.slice(10));
    if (day >= 0) return day + 1;
  }
  switch (name) {
    case "math.pi":
      return Math.PI;
    case "math.e":
      return Math.E;
    case "math.phi":
      return (1 + Math.sqrt(5)) / 2;
    case "math.rphi":
      return (Math.sqrt(5) - 1) / 2;
    default:
      return undefined;
  }
}

// --- strings ------------------------------------------------------------------------

function grouped(text: string): string {
  const [whole, fraction] = text.split(".");
  const sign = whole.startsWith("-") ? "-" : "";
  const digits = sign ? whole.slice(1) : whole;
  const withCommas = digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${sign}${withCommas}${fraction === undefined ? "" : `.${fraction}`}`;
}

/** A number in a pattern such as `#.##`, `0.00` or `#,###.0`. */
function patterned(value: number, pattern: string): string {
  const dot = pattern.indexOf(".");
  const fraction = dot >= 0 ? pattern.slice(dot + 1).replace(/[^0#]/g, "") : "";
  const fixed = (fraction.match(/0/g) ?? []).length;
  let text = value.toFixed(fraction.length);
  if (fraction.length > fixed && text.includes(".")) {
    // `#` decimals are dropped when they are zeros.
    const [whole, part] = text.split(".");
    let trimmed = part;
    while (trimmed.length > fixed && trimmed.endsWith("0")) {
      trimmed = trimmed.slice(0, -1);
    }
    text = trimmed ? `${whole}.${trimmed}` : whole;
  }
  return pattern.includes(",") ? grouped(text) : text;
}

function plain(value: number): string {
  if (Number.isInteger(value)) return String(value);
  return String(Number(value.toFixed(8)));
}

/** `str.tostring(value, format)`; `decimals` is the chart's price precision. */
export function toText(value: Value, format?: Value, decimals = 2): string {
  if (value === null) return "NaN";
  if (typeof value === "string") return value;
  if (typeof value === "boolean") return value ? "true" : "false";
  if (value instanceof PineArray || Array.isArray(value)) {
    const items = value instanceof PineArray ? value.items : value;
    return `[${items.map((item) => toText(item, format, decimals)).join(", ")}]`;
  }
  if (typeof value !== "number") return "";
  if (!Number.isFinite(value)) return "NaN";
  if (typeof format !== "string" || format === "format.inherit") {
    return plain(value);
  }
  switch (format) {
    case "format.mintick":
    case "format.price":
      return value.toFixed(decimals);
    case "format.percent":
      return `${value.toFixed(2)}%`;
    case "format.volume": {
      const units: [number, string][] = [
        [1e9, "B"],
        [1e6, "M"],
        [1e3, "K"],
      ];
      for (const [size, unit] of units) {
        if (Math.abs(value) >= size) {
          return `${Number((value / size).toFixed(3))}${unit}`;
        }
      }
      return plain(value);
    }
    default:
      return /[#0]/.test(format) ? patterned(value, format) : plain(value);
  }
}

/** `str.format("{0} is {1,number,#.##}", …)`. */
function formatted(pattern: string, args: Value[], decimals: number): string {
  return pattern.replace(
    /\{(\d+)(?:,\s*number(?:,\s*([^}]+))?)?\}/g,
    (_, index: string, style?: string) => {
      const value = args[Number(index)] ?? null;
      if (style === undefined) return toText(value, undefined, decimals);
      if (style.trim() === "percent" && typeof value === "number") {
        return `${Math.round(value * 100)}%`;
      }
      if (style.trim() === "integer" && typeof value === "number") {
        return String(Math.round(value));
      }
      return toText(value, style.trim(), decimals);
    },
  );
}

const text = (value: Value | undefined): string =>
  typeof value === "string"
    ? value
    : value === null
      ? ""
      : toText(value ?? null);

/** `str.*` functions, or undefined when the name is not one. */
export function callString(
  name: string,
  args: Value[],
  decimals: number,
): Value | undefined {
  const s = text(args[0]);
  switch (name) {
    case "str.tostring":
      return toText(args[0] ?? null, args[1], decimals);
    case "str.format":
      return formatted(s, args.slice(1), decimals);
    case "str.length":
      return s.length;
    case "str.upper":
      return s.toUpperCase();
    case "str.lower":
      return s.toLowerCase();
    case "str.trim":
      return s.trim();
    case "str.contains":
      return s.includes(text(args[1]));
    case "str.startswith":
      return s.startsWith(text(args[1]));
    case "str.endswith":
      return s.endsWith(text(args[1]));
    case "str.pos": {
      const at = s.indexOf(text(args[1]));
      return at < 0 ? null : at;
    }
    case "str.substring": {
      const from = num(args[1]) ?? 0;
      const to = num(args[2]);
      return s.substring(from, to ?? undefined);
    }
    case "str.replace": {
      const target = text(args[1]);
      const replacement = text(args[2]);
      const occurrence = num(args[3]) ?? 0;
      let from = 0;
      for (let k = 0; k <= occurrence; k += 1) {
        const at = s.indexOf(target, from);
        if (at < 0) return s;
        if (k === occurrence) {
          return s.slice(0, at) + replacement + s.slice(at + target.length);
        }
        from = at + target.length;
      }
      return s;
    }
    case "str.replace_all":
      return text(args[1]) === ""
        ? s
        : s.split(text(args[1])).join(text(args[2]));
    case "str.split":
      return new PineArray(s.split(text(args[1])));
    case "str.tonumber": {
      const value = Number(s);
      return s.trim() !== "" && Number.isFinite(value) ? value : null;
    }
    case "str.repeat": {
      const count = Math.max(0, Math.min(1000, num(args[1]) ?? 0));
      return Array.from({ length: count }, () => s).join(text(args[2] ?? ""));
    }
    default:
      return undefined;
  }
}

// --- arrays -------------------------------------------------------------------------

function arrayOf(value: Value | undefined): PineArray {
  if (value instanceof PineArray) return value;
  return fail(value === null ? "the array is na" : "expected an array");
}

function indexIn(array: PineArray, value: Value | undefined): number {
  const index = num(value);
  if (index === null || !Number.isInteger(index)) {
    return fail("an array index must be a whole number");
  }
  // Negative indexes count from the end, as in Pine v5.
  const at = index < 0 ? array.items.length + index : index;
  if (at < 0 || at >= array.items.length) {
    return fail(
      `index ${index} is out of bounds: the array has ${array.items.length} items`,
    );
  }
  return at;
}

function grow(array: PineArray, by = 1): void {
  if (array.items.length + by > MAX_ARRAY_SIZE) {
    fail(`an array cannot hold more than ${MAX_ARRAY_SIZE} items`);
  }
}

function numbers(array: PineArray): number[] {
  return array.items
    .map((item) => num(item))
    .filter((item): item is number => item !== null);
}

function quantile(sorted: number[], fraction: number): number | null {
  if (sorted.length === 0) return null;
  const at = (sorted.length - 1) * fraction;
  const low = Math.floor(at);
  const high = Math.ceil(at);
  return sorted[low] + (sorted[high] - sorted[low]) * (at - low);
}

const same = (a: Value, b: Value | undefined): boolean =>
  a === b || (num(a) !== null && num(a) === num(b ?? null));

const ascending = (a: Value, b: Value): number =>
  typeof a === "string" && typeof b === "string"
    ? a.localeCompare(b)
    : (num(a) ?? Number.POSITIVE_INFINITY) -
      (num(b) ?? Number.POSITIVE_INFINITY);

/** `array.*` functions, or undefined when the name is not one. */
export function callArray(name: string, args: Value[]): Value | undefined {
  if (name.startsWith("array.new")) {
    const size = num(args[0]) ?? 0;
    if (!Number.isInteger(size) || size < 0 || size > MAX_ARRAY_SIZE) {
      return fail(
        `an array size must be a whole number from 0 to ${MAX_ARRAY_SIZE}`,
      );
    }
    const initial = args[1] ?? null;
    return new PineArray(Array.from({ length: size }, () => initial));
  }
  switch (name) {
    case "array.from":
      return new PineArray([...args]);
    case "array.size":
      return arrayOf(args[0]).items.length;
    case "array.get": {
      const array = arrayOf(args[0]);
      return array.items[indexIn(array, args[1])];
    }
    case "array.set": {
      const array = arrayOf(args[0]);
      array.items[indexIn(array, args[1])] = args[2] ?? null;
      return null;
    }
    case "array.push": {
      const array = arrayOf(args[0]);
      grow(array);
      array.items.push(args[1] ?? null);
      return null;
    }
    case "array.unshift": {
      const array = arrayOf(args[0]);
      grow(array);
      array.items.unshift(args[1] ?? null);
      return null;
    }
    case "array.insert": {
      const array = arrayOf(args[0]);
      grow(array);
      const index = num(args[1]) ?? 0;
      if (index < 0 || index > array.items.length) {
        return fail(
          `index ${index} is out of bounds: the array has ${array.items.length} items`,
        );
      }
      array.items.splice(index, 0, args[2] ?? null);
      return null;
    }
    case "array.pop": {
      const array = arrayOf(args[0]);
      if (array.items.length === 0)
        return fail("cannot pop from an empty array");
      return array.items.pop() ?? null;
    }
    case "array.shift": {
      const array = arrayOf(args[0]);
      if (array.items.length === 0) return fail("cannot shift an empty array");
      return array.items.shift() ?? null;
    }
    case "array.remove": {
      const array = arrayOf(args[0]);
      return array.items.splice(indexIn(array, args[1]), 1)[0] ?? null;
    }
    case "array.clear":
      arrayOf(args[0]).items.length = 0;
      return null;
    case "array.first": {
      const array = arrayOf(args[0]);
      if (array.items.length === 0) return fail("the array is empty");
      return array.items[0];
    }
    case "array.last": {
      const array = arrayOf(args[0]);
      if (array.items.length === 0) return fail("the array is empty");
      return array.items[array.items.length - 1];
    }
    case "array.includes":
      return arrayOf(args[0]).items.some((item) => same(item, args[1]));
    case "array.indexof": {
      const at = arrayOf(args[0]).items.findIndex((item) =>
        same(item, args[1]),
      );
      return at;
    }
    case "array.lastindexof": {
      const items = arrayOf(args[0]).items;
      for (let k = items.length - 1; k >= 0; k -= 1) {
        if (same(items[k], args[1])) return k;
      }
      return -1;
    }
    case "array.sum": {
      const list = numbers(arrayOf(args[0]));
      return list.reduce((a, b) => a + b, 0);
    }
    case "array.avg": {
      const list = numbers(arrayOf(args[0]));
      return list.length ? list.reduce((a, b) => a + b, 0) / list.length : null;
    }
    case "array.min":
    case "array.max": {
      const list = numbers(arrayOf(args[0])).sort((a, b) => a - b);
      if (list.length === 0) return null;
      const nth = num(args[1]) ?? 0;
      const ordered = name === "array.max" ? list.reverse() : list;
      return ordered[nth] ?? null;
    }
    case "array.range": {
      const list = numbers(arrayOf(args[0]));
      return list.length ? Math.max(...list) - Math.min(...list) : null;
    }
    case "array.median":
      return quantile(
        numbers(arrayOf(args[0])).sort((a, b) => a - b),
        0.5,
      );
    case "array.percentile_linear_interpolation":
      return quantile(
        numbers(arrayOf(args[0])).sort((a, b) => a - b),
        (num(args[1]) ?? 50) / 100,
      );
    case "array.percentile_nearest_rank": {
      const list = numbers(arrayOf(args[0])).sort((a, b) => a - b);
      if (list.length === 0) return null;
      const rank = Math.ceil(((num(args[1]) ?? 50) / 100) * list.length);
      return list[Math.max(0, rank - 1)];
    }
    case "array.mode": {
      const counts = new Map<number, number>();
      for (const value of numbers(arrayOf(args[0]))) {
        counts.set(value, (counts.get(value) ?? 0) + 1);
      }
      let best: number | null = null;
      let bestCount = 0;
      for (const [value, count] of counts) {
        if (
          count > bestCount ||
          (count === bestCount && value < (best ?? value))
        ) {
          best = value;
          bestCount = count;
        }
      }
      return best;
    }
    case "array.variance":
    case "array.stdev": {
      const list = numbers(arrayOf(args[0]));
      if (list.length === 0) return null;
      const biased = args[1] === undefined ? true : args[1] !== false;
      const mean = list.reduce((a, b) => a + b, 0) / list.length;
      const divisor = biased ? list.length : list.length - 1;
      if (divisor <= 0) return null;
      const variance =
        list.reduce((sum, value) => sum + (value - mean) ** 2, 0) / divisor;
      return name === "array.stdev" ? Math.sqrt(variance) : variance;
    }
    case "array.covariance": {
      const a = numbers(arrayOf(args[0]));
      const b = numbers(arrayOf(args[1]));
      if (a.length !== b.length || a.length === 0) return null;
      const ma = a.reduce((x, y) => x + y, 0) / a.length;
      const mb = b.reduce((x, y) => x + y, 0) / b.length;
      const divisor = args[2] === false ? a.length - 1 : a.length;
      return a.reduce((sum, x, k) => sum + (x - ma) * (b[k] - mb), 0) / divisor;
    }
    case "array.abs":
      return new PineArray(
        arrayOf(args[0]).items.map((item) => {
          const value = num(item);
          return value === null ? null : Math.abs(value);
        }),
      );
    case "array.sort": {
      const array = arrayOf(args[0]);
      array.items.sort(ascending);
      if (args[1] === "order.descending") array.items.reverse();
      return null;
    }
    case "array.sort_indices": {
      const items = arrayOf(args[0]).items;
      const indices = items
        .map((_, k) => k)
        .sort((a, b) => ascending(items[a], items[b]));
      if (args[1] === "order.descending") indices.reverse();
      return new PineArray(indices);
    }
    case "array.reverse":
      arrayOf(args[0]).items.reverse();
      return null;
    case "array.slice": {
      const array = arrayOf(args[0]);
      const from = num(args[1]) ?? 0;
      const to = num(args[2]) ?? array.items.length;
      return new PineArray(array.items.slice(from, to));
    }
    case "array.copy":
      return new PineArray([...arrayOf(args[0]).items]);
    case "array.concat": {
      const target = arrayOf(args[0]);
      const extra = arrayOf(args[1]);
      grow(target, extra.items.length);
      target.items.push(...extra.items);
      return target;
    }
    case "array.fill": {
      const array = arrayOf(args[0]);
      const from = num(args[2]) ?? 0;
      const to = num(args[3]) ?? array.items.length;
      array.items.fill(args[1] ?? null, from, to);
      return null;
    }
    case "array.join":
      return arrayOf(args[0])
        .items.map((item) => toText(item))
        .join(typeof args[1] === "string" ? args[1] : ",");
    case "array.every":
      return arrayOf(args[0]).items.every((item) => truthy(item));
    case "array.some":
      return arrayOf(args[0]).items.some((item) => truthy(item));
    case "array.binary_search": {
      const items = numbers(arrayOf(args[0]));
      const target = num(args[1]);
      if (target === null) return -1;
      let low = 0;
      let high = items.length - 1;
      while (low <= high) {
        const middle = (low + high) >> 1;
        if (items[middle] === target) return middle;
        if (items[middle] < target) low = middle + 1;
        else high = middle - 1;
      }
      return -1;
    }
    default:
      return undefined;
  }
}

/** Namespace of a method call on a value: `xs.push(1)` is `array.push(xs, 1)`. */
export function methodNamespace(value: Value | undefined): string | null {
  if (value instanceof PineArray) return "array";
  if (value instanceof PineRef) return value.kind;
  if (value instanceof PinePoint) return "chart.point";
  if (typeof value === "string") return "str";
  return null;
}
