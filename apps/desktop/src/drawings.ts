/**
 * Chart drawings: shapes anchored to (bar time, price), so they stay on the same bars
 * when the chart scrolls, zooms or rescales. Saved drawings belong to one chart and one
 * instrument; measure and zoom are temporary and never saved.
 */

export type DrawingKind = "trend" | "hline" | "fib" | "fibext" | "text";
export type DrawingTool = "cursor" | DrawingKind | "measure" | "zoom";

export interface Anchor {
  /** Start time of a loaded bar, epoch seconds. */
  time: number;
  price: number;
}

export interface Drawing {
  id: string;
  kind: DrawingKind;
  instrumentId: string;
  points: Anchor[];
  text?: string;
  /** Fibonacci retracement and extension options (TradingView-style). */
  fib?: FibSettings;
}

/** How many clicks each tool needs. */
export const POINTS_NEEDED: Record<
  Exclude<DrawingTool, "cursor">,
  1 | 2 | 3
> = {
  trend: 2,
  hline: 1,
  fib: 2,
  fibext: 3,
  text: 1,
  measure: 2,
  zoom: 2,
};

export interface FibSettings {
  /** Ratios drawn, e.g. 0.618; values outside 0..1 extend beyond the move. */
  levels: number[];
  extendLeft: boolean;
  extendRight: boolean;
  /** Swap the move's ends: ratio 0 at the start instead of the end. */
  reverse: boolean;
  showPrices: boolean;
  /** Shade the bands between neighbouring levels. */
  background: boolean;
}

/** The ratios offered as switches, as in TradingView's Fibonacci tools. */
export const FIB_LEVEL_CHOICES = [
  0, 0.236, 0.382, 0.5, 0.618, 0.65, 0.786, 1, 1.272, 1.414, 1.618, 2, 2.618,
  3.618, 4.236,
];
/** Shown by default, as in TradingView. */
export const DEFAULT_FIB_LEVELS = [
  0, 0.236, 0.382, 0.5, 0.618, 0.786, 1, 1.618, 2.618, 3.618, 4.236,
];
/** What retracements looked like before these options existed (kept for them). */
export const LEGACY_FIB_LEVELS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
export const MAX_FIB_LEVELS = 24;

export function defaultFibSettings(): FibSettings {
  return {
    levels: [...DEFAULT_FIB_LEVELS],
    extendLeft: false,
    extendRight: false,
    reverse: false,
    showPrices: true,
    background: true,
  };
}

/** A drawing's Fibonacci options; retracements saved before options existed keep
 * their look (seven levels, drawn to the right edge, no shading). */
export function fibSettingsOf(
  drawing: Pick<Drawing, "kind" | "fib">,
): FibSettings {
  if (drawing.fib) return drawing.fib;
  return drawing.kind === "fib"
    ? {
        ...defaultFibSettings(),
        levels: [...LEGACY_FIB_LEVELS],
        extendRight: true,
        background: false,
      }
    : defaultFibSettings();
}

const sortedRatios = (levels: number[]) =>
  [...new Set(levels)].sort((x, y) => x - y);

export interface FibLevel {
  ratio: number;
  price: number;
}

/**
 * Retracement levels of a move from a to b: ratio 0 sits at the end of the move and 1
 * at its start, so the levels measure how far price gives back; ratios above 1 reach
 * beyond the start. `reverse` swaps the ends.
 */
export function fibLevels(
  a: Anchor,
  b: Anchor,
  settings: FibSettings = fibSettingsOf({ kind: "fib" }),
): FibLevel[] {
  const [start, end] = settings.reverse ? [b, a] : [a, b];
  return sortedRatios(settings.levels).map((ratio) => ({
    ratio,
    price: end.price + (start.price - end.price) * ratio,
  }));
}

/**
 * Trend-based extension: the move from a to b projected from c, so ratio 1 is c plus
 * the whole a-to-b move and 1.618 is c plus 1.618 times it. `reverse` projects the
 * move the other way.
 */
export function fibExtensionLevels(
  a: Anchor,
  b: Anchor,
  c: Anchor,
  settings: FibSettings = defaultFibSettings(),
): FibLevel[] {
  const move = (b.price - a.price) * (settings.reverse ? -1 : 1);
  return sortedRatios(settings.levels).map((ratio) => ({
    ratio,
    price: c.price + move * ratio,
  }));
}

