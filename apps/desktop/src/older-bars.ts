import type { PriceBar } from "@siw/contracts/types";
import { dailyLookbackDays, DEFAULT_CANDLES } from "./timeframes";

/** A chart loads at most this many bars before its default history, on request. */
export const MAX_OLDER_BARS = 10 * DEFAULT_CANDLES;
/** At least this near the first bar, scrolling or zooming out asks for older bars
 * (otherwise when less than one screen of bars is left before the view). */
export const OLDER_TRIGGER_BARS = 2;
/** A view change counts as the user's within this long after their wheel or drag. */
export const USER_MOVE_MS = 800;

function shiftDays(date: string, days: number): string {
  const value = new Date(`${date}T00:00:00Z`);
  value.setUTCDate(value.getUTCDate() + days);
  return value.toISOString().slice(0, 10);
}

/**
 * The daily or weekly request just before `beforeDate` (the start of what is shown),
 * as long as the first load's, so the two never overlap.
 */
export function olderDailyRange(
  timeframe: string,
  beforeDate: string,
): { startUtc: string; endUtc: string } {
  return {
    startUtc: `${shiftDays(beforeDate, -dailyLookbackDays(timeframe))}T00:00:00Z`,
    endUtc: `${beforeDate}T00:00:00Z`,
  };
}

/**
 * Older bars put in front of the ones loaded before them. Only bars that start before
 * `firstShown` (the first bar on the chart) are kept, so an overlapping answer never
 * doubles a bar.
 */
export function prependOlder(
  loaded: PriceBar[],
  older: PriceBar[],
  firstShown: string | null,
): PriceBar[] {
  const first = loaded[0]?.interval_start_utc ?? firstShown;
  if (first === null) return [...older, ...loaded];
  return [...older.filter((bar) => bar.interval_start_utc < first), ...loaded];
}

/**
 * Sessions answered so far, oldest first, kept only while they run back unbroken from
 * the newest one: a session that failed stops the run, so no gap is drawn over.
 */
export function contiguousFromNewest<T>(answers: (T[] | null)[]): {
  bars: T[];
  sessions: number;
} {
  const kept: T[][] = [];
  for (let i = answers.length - 1; i >= 0; i -= 1) {
    const answer = answers[i];
    if (answer === null) break;
    kept.unshift(answer);
  }
  return { bars: kept.flat(), sessions: kept.length };
}
