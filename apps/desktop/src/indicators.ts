/**
 * Standard technical indicators, written from their published formulas.
 *
 * Values are computed from the bars currently loaded for a chart, in order, and each
 * value uses only that bar and earlier ones (no look-ahead), except where a formula
 * plots a value at a shifted time on purpose (Ichimoku spans), which is noted. Until a
 * formula has enough bars (its warm-up), the value is null and nothing is drawn.
 *
 * Numbers are JS floats: indicators are for display and analysis, not for accounting.
 */

import {
  alignToChart,
  isHigherTimeframe,
  type HigherBars,
  type HigherTimeframe,
} from "./higher-timeframe";
import {
  formulaDefinition,
  formulaLookback,
  MAX_FORMULA_LENGTH,
  parseFormula,
} from "./formula";

export interface OhlcvBar {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export type Series = (number | null)[];

// ---------------------------------------------------------------------------------
// Building blocks
// ---------------------------------------------------------------------------------

export function sma(values: Series, length: number): Series {
  const out: Series = [];
  let sum = 0;
  let count = 0;
  const window: (number | null)[] = [];
  for (const value of values) {
    window.push(value);
    if (value !== null) {
      sum += value;
      count += 1;
    }
    if (window.length > length) {
      const dropped = window.shift();
      if (dropped !== null && dropped !== undefined) {
        sum -= dropped;
        count -= 1;
      }
    }
    out.push(
      window.length === length && count === length ? sum / length : null,
    );
  }
  return out;
}

/** Exponential average seeded with the simple average of the first `length` values. */
function smoothed(values: Series, length: number, alpha: number): Series {
  const out: Series = [];
  let previous: number | null = null;
  const seed: number[] = [];
  for (const value of values) {
    if (value === null) {
      out.push(previous === null ? null : previous);
      continue;
    }
    if (previous === null) {
      seed.push(value);
      if (seed.length === length) {
        previous = seed.reduce((total, item) => total + item, 0) / length;
        out.push(previous);
      } else {
        out.push(null);
      }
      continue;
    }
    previous = alpha * value + (1 - alpha) * previous;
    out.push(previous);
  }
  return out;
}

export function ema(values: Series, length: number): Series {
  return smoothed(values, length, 2 / (length + 1));
}

/** Wilder's moving average (RMA), used by RSI, ATR and ADX. */
export function rma(values: Series, length: number): Series {
  return smoothed(values, length, 1 / length);
}

export function wma(values: Series, length: number): Series {
  const out: Series = [];
  const denominator = (length * (length + 1)) / 2;
  for (let i = 0; i < values.length; i += 1) {
    if (i < length - 1) {
      out.push(null);
      continue;
    }
    let total = 0;
    let valid = true;
    for (let k = 0; k < length; k += 1) {
      const value = values[i - k];
      if (value === null) {
        valid = false;
        break;
      }
      total += value * (length - k);
    }
    out.push(valid ? total / denominator : null);
  }
  return out;
}

/** Population standard deviation over a window. */
export function stdev(values: Series, length: number): Series {
  const mean = sma(values, length);
  return values.map((_, i) => {
    const average = mean[i];
    if (average === null) return null;
    let total = 0;
    for (let k = 0; k < length; k += 1) {
      const value = values[i - k] as number;
      total += (value - average) ** 2;
    }
    return Math.sqrt(total / length);
  });
}

function rolling(
  values: Series,
  length: number,
  pick: (window: number[]) => number,
): Series {
  return values.map((_, i) => {
    if (i < length - 1) return null;
    const window = values.slice(i - length + 1, i + 1);
    return window.every((value) => value !== null)
      ? pick(window as number[])
      : null;
  });
}

export const highest = (values: Series, length: number) =>
  rolling(values, length, (window) => Math.max(...window));
export const lowest = (values: Series, length: number) =>
  rolling(values, length, (window) => Math.min(...window));

function combine(
  a: Series,
  b: Series,
  op: (x: number, y: number) => number | null,
): Series {
  return a.map((x, i) => {
    const y = b[i];
    return x === null || y === null ? null : op(x, y);
  });
}

const minus = (a: Series, b: Series) => combine(a, b, (x, y) => x - y);
const plus = (a: Series, b: Series) => combine(a, b, (x, y) => x + y);
const ratio = (a: Series, b: Series, scale = 1) =>
  combine(a, b, (x, y) => (y === 0 ? null : (scale * x) / y));
const times = (a: Series, k: number): Series =>
  a.map((x) => (x === null ? null : x * k));

export function trueRange(bars: OhlcvBar[]): Series {
  return bars.map((bar, i) =>
    i === 0
      ? bar.high - bar.low
      : Math.max(
          bar.high - bar.low,
          Math.abs(bar.high - bars[i - 1].close),
          Math.abs(bar.low - bars[i - 1].close),
        ),
  );
}

export function change(values: Series, length = 1): Series {
  return values.map((value, i) => {
    const before = values[i - length];
    return i < length ||
      value === null ||
      before === null ||
      before === undefined
      ? null
      : value - before;
  });
}

const closes = (bars: OhlcvBar[]): Series => bars.map((bar) => bar.close);
const hl2 = (bars: OhlcvBar[]): Series =>
  bars.map((bar) => (bar.high + bar.low) / 2);
const hlc3 = (bars: OhlcvBar[]): Series =>
  bars.map((bar) => (bar.high + bar.low + bar.close) / 3);

export function shift(values: Series, by: number): Series {
  // Positive `by` moves values later in time (plotted `by` bars after their bar).
  return values.map((_, i) => {
    const source = i - by;
    return source >= 0 && source < values.length ? values[source] : null;
  });
}

/** What some formulas need besides bars: VWAP restarts each exchange-local day. */
export interface IndicatorContext {
  timeZone: string;
}

const dayFormats = new Map<string, Intl.DateTimeFormat>();
function exchangeDate(time: number, timeZone: string): string {
  let format = dayFormats.get(timeZone);
  if (!format) {
    format = new Intl.DateTimeFormat("en-CA", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
    dayFormats.set(timeZone, format);
  }
  return format.format(new Date(time * 1000));
}

// ---------------------------------------------------------------------------------
// Catalog
// ---------------------------------------------------------------------------------

export type IndicatorCategory =
  "trend" | "momentum" | "volatility" | "volume" | "custom";

export interface IndicatorInput {
  key: string;
  label: { en: string; ko: string };
  default: number;
  min: number;
  max: number;
  step?: number;
  /** False for settings that are not a number of bars to look back over. */
  lookback?: boolean;
}

export interface IndicatorOutput {
  key: string;
  label: string;
  color: string;
  kind?: "line" | "histogram";
}

export interface IndicatorDefinition {
  id: string;
  name: { en: string; ko: string };
  short: string;
  category: IndicatorCategory;
  /** Drawn over the price candles, or in its own pane below. */
  pane: "price" | "separate";
  inputs: IndicatorInput[];
  outputs: IndicatorOutput[];
  /** Horizontal guide levels for oscillators (e.g. RSI 30/70). */
  levels?: number[];
  /** Reads only closes, so its source can be another price (open, hl2, …). */
  sourceInput?: boolean;
  compute: (
    bars: OhlcvBar[],
    p: Record<string, number>,
    context?: IndicatorContext,
  ) => Record<string, Series>;
}

const len = (fallback: number, max = 500): IndicatorInput => ({
  key: "length",
  label: { en: "Length", ko: "기간" },
  default: fallback,
  min: 1,
  max,
});
const input = (
  key: string,
  en: string,
  ko: string,
  fallback: number,
  min = 1,
  max = 500,
  step?: number,
): IndicatorInput => ({
  key,
  label: { en, ko },
  default: fallback,
  min,
  max,
  ...(step === undefined ? {} : { step }),
});

const BLUE = "#4f8ff7";
const ORANGE = "#f39c3d";
const PURPLE = "#a777e8";
const TEAL = "#2bb5a5";
const RED = "#e5566b";
const GREEN = "#3fb68b";
const GREY = "#8b95a5";

export const INDICATORS: IndicatorDefinition[] = [
  // --- Moving averages and trend overlays ------------------------------------------
  {
    id: "sma",
    sourceInput: true,
    name: { en: "Moving Average Simple", ko: "단순 이동평균(SMA)" },
    short: "SMA",
    category: "trend",
    pane: "price",
    inputs: [len(9)],
    outputs: [{ key: "value", label: "SMA", color: BLUE }],
    compute: (bars, p) => ({ value: sma(closes(bars), p.length) }),
  },
  {
    id: "ema",
    sourceInput: true,
    name: { en: "Moving Average Exponential", ko: "지수 이동평균(EMA)" },
    short: "EMA",
    category: "trend",
    pane: "price",
    inputs: [len(9)],
    outputs: [{ key: "value", label: "EMA", color: ORANGE }],
    compute: (bars, p) => ({ value: ema(closes(bars), p.length) }),
  },
  {
    id: "wma",
    sourceInput: true,
    name: { en: "Moving Average Weighted", ko: "가중 이동평균(WMA)" },
    short: "WMA",
    category: "trend",
    pane: "price",
    inputs: [len(9)],
    outputs: [{ key: "value", label: "WMA", color: PURPLE }],
    compute: (bars, p) => ({ value: wma(closes(bars), p.length) }),
  },
  {
    id: "vwma",
    name: {
      en: "Volume Weighted Moving Average",
      ko: "거래량 가중 이동평균(VWMA)",
    },
    short: "VWMA",
    category: "trend",
    pane: "price",
    inputs: [len(20)],
    outputs: [{ key: "value", label: "VWMA", color: TEAL }],
    compute: (bars, p) => {
      const volume = bars.map((bar) => bar.volume);
      const priceVolume = bars.map((bar) => bar.close * bar.volume);
      return {
        value: ratio(sma(priceVolume, p.length), sma(volume, p.length)),
      };
    },
  },
  {
    id: "hma",
    sourceInput: true,
    name: { en: "Hull Moving Average", ko: "헐 이동평균(HMA)" },
    short: "HMA",
    category: "trend",
    pane: "price",
    inputs: [len(9)],
    outputs: [{ key: "value", label: "HMA", color: "#e0b341" }],
    compute: (bars, p) => {
      const source = closes(bars);
      const half = Math.max(1, Math.floor(p.length / 2));
      const raw = minus(times(wma(source, half), 2), wma(source, p.length));
      return { value: wma(raw, Math.max(1, Math.round(Math.sqrt(p.length)))) };
    },
  },
  {
    id: "vwap",
    name: { en: "VWAP (session anchored)", ko: "VWAP(거래일 기준)" },
    short: "VWAP",
    category: "volume",
    pane: "price",
    inputs: [],
    outputs: [{ key: "value", label: "VWAP", color: "#e6c547" }],
    compute: (bars, _p, context) => {
      const timeZone = context?.timeZone ?? "America/New_York";
      let day = "";
      let priceVolume = 0;
      let volume = 0;
      return {
        value: bars.map((bar) => {
          const today = exchangeDate(bar.time, timeZone);
          if (today !== day) {
            day = today;
            priceVolume = 0;
            volume = 0;
          }
          priceVolume += ((bar.high + bar.low + bar.close) / 3) * bar.volume;
          volume += bar.volume;
          return volume === 0 ? null : priceVolume / volume;
        }),
      };
    },
  },
  {
    id: "bb",
    sourceInput: true,
    name: { en: "Bollinger Bands", ko: "볼린저 밴드" },
    short: "BB",
    category: "volatility",
    pane: "price",
    inputs: [
      len(20),
      input("mult", "StdDev", "표준편차 배수", 2, 0.1, 10, 0.1),
    ],
    outputs: [
      { key: "basis", label: "Basis", color: ORANGE },
      { key: "upper", label: "Upper", color: BLUE },
      { key: "lower", label: "Lower", color: BLUE },
    ],
    compute: (bars, p) => {
      const basis = sma(closes(bars), p.length);
      const deviation = times(stdev(closes(bars), p.length), p.mult);
      return {
        basis,
        upper: plus(basis, deviation),
        lower: minus(basis, deviation),
      };
    },
  },
  {
    id: "kc",
    name: { en: "Keltner Channels", ko: "켈트너 채널" },
    short: "KC",
    category: "volatility",
    pane: "price",
    inputs: [
      len(20),
      input("mult", "Multiplier", "배수", 2, 0.1, 10, 0.1),
      input("atr", "ATR length", "ATR 기간", 10),
    ],
    outputs: [
      { key: "basis", label: "Basis", color: BLUE },
      { key: "upper", label: "Upper", color: TEAL },
      { key: "lower", label: "Lower", color: TEAL },
    ],
    compute: (bars, p) => {
      const basis = ema(closes(bars), p.length);
      const range = times(rma(trueRange(bars), p.atr), p.mult);
      return { basis, upper: plus(basis, range), lower: minus(basis, range) };
    },
  },
  {
    id: "dc",
    name: { en: "Donchian Channels", ko: "돈치안 채널" },
    short: "DC",
    category: "volatility",
    pane: "price",
    inputs: [len(20)],
    outputs: [
      { key: "upper", label: "Upper", color: BLUE },
      { key: "basis", label: "Basis", color: ORANGE },
      { key: "lower", label: "Lower", color: BLUE },
    ],
    compute: (bars, p) => {
      const upper = highest(
        bars.map((bar) => bar.high),
        p.length,
      );
      const lower = lowest(
        bars.map((bar) => bar.low),
        p.length,
      );
      return { upper, lower, basis: times(plus(upper, lower), 0.5) };
    },
  },
  {
    id: "env",
    sourceInput: true,
    name: { en: "Envelope", ko: "엔벨로프" },
    short: "ENV",
    category: "trend",
    pane: "price",
    inputs: [len(20), input("percent", "Percent", "퍼센트", 10, 0.1, 100, 0.1)],
    outputs: [
      { key: "basis", label: "Basis", color: ORANGE },
      { key: "upper", label: "Upper", color: BLUE },
      { key: "lower", label: "Lower", color: BLUE },
    ],
    compute: (bars, p) => {
      const basis = sma(closes(bars), p.length);
      return {
        basis,
        upper: times(basis, 1 + p.percent / 100),
        lower: times(basis, 1 - p.percent / 100),
      };
    },
  },
  {
    id: "psar",
    name: { en: "Parabolic SAR", ko: "파라볼릭 SAR" },
    short: "SAR",
    category: "trend",
    pane: "price",
    inputs: [
      input("start", "Start", "시작", 0.02, 0.001, 1, 0.001),
      input("increment", "Increment", "증가", 0.02, 0.001, 1, 0.001),
      input("maximum", "Maximum", "최대", 0.2, 0.01, 1, 0.01),
    ],
    outputs: [{ key: "value", label: "SAR", color: "#d1d4dc" }],
    compute: (bars, p) => {
      const out: Series = bars.map(() => null);
      if (bars.length < 2) return { value: out };
      let rising = bars[1].close >= bars[0].close;
      let sar = rising ? bars[0].low : bars[0].high;
      let extreme = rising ? bars[0].high : bars[0].low;
      let factor = p.start;
      for (let i = 1; i < bars.length; i += 1) {
        const bar = bars[i];
        sar = sar + factor * (extreme - sar);
        if (rising) {
          sar = Math.min(sar, bars[i - 1].low, bars[Math.max(0, i - 2)].low);
          if (bar.low < sar) {
            rising = false;
            sar = extreme;
            extreme = bar.low;
            factor = p.start;
          } else if (bar.high > extreme) {
            extreme = bar.high;
            factor = Math.min(factor + p.increment, p.maximum);
          }
        } else {
          sar = Math.max(sar, bars[i - 1].high, bars[Math.max(0, i - 2)].high);
          if (bar.high > sar) {
            rising = true;
            sar = extreme;
            extreme = bar.high;
            factor = p.start;
          } else if (bar.low < extreme) {
            extreme = bar.low;
            factor = Math.min(factor + p.increment, p.maximum);
          }
        }
        out[i] = sar;
      }
      return { value: out };
    },
  },
  {
    id: "supertrend",
    name: { en: "Supertrend", ko: "슈퍼트렌드" },
    short: "ST",
    category: "trend",
    pane: "price",
    inputs: [
      input("atr", "ATR length", "ATR 기간", 10),
      input("factor", "Factor", "배수", 3, 0.1, 20, 0.1),
    ],
    outputs: [
      { key: "up", label: "Up trend", color: GREEN },
      { key: "down", label: "Down trend", color: RED },
    ],
    compute: (bars, p) => {
      const atr = rma(trueRange(bars), p.atr);
      const mid = hl2(bars);
      const up: Series = bars.map(() => null);
      const down: Series = bars.map(() => null);
      let upper = 0;
      let lower = 0;
      let direction = 1;
      let started = false;
      for (let i = 0; i < bars.length; i += 1) {
        const range = atr[i];
        const middle = mid[i] as number;
        if (range === null) continue;
        let nextUpper = middle + p.factor * range;
        let nextLower = middle - p.factor * range;
        if (started) {
          const previousClose = bars[i - 1].close;
          nextLower =
            nextLower > lower || previousClose < lower ? nextLower : lower;
          nextUpper =
            nextUpper < upper || previousClose > upper ? nextUpper : upper;
          if (direction === 1 && bars[i].close < nextLower) direction = -1;
          else if (direction === -1 && bars[i].close > nextUpper) direction = 1;
        }
        upper = nextUpper;
        lower = nextLower;
        started = true;
        if (direction === 1) up[i] = lower;
        else down[i] = upper;
      }
      return { up, down };
    },
  },
  {
    id: "ichimoku",
    name: { en: "Ichimoku Cloud", ko: "일목균형표" },
    short: "Ichimoku",
    category: "trend",
    pane: "price",
    inputs: [
      input("conversion", "Conversion line", "전환선 기간", 9),
      input("base", "Base line", "기준선 기간", 26),
      input("spanB", "Leading span B", "선행스팬2 기간", 52),
      input("displacement", "Displacement", "이동 간격", 26),
    ],
    outputs: [
      { key: "conversion", label: "Conversion", color: BLUE },
      { key: "base", label: "Base", color: RED },
      { key: "spanA", label: "Span A", color: GREEN },
      { key: "spanB", label: "Span B", color: "#c75a5a" },
      { key: "lagging", label: "Lagging", color: "#7fb37f" },
    ],
    compute: (bars, p) => {
      const highs = bars.map((bar) => bar.high);
      const lows = bars.map((bar) => bar.low);
      const middle = (length: number) =>
        times(plus(highest(highs, length), lowest(lows, length)), 0.5);
      const conversion = middle(p.conversion);
      const base = middle(p.base);
      // Leading spans are plotted `displacement` bars later; only spans that land on a
      // loaded bar are drawn. The lagging span is today's close plotted in the past:
      // a display convention that must not be used as a signal at that past time.
      return {
        conversion,
        base,
        spanA: shift(times(plus(conversion, base), 0.5), p.displacement - 1),
        spanB: shift(middle(p.spanB), p.displacement - 1),
        lagging: shift(closes(bars), -(p.displacement - 1)),
      };
    },
  },

  // --- Momentum oscillators ----------------------------------------------------------
  {
    id: "rsi",
    sourceInput: true,
    name: { en: "Relative Strength Index", ko: "상대강도지수(RSI)" },
    short: "RSI",
    category: "momentum",
    pane: "separate",
    inputs: [len(14)],
    outputs: [{ key: "value", label: "RSI", color: PURPLE }],
    levels: [70, 30],
    compute: (bars, p) => ({ value: rsi(closes(bars), p.length) }),
  },
  {
    id: "macd",
    sourceInput: true,
    name: { en: "MACD", ko: "MACD" },
    short: "MACD",
    category: "momentum",
    pane: "separate",
    inputs: [
      input("fast", "Fast length", "단기 기간", 12),
      input("slow", "Slow length", "장기 기간", 26),
      input("signal", "Signal smoothing", "시그널 기간", 9),
    ],
    outputs: [
      { key: "histogram", label: "Histogram", color: GREY, kind: "histogram" },
      { key: "macd", label: "MACD", color: BLUE },
      { key: "signal", label: "Signal", color: ORANGE },
    ],
    compute: (bars, p) => {
      const macd = minus(ema(closes(bars), p.fast), ema(closes(bars), p.slow));
      const signal = ema(macd, p.signal);
      return { macd, signal, histogram: minus(macd, signal) };
    },
  },
  {
    id: "stoch",
    name: { en: "Stochastic", ko: "스토캐스틱" },
    short: "Stoch",
    category: "momentum",
    pane: "separate",
    inputs: [
      input("k", "%K length", "%K 기간", 14),
      input("smoothK", "%K smoothing", "%K 평활", 1),
      input("d", "%D smoothing", "%D 기간", 3),
    ],
    outputs: [
      { key: "k", label: "%K", color: BLUE },
      { key: "d", label: "%D", color: ORANGE },
    ],
    levels: [80, 20],
    compute: (bars, p) => {
      const k = sma(stochastic(bars, closes(bars), p.k), p.smoothK);
      return { k, d: sma(k, p.d) };
    },
  },
  {
    id: "stochrsi",
    sourceInput: true,
    name: { en: "Stochastic RSI", ko: "스토캐스틱 RSI" },
    short: "Stoch RSI",
    category: "momentum",
    pane: "separate",
    inputs: [
      input("k", "K", "K", 3),
      input("d", "D", "D", 3),
      input("rsi", "RSI length", "RSI 기간", 14),
      input("stoch", "Stochastic length", "스토캐스틱 기간", 14),
    ],
    outputs: [
      { key: "k", label: "K", color: BLUE },
      { key: "d", label: "D", color: ORANGE },
    ],
    levels: [80, 20],
    compute: (bars, p) => {
      const strength = rsi(closes(bars), p.rsi);
      const high = highest(strength, p.stoch);
      const low = lowest(strength, p.stoch);
      const raw = strength.map((value, i) => {
        const top = high[i];
        const bottom = low[i];
        return value === null ||
          top === null ||
          bottom === null ||
          top === bottom
          ? null
          : (100 * (value - bottom)) / (top - bottom);
      });
      const k = sma(raw, p.k);
      return { k, d: sma(k, p.d) };
    },
  },
  {
    id: "cci",
    name: { en: "Commodity Channel Index", ko: "상품채널지수(CCI)" },
    short: "CCI",
    category: "momentum",
    pane: "separate",
    inputs: [len(20)],
    outputs: [{ key: "value", label: "CCI", color: TEAL }],
    levels: [100, -100],
    compute: (bars, p) => {
      const typical = hlc3(bars);
      const mean = sma(typical, p.length);
      return {
        value: typical.map((value, i) => {
          const average = mean[i];
          if (value === null || average === null) return null;
          let deviation = 0;
          for (let k = 0; k < p.length; k += 1) {
            deviation += Math.abs((typical[i - k] as number) - average);
          }
          deviation /= p.length;
          return deviation === 0
            ? null
            : (value - average) / (0.015 * deviation);
        }),
      };
    },
  },
  {
    id: "willr",
    name: { en: "Williams %R", ko: "윌리엄스 %R" },
    short: "%R",
    category: "momentum",
    pane: "separate",
    inputs: [len(14)],
    outputs: [{ key: "value", label: "%R", color: PURPLE }],
    levels: [-20, -80],
    compute: (bars, p) => ({
      value: stochastic(bars, closes(bars), p.length).map((value) =>
        value === null ? null : value - 100,
      ),
    }),
  },
  {
    id: "mom",
    sourceInput: true,
    name: { en: "Momentum", ko: "모멘텀" },
    short: "MOM",
    category: "momentum",
    pane: "separate",
    inputs: [len(10)],
    outputs: [{ key: "value", label: "MOM", color: BLUE }],
    compute: (bars, p) => ({ value: change(closes(bars), p.length) }),
  },
  {
    id: "roc",
    sourceInput: true,
    name: { en: "Rate Of Change", ko: "변화율(ROC)" },
    short: "ROC",
    category: "momentum",
    pane: "separate",
    inputs: [len(9)],
    outputs: [{ key: "value", label: "ROC", color: BLUE }],
    levels: [0],
    compute: (bars, p) => {
      const source = closes(bars);
      return {
        value: source.map((value, i) => {
          const before = source[i - p.length];
          return i < p.length || value === null || !before
            ? null
            : (100 * (value - before)) / before;
        }),
      };
    },
  },
  {
    id: "ao",
    name: { en: "Awesome Oscillator", ko: "어썸 오실레이터" },
    short: "AO",
    category: "momentum",
    pane: "separate",
    inputs: [],
    outputs: [{ key: "value", label: "AO", color: GREY, kind: "histogram" }],
    compute: (bars) => ({
      value: minus(sma(hl2(bars), 5), sma(hl2(bars), 34)),
    }),
  },
  {
    id: "trix",
    name: { en: "TRIX", ko: "TRIX" },
    short: "TRIX",
    category: "momentum",
    pane: "separate",
    inputs: [len(18)],
    outputs: [{ key: "value", label: "TRIX", color: RED }],
    levels: [0],
    compute: (bars, p) => {
      const log = bars.map((bar) =>
        bar.close > 0 ? Math.log(bar.close) : null,
      );
      const triple = ema(ema(ema(log, p.length), p.length), p.length);
      return { value: times(change(triple), 10_000) };
    },
  },
  {
    id: "uo",
    name: { en: "Ultimate Oscillator", ko: "얼티밋 오실레이터" },
    short: "UO",
    category: "momentum",
    pane: "separate",
    inputs: [
      input("fast", "Fast length", "단기 기간", 7),
      input("middle", "Middle length", "중기 기간", 14),
      input("slow", "Slow length", "장기 기간", 28),
    ],
    outputs: [{ key: "value", label: "UO", color: "#e0b341" }],
    levels: [70, 30],
    compute: (bars, p) => {
      const buying: Series = bars.map((bar, i) =>
        i === 0 ? null : bar.close - Math.min(bar.low, bars[i - 1].close),
      );
      const range: Series = bars.map((bar, i) =>
        i === 0
          ? null
          : Math.max(bar.high, bars[i - 1].close) -
            Math.min(bar.low, bars[i - 1].close),
      );
      const average = (length: number) =>
        ratio(sma(buying, length), sma(range, length));
      const fast = average(p.fast);
      const middle = average(p.middle);
      const slow = average(p.slow);
      return {
        value: fast.map((value, i) => {
          const m = middle[i];
          const s = slow[i];
          return value === null || m === null || s === null
            ? null
            : (100 * (4 * value + 2 * m + s)) / 7;
        }),
      };
    },
  },
  {
    id: "aroon",
    name: { en: "Aroon", ko: "아룬" },
    short: "Aroon",
    category: "trend",
    pane: "separate",
    inputs: [len(14)],
    outputs: [
      { key: "up", label: "Up", color: ORANGE },
      { key: "down", label: "Down", color: BLUE },
    ],
    compute: (bars, p) => {
      const up: Series = [];
      const down: Series = [];
      bars.forEach((_, i) => {
        if (i < p.length) {
          up.push(null);
          down.push(null);
          return;
        }
        let highIndex = i;
        let lowIndex = i;
        for (let k = i - p.length; k <= i; k += 1) {
          if (bars[k].high >= bars[highIndex].high) highIndex = k;
          if (bars[k].low <= bars[lowIndex].low) lowIndex = k;
        }
        up.push((100 * (p.length - (i - highIndex))) / p.length);
        down.push((100 * (p.length - (i - lowIndex))) / p.length);
      });
      return { up, down };
    },
  },
  {
    id: "dmi",
    name: {
      en: "Directional Movement Index (ADX)",
      ko: "방향성 지수(DMI·ADX)",
    },
    short: "DMI",
    category: "trend",
    pane: "separate",
    inputs: [
      input("length", "DI length", "DI 기간", 14),
      input("smoothing", "ADX smoothing", "ADX 평활", 14),
    ],
    outputs: [
      { key: "adx", label: "ADX", color: RED },
      { key: "plus", label: "+DI", color: BLUE },
      { key: "minus", label: "-DI", color: ORANGE },
    ],
    compute: (bars, p) => {
      const plusMove: Series = bars.map((bar, i) => {
        if (i === 0) return null;
        const up = bar.high - bars[i - 1].high;
        const down = bars[i - 1].low - bar.low;
        return up > down && up > 0 ? up : 0;
      });
      const minusMove: Series = bars.map((bar, i) => {
        if (i === 0) return null;
        const up = bar.high - bars[i - 1].high;
        const down = bars[i - 1].low - bar.low;
        return down > up && down > 0 ? down : 0;
      });
      const range = rma(trueRange(bars), p.length);
      const plusDi = ratio(rma(plusMove, p.length), range, 100);
      const minusDi = ratio(rma(minusMove, p.length), range, 100);
      const dx = combine(plusDi, minusDi, (a, b) =>
        a + b === 0 ? 0 : (100 * Math.abs(a - b)) / (a + b),
      );
      return { plus: plusDi, minus: minusDi, adx: rma(dx, p.smoothing) };
    },
  },

  // --- Volatility --------------------------------------------------------------------
  {
    id: "atr",
    name: { en: "Average True Range", ko: "평균 실제 범위(ATR)" },
    short: "ATR",
    category: "volatility",
    pane: "separate",
    inputs: [len(14)],
    outputs: [{ key: "value", label: "ATR", color: RED }],
    compute: (bars, p) => ({ value: rma(trueRange(bars), p.length) }),
  },
  {
    id: "bbw",
    sourceInput: true,
    name: { en: "Bollinger BandWidth", ko: "볼린저 밴드폭" },
    short: "BBW",
    category: "volatility",
    pane: "separate",
    inputs: [
      len(20),
      input("mult", "StdDev", "표준편차 배수", 2, 0.1, 10, 0.1),
    ],
    outputs: [{ key: "value", label: "BBW", color: BLUE }],
    compute: (bars, p) => {
      const basis = sma(closes(bars), p.length);
      const band = times(stdev(closes(bars), p.length), 2 * p.mult);
      return { value: ratio(band, basis) };
    },
  },
  {
    id: "bbpb",
    sourceInput: true,
    name: { en: "Bollinger Bands %B", ko: "볼린저 %B" },
    short: "%B",
    category: "volatility",
    pane: "separate",
    inputs: [
      len(20),
      input("mult", "StdDev", "표준편차 배수", 2, 0.1, 10, 0.1),
    ],
    outputs: [{ key: "value", label: "%B", color: TEAL }],
    levels: [1, 0],
    compute: (bars, p) => {
      const basis = sma(closes(bars), p.length);
      const deviation = times(stdev(closes(bars), p.length), p.mult);
      const lower = minus(basis, deviation);
      const width = times(deviation, 2);
      return { value: ratio(minus(closes(bars), lower), width) };
    },
  },
  {
    id: "hv",
    name: { en: "Historical Volatility", ko: "역사적 변동성" },
    short: "HV",
    category: "volatility",
    pane: "separate",
    inputs: [
      len(10),
      {
        ...input("periods", "Bars per year", "연간 봉 수", 252, 1, 200_000),
        lookback: false,
      },
    ],
    outputs: [{ key: "value", label: "HV", color: PURPLE }],
    compute: (bars, p) => {
      const returns: Series = bars.map((bar, i) =>
        i === 0 || bars[i - 1].close <= 0 || bar.close <= 0
          ? null
          : Math.log(bar.close / bars[i - 1].close),
      );
      return {
        value: times(stdev(returns, p.length), 100 * Math.sqrt(p.periods)),
      };
    },
  },

  // --- Volume ------------------------------------------------------------------------
  {
    id: "obv",
    name: { en: "On Balance Volume", ko: "OBV" },
    short: "OBV",
    category: "volume",
    pane: "separate",
    inputs: [],
    outputs: [{ key: "value", label: "OBV", color: BLUE }],
    compute: (bars) => {
      let total = 0;
      return {
        value: bars.map((bar, i) => {
          if (i > 0) {
            const previous = bars[i - 1].close;
            total +=
              bar.close > previous
                ? bar.volume
                : bar.close < previous
                  ? -bar.volume
                  : 0;
          }
          return total;
        }),
      };
    },
  },
  {
    id: "mfi",
    name: { en: "Money Flow Index", ko: "자금흐름지수(MFI)" },
    short: "MFI",
    category: "volume",
    pane: "separate",
    inputs: [len(14)],
    outputs: [{ key: "value", label: "MFI", color: PURPLE }],
    levels: [80, 20],
    compute: (bars, p) => {
      const typical = hlc3(bars) as number[];
      const positive: Series = bars.map((bar, i) =>
        i > 0 && typical[i] > typical[i - 1]
          ? typical[i] * bar.volume
          : i > 0
            ? 0
            : null,
      );
      const negative: Series = bars.map((bar, i) =>
        i > 0 && typical[i] < typical[i - 1]
          ? typical[i] * bar.volume
          : i > 0
            ? 0
            : null,
      );
      const up = sma(positive, p.length);
      const down = sma(negative, p.length);
      return {
        value: combine(up, down, (a, b) =>
          a + b === 0 ? null : (100 * a) / (a + b),
        ),
      };
    },
  },
  {
    id: "cmf",
    name: { en: "Chaikin Money Flow", ko: "차이킨 자금흐름(CMF)" },
    short: "CMF",
    category: "volume",
    pane: "separate",
    inputs: [len(20)],
    outputs: [{ key: "value", label: "CMF", color: GREEN }],
    levels: [0],
    compute: (bars, p) => {
      const flow: Series = bars.map((bar) =>
        bar.high === bar.low
          ? 0
          : ((bar.close - bar.low - (bar.high - bar.close)) /
              (bar.high - bar.low)) *
            bar.volume,
      );
      return {
        value: ratio(
          sma(flow, p.length),
          sma(
            bars.map((bar) => bar.volume),
            p.length,
          ),
        ),
      };
    },
  },
  {
    id: "volma",
    name: { en: "Volume Moving Average", ko: "거래량 이동평균" },
    short: "Vol MA",
    category: "volume",
    pane: "separate",
    inputs: [len(20)],
    outputs: [
      { key: "volume", label: "Volume", color: GREY, kind: "histogram" },
      { key: "average", label: "MA", color: BLUE },
    ],
    compute: (bars, p) => {
      const volume = bars.map((bar) => bar.volume);
      return { volume, average: sma(volume, p.length) };
    },
  },
];

export function rsi(source: Series, length: number): Series {
  const delta = change(source);
  const gains = delta.map((value) =>
    value === null ? null : Math.max(value, 0),
  );
  const losses = delta.map((value) =>
    value === null ? null : Math.max(-value, 0),
  );
  const up = rma(gains, length);
  const down = rma(losses, length);
  return combine(up, down, (gain, loss) =>
    loss === 0 ? 100 : gain === 0 ? 0 : 100 - 100 / (1 + gain / loss),
  );
}

function stochastic(bars: OhlcvBar[], source: Series, length: number): Series {
  const high = highest(
    bars.map((bar) => bar.high),
    length,
  );
  const low = lowest(
    bars.map((bar) => bar.low),
    length,
  );
  return source.map((value, i) => {
    const top = high[i];
    const bottom = low[i];
    return value === null || top === null || bottom === null || top === bottom
      ? null
      : (100 * (value - bottom)) / (top - bottom);
  });
}

export const INDICATOR_BY_ID = new Map(
  INDICATORS.map((item) => [item.id, item]),
);

/** One indicator placed on one chart, with its own settings. */
/** Prices an indicator can read instead of the close (TradingView's "Source"). */
export const PRICE_SOURCES = [
  "close",
  "open",
  "high",
  "low",
  "hl2",
  "hlc3",
  "ohlc4",
] as const;
export type PriceSource = (typeof PRICE_SOURCES)[number];
export const LINE_STYLES = ["solid", "dashed", "dotted"] as const;
export type LineStyleName = (typeof LINE_STYLES)[number];

/** How one output is drawn; unset fields keep the definition's look. */
export interface OutputStyle {
  color?: string;
  /** 0–100 percent; 100 when unset. */
  opacity?: number;
  width?: number;
  line?: LineStyleName;
  visible?: boolean;
}

/** A horizontal guide level (e.g. RSI 70) as the user set it. */
export interface LevelStyle {
  value: number;
  color: string;
  visible: boolean;
}

export interface IndicatorStyle {
  outputs?: Record<string, OutputStyle>;
  /** Replaces the definition's levels when set. */
  levels?: LevelStyle[];
  /** Decimals in the legend and on the price scale; automatic when unset. */
  precision?: number;
}

export const MAX_PRECISION = 8;

/**
 * Chart timeframes an indicator shows on (TradingView's "Visibility"). A group left
 * out shows on all of its timeframes; `false` hides the indicator there.
 */
export const VISIBILITY_GROUPS = {
  minutes: { min: 1, max: 59 },
  hours: { min: 1, max: 24 },
  days: { min: 1, max: 366 },
  weeks: { min: 1, max: 52 },
} as const;
export type VisibilityGroup = keyof typeof VISIBILITY_GROUPS;
export type VisibilityRange = { from: number; to: number } | false;
export type IndicatorVisibility = Partial<
  Record<VisibilityGroup, VisibilityRange>
>;

/** The group and count a chart timeframe falls in: "45m" → minutes 45, "120m" → hours 2. */
export function timeframeGroup(
  timeframe: string,
): { group: VisibilityGroup; count: number } | null {
  if (timeframe === "1d") return { group: "days", count: 1 };
  if (timeframe === "1w") return { group: "weeks", count: 1 };
  const match = /^(\d+)m$/.exec(timeframe);
  if (!match) return null;
  const minutes = Number(match[1]);
  return minutes < 60 || minutes % 60 !== 0
    ? { group: "minutes", count: minutes }
    : { group: "hours", count: minutes / 60 };
}

/** Whether an indicator's visibility settings let it show on this chart timeframe. */
export function visibleOn(
  instance: Pick<IndicatorInstance, "visibility">,
  timeframe: string,
): boolean {
  const place = timeframeGroup(timeframe);
  if (!place || !instance.visibility) return true;
  const range = instance.visibility[place.group];
  if (range === undefined) return true;
  return (
    range !== false && place.count >= range.from && place.count <= range.to
  );
}

/** A colour with an opacity, for the chart ("#rrggbb" stays as it is at 100). */
export function withOpacity(color: string, opacity?: number): string {
  if (
    opacity === undefined ||
    opacity >= 100 ||
    !/^#[0-9a-fA-F]{6}$/.test(color)
  ) {
    return color;
  }
  const value = (offset: number) =>
    parseInt(color.slice(offset, offset + 2), 16);
  return `rgba(${value(1)}, ${value(3)}, ${value(5)}, ${Math.max(0, opacity) / 100})`;
}

export const DEFAULT_LEVEL_COLOR = "#4a566b";
export const MAX_LINE_WIDTH = 4;

export interface IndicatorInstance {
  key: string;
  type: string;
  params: Record<string, number>;
  hidden?: boolean;
  /** For indicators with `sourceInput`: the price read instead of the close. */
  source?: PriceSource;
  /** Colours, widths, line styles, visibility and levels (TradingView's "Style"). */
  style?: IndicatorStyle;
  /** Chart timeframes it shows on (TradingView's "Visibility"); all when unset. */
  visibility?: IndicatorVisibility;
  /** Computed on this larger timeframe instead of the chart's (built-ins, formulas). */
  timeframe?: HigherTimeframe;
  /** Formula indicators (type "formula"): the expression, its name and pane. */
  formula?: string;
  label?: string;
  pane?: "price" | "separate";
}

/** The definition behind an instance: built-in, Python, or the instance's formula. */
export function definitionFor(
  instance: IndicatorInstance,
): IndicatorDefinition | undefined {
  if (instance.type === "formula") {
    return formulaDefinition(
      instance.formula ?? "",
      instance.label ?? "Formula",
      instance.pane ?? "separate",
    );
  }
  return INDICATOR_BY_ID.get(instance.type);
}

export function defaultParams(
  definition: IndicatorDefinition,
): Record<string, number> {
  return Object.fromEntries(
    definition.inputs.map((item) => [item.key, item.default]),
  );
}

/** Settings from storage, with unknown keys dropped and bad numbers replaced. */
export function cleanParams(
  definition: IndicatorDefinition,
  params: unknown,
): Record<string, number> {
  const given = (
    typeof params === "object" && params !== null ? params : {}
  ) as Record<string, unknown>;
  return Object.fromEntries(
    definition.inputs.map((item) => {
      const value = given[item.key];
      return [
        item.key,
        typeof value === "number" &&
        Number.isFinite(value) &&
        value >= item.min &&
        value <= item.max
          ? value
          : item.default,
      ];
    }),
  );
}

const COLOR = /^#[0-9a-fA-F]{6}$/;

/** A saved style with unknown fields dropped and bad values left out. */
export function cleanStyle(value: unknown): IndicatorStyle | undefined {
  if (typeof value !== "object" || value === null) return undefined;
  const record = value as Record<string, unknown>;
  const style: IndicatorStyle = {};
  if (typeof record.outputs === "object" && record.outputs !== null) {
    const outputs: Record<string, OutputStyle> = {};
    for (const [key, raw] of Object.entries(
      record.outputs as Record<string, unknown>,
    )) {
      if (typeof raw !== "object" || raw === null || key.length > 40) continue;
      const item = raw as Record<string, unknown>;
      const out: OutputStyle = {};
      if (typeof item.color === "string" && COLOR.test(item.color)) {
        out.color = item.color;
      }
      if (
        typeof item.opacity === "number" &&
        Number.isInteger(item.opacity) &&
        item.opacity >= 0 &&
        item.opacity <= 100
      ) {
        out.opacity = item.opacity;
      }
      if (
        typeof item.width === "number" &&
        Number.isInteger(item.width) &&
        item.width >= 1 &&
        item.width <= MAX_LINE_WIDTH
      ) {
        out.width = item.width;
      }
      if ((LINE_STYLES as readonly unknown[]).includes(item.line)) {
        out.line = item.line as LineStyleName;
      }
      if (typeof item.visible === "boolean") out.visible = item.visible;
      if (Object.keys(out).length > 0) outputs[key] = out;
    }
    if (Object.keys(outputs).length > 0) style.outputs = outputs;
  }
  if (Array.isArray(record.levels)) {
    style.levels = record.levels.slice(0, 10).flatMap((raw): LevelStyle[] => {
      if (typeof raw !== "object" || raw === null) return [];
      const item = raw as Record<string, unknown>;
      return typeof item.value === "number" && Number.isFinite(item.value)
        ? [
            {
              value: item.value,
              color:
                typeof item.color === "string" && COLOR.test(item.color)
                  ? item.color
                  : DEFAULT_LEVEL_COLOR,
              visible: item.visible !== false,
            },
          ]
        : [];
    });
  }
  if (
    typeof record.precision === "number" &&
    Number.isInteger(record.precision) &&
    record.precision >= 0 &&
    record.precision <= MAX_PRECISION
  ) {
    style.precision = record.precision;
  }
  return Object.keys(style).length > 0 ? style : undefined;
}

/** Saved visibility with unknown groups dropped and ranges kept inside their limits. */
export function cleanVisibility(
  value: unknown,
): IndicatorVisibility | undefined {
  if (typeof value !== "object" || value === null) return undefined;
  const record = value as Record<string, unknown>;
  const out: IndicatorVisibility = {};
  for (const [group, limits] of Object.entries(VISIBILITY_GROUPS) as [
    VisibilityGroup,
    { min: number; max: number },
  ][]) {
    const raw = record[group];
    if (raw === false) {
      out[group] = false;
      continue;
    }
    if (typeof raw !== "object" || raw === null) continue;
    const item = raw as Record<string, unknown>;
    const clamp = (n: unknown, fallback: number) =>
      typeof n === "number" && Number.isInteger(n)
        ? Math.min(limits.max, Math.max(limits.min, n))
        : fallback;
    const from = clamp(item.from, limits.min);
    const to = clamp(item.to, limits.max);
    if (from <= to) out[group] = { from, to };
  }
  return Object.keys(out).length > 0 ? out : undefined;
}

/** Source and style kept from storage (shared by every kind of indicator). */
function savedLook(
  record: Record<string, unknown>,
  definition?: IndicatorDefinition,
): Pick<IndicatorInstance, "source" | "style" | "visibility"> {
  const style = cleanStyle(record.style);
  const visibility = cleanVisibility(record.visibility);
  const source =
    definition?.sourceInput &&
    (PRICE_SOURCES as readonly unknown[]).includes(record.source) &&
    record.source !== "close"
      ? (record.source as PriceSource)
      : undefined;
  return {
    ...(source ? { source } : {}),
    ...(style ? { style } : {}),
    ...(visibility ? { visibility } : {}),
  };
}

export function parseIndicators(value: unknown): IndicatorInstance[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item): IndicatorInstance[] => {
    if (typeof item !== "object" || item === null) return [];
    const record = item as Record<string, unknown>;
    if (
      record.type === "formula" &&
      typeof record.formula === "string" &&
      record.formula.length <= MAX_FORMULA_LENGTH &&
      typeof record.key === "string" &&
      record.key.length > 0
    ) {
      return [
        {
          key: record.key,
          type: "formula",
          params: {},
          formula: record.formula,
          label:
            typeof record.label === "string" && record.label.trim()
              ? record.label.trim().slice(0, 40)
              : "Formula",
          pane: record.pane === "price" ? "price" : "separate",
          ...(isHigherTimeframe(record.timeframe)
            ? { timeframe: record.timeframe }
            : {}),
          ...(record.hidden === true ? { hidden: true } : {}),
          ...savedLook(record),
        },
      ];
    }
    if (
      typeof record.type === "string" &&
      record.type.startsWith("py:") &&
      !INDICATOR_BY_ID.has(record.type) &&
      typeof record.key === "string" &&
      record.key.length > 0
    ) {
      // A Python indicator whose definition loads from the API later: keep it as saved.
      const params = Object.fromEntries(
        Object.entries(
          typeof record.params === "object" && record.params !== null
            ? (record.params as Record<string, unknown>)
            : {},
        ).filter(
          (entry): entry is [string, number] =>
            typeof entry[1] === "number" && Number.isFinite(entry[1]),
        ),
      );
      return [
        {
          key: record.key,
          type: record.type,
          params,
          ...(record.hidden === true ? { hidden: true } : {}),
          ...savedLook(record),
        },
      ];
    }
    const definition =
      typeof record.type === "string"
        ? INDICATOR_BY_ID.get(record.type)
        : undefined;
    if (
      !definition ||
      typeof record.key !== "string" ||
      record.key.length === 0
    ) {
      return [];
    }
    return [
      {
        key: record.key,
        type: definition.id,
        params: cleanParams(definition, record.params),
        // Python indicators are computed by the API on the chart's own bars only.
        ...(isHigherTimeframe(record.timeframe) &&
        !definition.id.startsWith("py:")
          ? { timeframe: record.timeframe }
          : {}),
        ...(record.hidden === true ? { hidden: true } : {}),
        ...savedLook(record, definition),
      },
    ];
  });
}