/** Saved options with anything malformed replaced by defaults. */
export function parseFibSettings(value: unknown): FibSettings | undefined {
  if (typeof value !== "object" || value === null) return undefined;
  const record = value as Record<string, unknown>;
  const defaults = defaultFibSettings();
  const levels = Array.isArray(record.levels)
    ? sortedRatios(
        record.levels.filter(
          (level): level is number =>
            typeof level === "number" &&
            Number.isFinite(level) &&
            level >= -10 &&
            level <= 10,
        ),
      ).slice(0, MAX_FIB_LEVELS)
    : defaults.levels;
  const flag = (key: keyof FibSettings, fallback: boolean) =>
    typeof record[key] === "boolean" ? (record[key] as boolean) : fallback;
  return {
    levels,
    extendLeft: flag("extendLeft", defaults.extendLeft),
    extendRight: flag("extendRight", defaults.extendRight),
    reverse: flag("reverse", defaults.reverse),
    showPrices: flag("showPrices", defaults.showPrices),
    background: flag("background", defaults.background),
  };
}

export interface Measurement {
  change: number;
  percent: number | null;
  bars: number;
  seconds: number;
}

/** Price and time distance between two anchors; `barSeconds` is the chart timeframe. */
export function measure(a: Anchor, b: Anchor, barSeconds: number): Measurement {
  const change = b.price - a.price;
  const seconds = b.time - a.time;
  return {
    change,
    percent: a.price === 0 ? null : (change / a.price) * 100,
    bars: barSeconds > 0 ? Math.round(seconds / barSeconds) : 0,
    seconds,
  };
}

export interface BarPrices {
  open: number;
  high: number;
  low: number;
  close: number;
}

/** With the magnet on, a click lands on the bar's nearest open, high, low or close. */
export function snapPrice(price: number, bar: BarPrices | undefined): number {
  if (!bar) return price;
  return [bar.open, bar.high, bar.low, bar.close].reduce((best, value) =>
    Math.abs(value - price) < Math.abs(best - price) ? value : best,
  );
}

export function durationText(seconds: number): string {
  const sign = seconds < 0 ? "-" : "";
  let rest = Math.abs(seconds);
  const days = Math.floor(rest / 86_400);
  rest -= days * 86_400;
  const hours = Math.floor(rest / 3_600);
  rest -= hours * 3_600;
  const minutes = Math.round(rest / 60);
  const parts = [
    days ? `${days}d` : "",
    hours ? `${hours}h` : "",
    minutes || (!days && !hours) ? `${minutes}m` : "",
  ].filter(Boolean);
  return sign + parts.join(" ");
}

const KINDS: DrawingKind[] = ["trend", "hline", "fib", "fibext", "text"];
const MAX_TEXT = 200;

/** Saved drawings with anything malformed dropped. */
export function parseDrawings(value: unknown): Drawing[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item): Drawing[] => {
    if (typeof item !== "object" || item === null) return [];
    const record = item as Record<string, unknown>;
    const kind = record.kind as DrawingKind;
    if (
      typeof record.id !== "string" ||
      record.id.length === 0 ||
      !KINDS.includes(kind) ||
      typeof record.instrumentId !== "string" ||
      !Array.isArray(record.points)
    ) {
      return [];
    }
    const points = record.points.filter(
      (point): point is Anchor =>
        typeof point === "object" &&
        point !== null &&
        Number.isFinite((point as Anchor).time) &&
        Number.isFinite((point as Anchor).price),
    );
    if (points.length !== POINTS_NEEDED[kind]) return [];
    const text =
      typeof record.text === "string"
        ? record.text.slice(0, MAX_TEXT)
        : undefined;
    if (kind === "text" && !text) return [];
    const fib =
      kind === "fib" || kind === "fibext"
        ? parseFibSettings(record.fib)
        : undefined;
    return [
      {
        id: record.id,
        kind,
        instrumentId: record.instrumentId,
        points: points.map(({ time, price }) => ({ time, price })),
        ...(text ? { text } : {}),
        ...(fib ? { fib } : {}),
      },
    ];
  });
}
