/**
 * Chart drawings: shapes anchored to (bar time, price), so they stay on the same bars
 * when the chart scrolls, zooms or rescales. Saved drawings belong to one chart and one
 * instrument; measure and zoom are temporary and never saved.
 */

export type DrawingKind = "trend" | "hline" | "fib" | "text";
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
}

/** How many clicks each tool needs. */
export const POINTS_NEEDED: Record<Exclude<DrawingTool, "cursor">, 1 | 2> = {
  trend: 2,
  hline: 1,
  fib: 2,
  text: 1,
  measure: 2,
  zoom: 2,
};

/** Standard Fibonacci retracement ratios. */
export const FIB_RATIOS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];

/**
 * Retracement levels between a move's start (a) and end (b): ratio 0 sits at the end
 * of the move and 1 at its start, so the levels measure how far price gives back.
 */
export function fibLevels(
  a: Anchor,
  b: Anchor,
): { ratio: number; price: number }[] {
  return FIB_RATIOS.map((ratio) => ({
    ratio,
    price: b.price + (a.price - b.price) * ratio,
  }));
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

const KINDS: DrawingKind[] = ["trend", "hline", "fib", "text"];
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
    return [
      {
        id: record.id,
        kind,
        instrumentId: record.instrumentId,
        points: points.map(({ time, price }) => ({ time, price })),
        ...(text ? { text } : {}),
      },
    ];
  });
}