/** The params as the legend shows them, e.g. "14" or "12, 26, 9". */
export function paramsText(
  definition: IndicatorDefinition,
  params: Record<string, number>,
): string {
  return definition.inputs.map((item) => params[item.key]).join(", ");
}

// ---------------------------------------------------------------------------------
// Chart-ready series
// ---------------------------------------------------------------------------------

export interface RenderedPoint {
  time: number;
  value: number;
  color?: string;
}

export interface RenderedLine {
  id: string;
  label: string;
  color: string;
  kind: "line" | "histogram";
  data: RenderedPoint[];
  width: number;
  line: LineStyleName;
  visible: boolean;
}

export interface RenderedIndicator {
  key: string;
  instance: IndicatorInstance;
  definition: IndicatorDefinition;
  params: Record<string, number>;
  hidden: boolean;
  lines: RenderedLine[];
  /** Guide levels to draw (the user's, else the definition's). */
  levels: LevelStyle[];
  /** Decimals chosen in the style; automatic when undefined. */
  precision?: number;
  /** Value of each output at every bar index, for the legend under the crosshair. */
  values: Record<string, Series>;
}

const HISTOGRAM_UP = "rgba(63, 182, 139, 0.6)";
const HISTOGRAM_DOWN = "rgba(229, 86, 107, 0.6)";

