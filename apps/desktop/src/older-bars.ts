import type { PriceBar } from "@siw/contracts/types";
import { dailyLookbackDays, DEFAULT_CANDLES } from "./timeframes";

/** A chart loads at most this many bars before its default history, on request. */
export const MAX_OLDER_BARS = 10 * DEFAULT_CANDLES;
/** At least this near the first bar, scrolling or zooming out asks for older bars
 * (otherwise when less than one screen of bars is left before the view). */
export const OLDER_TRIGGER_BARS = 2;
/** Sessions without a single bar (a trading halt, a thin stock) skipped in one request
 * for older bars before it says there are none (about three months). */
export const MAX_EMPTY_SESSIONS = 65;
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

/**
 * The next batch of older intraday sessions before `before` (a session date), oldest
 * first. A batch whose sessions all answered with no bars (a halt) is skipped: with
 * `lastTradeBefore` (the last session date with a daily bar before a date, or null when
 * there is none) the search jumps straight to that day; without it, the batch before is
 * asked for, up to MAX_EMPTY_SESSIONS. `from` is the earliest session covered; `failed`
 * means a session could not be read, so the run stops there.
 */
export async function olderSessionBars<S extends { session_date: string }>(
  before: string,
  batch: number,
  sessionsBefore: (endDate: string, count: number) => Promise<S[]>,
  barsOf: (session: S) => Promise<PriceBar[] | null>,
  lastTradeBefore?: (date: string) => Promise<string | null>,
): Promise<{ bars: PriceBar[]; from: string | null; failed: boolean }> {
  let cursor = before;
  let skipped = 0;
  let from: string | null = null;
  for (;;) {
    const sessions = await sessionsBefore(previousDay(cursor), batch);
    if (sessions.length === 0) return { bars: [], from, failed: false };
    const answers = await Promise.all(sessions.map(barsOf));
    const run = contiguousFromNewest(answers);
    const failed = run.sessions < sessions.length;
    if (run.bars.length > 0 || failed) {
      return {
        bars: run.bars,
        from:
          run.sessions > 0
            ? sessions[sessions.length - run.sessions].session_date
            : from,
        failed,
      };
    }
    cursor = sessions[0].session_date;
    from = cursor;
    if (lastTradeBefore) {
      const last = await lastTradeBefore(cursor);
      if (last === null) return { bars: [], from, failed: false }; // the data starts
      cursor = shiftDays(last, 1); // the next batch ends with that trading day
      continue;
    }
    skipped += sessions.length;
    if (skipped >= MAX_EMPTY_SESSIONS) return { bars: [], from, failed: false };
  }
}

function previousDay(date: string): string {
  return shiftDays(date, -1);
}
