import type { PriceBar } from "@siw/contracts/types";

import type { OhlcvBar, Series } from "./indicators";

/**
 * Indicators computed on a larger timeframe than the chart's (e.g. a daily SMA on a
 * 5-minute chart). Only final larger bars are used, and a chart bar sees a larger
 * bar's value only once that bar had been published by the chart bar's end, so the
 * line steps forward when a day (or week) closes and never shows a value early.
 */
export const HIGHER_TIMEFRAMES = ["1d", "1w"] as const;
export type HigherTimeframe = (typeof HIGHER_TIMEFRAMES)[number];

export function higherChoices(chartTimeframe: string): HigherTimeframe[] {
  if (chartTimeframe === "1w") return [];
  if (chartTimeframe === "1d") return ["1w"];
  return ["1d", "1w"];
}

export const isHigherTimeframe = (value: unknown): value is HigherTimeframe =>
  value === "1d" || value === "1w";

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