/** Most earlier bars any indicator needs; long averages also need time to settle. */
export const MAX_WARMUP_BARS = 1_000;

/**
 * How many bars before the shown ones the indicators should see, so their values are
 * settled from the first shown bar: three times the longest look-back (exponential
 * averages need about that long to forget their seed), capped at MAX_WARMUP_BARS.
 */
/** How many earlier bars one indicator reads (its longest look-back). */
export function instanceLookback(instance: IndicatorInstance): number {
  if (instance.type === "formula") {
    try {
      return formulaLookback(parseFormula(instance.formula ?? ""));
    } catch {
      return 0; // an invalid formula draws nothing and needs no warm-up
    }
  }
  const definition = INDICATOR_BY_ID.get(instance.type);
  if (!definition) return 0;
  let longest = 0;
  let total = 0;
  for (const item of definition.inputs) {
    const value = instance.params[item.key];
    if (item.lookback === false || item.step !== undefined) continue;
    if (Number.isInteger(value)) {
      longest = Math.max(longest, value);
      total += value;
    }
  }
  // Ichimoku's leading span B looks back its own length plus the displacement.
  return definition.id === "ichimoku" ? Math.max(longest, total) : longest;
}

export function warmupBars(instances: IndicatorInstance[]): number {
  let longest = 0;
  for (const instance of instances) {
    // Larger-timeframe indicators warm up on their own larger bars.
    if (instance.timeframe) continue;
    longest = Math.max(longest, instanceLookback(instance));
  }
  return Math.min(MAX_WARMUP_BARS, longest * 3);
}

