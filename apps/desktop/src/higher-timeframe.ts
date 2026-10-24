import type { PriceBar } from "@siw/contracts/types";

import type { OhlcvBar, Series } from "./indicators";

/**
 * Indicators computed on a larger timeframe than the chart's (e.g. a daily SMA on a
 * 5-minute chart). Only final larger bars are used, and a chart bar sees a larger
 * bar's value only once that bar had been published by the chart bar's end, so the
 * line steps forward when a day (or week) closes and never shows a value early.
 */
/** Larger intraday timeframes: session buckets, like the chart's own (ADR 0034). */
export const INTRADAY_HIGHER = ["15m", "30m", "60m", "120m", "240m"] as const;
/**
 * Smaller intraday timeframes (ADR 0039), e.g. a 1-minute EMA on a 5-minute chart:
 * each chart bar shows the value of the last smaller bar inside it.
 */
export const INTRADAY_LOWER = ["1m", "2m", "3m", "5m", "10m"] as const;
/** Smaller bars per chart bar at most (a 1-minute indicator up to a 60-minute chart). */
export const MAX_LOWER_RATIO = 60;
export const HIGHER_TIMEFRAMES = [
  ...INTRADAY_LOWER,
  ...INTRADAY_HIGHER,
  "1d",
  "1w",
] as const;
/** A timeframe an indicator can be computed on other than the chart's own. */
export type HigherTimeframe = (typeof HIGHER_TIMEFRAMES)[number];
export type IntradayHigher =
  (typeof INTRADAY_HIGHER)[number] | (typeof INTRADAY_LOWER)[number];

/** Minutes of an intraday timeframe such as "5m"; null for daily and weekly. */
export function minutesOf(timeframe: string): number | null {
  const match = /^(\d+)m$/.exec(timeframe);
  return match ? Number(match[1]) : null;
}

export function higherChoices(chartTimeframe: string): HigherTimeframe[] {
  if (chartTimeframe === "1w") return [];
  if (chartTimeframe === "1d") return ["1w"];
  const chart = minutesOf(chartTimeframe) ?? 1;
  return [
    ...INTRADAY_HIGHER.filter((choice) => (minutesOf(choice) ?? 0) > chart),
    "1d",
    "1w",
  ];
}

/** Smaller timeframes that divide the chart's evenly, for intraday charts only. */
export function lowerChoices(chartTimeframe: string): HigherTimeframe[] {
  const chart = minutesOf(chartTimeframe);
  if (chart === null) return []; // daily and weekly: too many minute bars
  return INTRADAY_LOWER.filter((choice) => {
    const minutes = minutesOf(choice) ?? chart;
    return (
      minutes < chart &&
      chart % minutes === 0 &&
      chart / minutes <= MAX_LOWER_RATIO
    );
  });
}

export const isHigherTimeframe = (value: unknown): value is HigherTimeframe =>
  (HIGHER_TIMEFRAMES as readonly unknown[]).includes(value);

export const isIntradayHigher = (
  value: HigherTimeframe,
): value is IntradayHigher => minutesOf(value) !== null;

/**
 * Sessions of larger intraday bars to fetch: the chart's own sessions plus enough
 * earlier ones to warm up the longest look-back (three times it, as for daily bars).
 */
export function intradaySessions(
  timeframe: IntradayHigher,
  lookback: number,
  shownSessions: number,
  extended: boolean,
): number {
  const perSession = Math.ceil(
    (extended ? 960 : 390) / (minutesOf(timeframe) ?? 60),
  );
  // The session list API returns at most 120 sessions.
  return Math.min(
    120,
    shownSessions + Math.ceil((lookback * 3 + 5) / perSession) + 1,
  );
}

export interface HigherBars {
  bars: OhlcvBar[];
  /** When each bar became available (epoch seconds), aligned with `bars`. */
  availableAt: number[];
}

const seconds = (utc: string) => Math.floor(Date.parse(utc) / 1000);

/** Final larger bars only, oldest first. */
export function higherBarsOf(bars: PriceBar[]): HigherBars {
  const final = bars
    .filter((bar) => bar.is_final)
    .sort((a, b) => a.interval_start_utc.localeCompare(b.interval_start_utc));
  return {
    bars: final.map((bar) => ({
      time: seconds(bar.interval_start_utc),
      open: Number(bar.open),
      high: Number(bar.high),
      low: Number(bar.low),
      close: Number(bar.close),
      volume: Number(bar.volume),
    })),
    availableAt: final.map((bar) =>
      Math.max(seconds(bar.interval_end_utc), seconds(bar.available_at_utc)),
    ),
  };
}

/**
 * Larger-timeframe values placed on chart bars: each chart bar takes the latest
 * larger bar available by its own end (`chartEnds`, epoch seconds).
 */
export function alignToChart(
  values: Record<string, Series>,
  availableAt: number[],
  chartEnds: number[],
): Record<string, Series> {
  const aligned: Record<string, Series> = {};
  for (const [key, series] of Object.entries(values)) {
    const out: Series = [];
    let j = -1;
    for (const end of chartEnds) {
      while (j + 1 < availableAt.length && availableAt[j + 1] <= end) j += 1;
      out.push(j >= 0 ? (series[j] ?? null) : null);
    }
    aligned[key] = out;
  }
  return aligned;
}

const DAY = 86_400;

/** The range of larger bars to fetch: the chart's span plus warm-up for `lookback`. */
export function higherRange(
  firstStart: number,
  lastEnd: number,
  lookback: number,
  timeframe: HigherTimeframe,
): { startUtc: string; endUtc: string } {
  // Calendar days per larger bar, with room for weekends and holidays.
  const span = timeframe === "1w" ? 7 * DAY : 1.6 * DAY;
  const start = firstStart - Math.ceil((lookback * 3 + 5) * span);
  const day = (value: number) => Math.floor(value / DAY) * DAY;
  return {
    startUtc: new Date(day(start) * 1000).toISOString().replace(".000Z", "Z"),
    endUtc: new Date(lastEnd * 1000).toISOString().replace(".000Z", "Z"),
  };
}