/**
 * Chart series for each indicator. `displayFrom` is the index of the first bar to
 * draw: bars before it only warm the formulas up and are never drawn.
 */
/** Bars whose close is replaced by `source`, for indicators that read closes only. */
export function withSource(bars: OhlcvBar[], source?: PriceSource): OhlcvBar[] {
  if (!source || source === "close") return bars;
  const price = (bar: OhlcvBar): number => {
    switch (source) {
      case "open":
        return bar.open;
      case "high":
        return bar.high;
      case "low":
        return bar.low;
      case "hl2":
        return (bar.high + bar.low) / 2;
      case "hlc3":
        return (bar.high + bar.low + bar.close) / 3;
      case "ohlc4":
        return (bar.open + bar.high + bar.low + bar.close) / 4;
    }
  };
  return bars.map((bar) => ({ ...bar, close: price(bar) }));
}

/** The levels an indicator draws: the user's when set, else the definition's. */
export function levelsOf(
  definition: IndicatorDefinition,
  instance: IndicatorInstance,
): LevelStyle[] {
  return (
    instance.style?.levels ??
    (definition.levels ?? []).map((value) => ({
      value,
      color: DEFAULT_LEVEL_COLOR,
      visible: true,
    }))
  );
}

export function renderIndicators(
  bars: OhlcvBar[],
  instances: IndicatorInstance[],
  displayFrom = 0,
  context?: IndicatorContext,
  /** Larger-timeframe bars by timeframe, and each chart bar's end (epoch seconds). */
  higher?: {
    bars: Partial<Record<HigherTimeframe, HigherBars>>;
    ends: number[];
  },
): RenderedIndicator[] {
  return instances.flatMap((instance) => {
    const definition = definitionFor(instance);
    if (!definition) return [];
    let values: Record<string, Series>;
    const priced = (input: OhlcvBar[]) =>
      definition.sourceInput ? withSource(input, instance.source) : input;
    if (instance.timeframe) {
      const source = higher?.bars[instance.timeframe];
      values = source
        ? alignToChart(
            definition.compute(priced(source.bars), instance.params, context),
            source.availableAt,
            higher!.ends,
          )
        : Object.fromEntries(
            definition.outputs.map((output) => [
              output.key,
              bars.map(() => null),
            ]),
          );
    } else {
      values = definition.compute(priced(bars), instance.params, context);
    }
    const lines = definition.outputs.map((output) => {
      const kind = output.kind ?? "line";
      const signed = kind === "histogram" && output.key !== "volume";
      const data: RenderedPoint[] = [];
      values[output.key].forEach((value, i) => {
        if (value === null || i < displayFrom) return;
        data.push({
          time: bars[i].time,
          value,
          ...(signed
            ? { color: value >= 0 ? HISTOGRAM_UP : HISTOGRAM_DOWN }
            : {}),
        });
      });
      const look = instance.style?.outputs?.[output.key] ?? {};
      return {
        id: `${instance.key}:${output.key}`,
        label: output.label,
        color: withOpacity(look.color ?? output.color, look.opacity),
        kind,
        data,
        width: look.width ?? 1,
        line: look.line ?? "solid",
        visible: look.visible ?? true,
      };
    });
    return [
      {
        key: instance.key,
        instance,
        definition,
        params: instance.params,
        hidden: instance.hidden === true,
        lines,
        levels: levelsOf(definition, instance),
        ...(instance.style?.precision === undefined
          ? {}
          : { precision: instance.style.precision }),
        values,
      },
    ];
  });
}
