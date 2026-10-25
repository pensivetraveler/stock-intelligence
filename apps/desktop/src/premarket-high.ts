import type { PriceBar, SessionWindow } from "@siw/contracts/types";

/**
 * The pre-market high on daily charts (ADR 0041). Daily bars cover the regular
 * session only, so the pre-market (04:00–09:30 ET) high comes from its own request:
 * one "960m" bucket per session, which the API cuts at the session boundary, so each
 * request for a session's pre-market window answers with one `pre` bar. Its high is
 * never merged into the daily bar.
 */

/** Newest sessions whose pre-market high is fetched (one request each). */
export const PREMARKET_SESSIONS = 60;
/** A bucket longer than any session, so the API returns the whole pre-market as one bar. */
export const PREMARKET_TIMEFRAME = "960m";

export interface PremarketHigh {
  /** Start of the daily bar it belongs to (the regular open), epoch seconds. */
  time: number;
  high: number;
  /** False while the pre-market is still running or its bar is not yet final. */
  final: boolean;
}

/** The pre-market window of a trading session; null when it has none. */
export function premarketRange(
  window: SessionWindow,
): { startUtc: string; endUtc: string } | null {
  return window.is_trading_day && window.pre_open_utc && window.regular_open_utc
    ? { startUtc: window.pre_open_utc, endUtc: window.regular_open_utc }
    : null;
}

const seconds = (utc: string) => Math.floor(Date.parse(utc) / 1000);

/**
 * Pre-market highs placed on daily bars, oldest first. A `pre` bar ends at the
 * regular open, which is where that session's daily bar starts; bars that match no
 * shown daily bar are dropped.
 */
export function premarketHighs(
  bars: PriceBar[],
  dailyTimes: number[],
): PremarketHigh[] {
  const shown = new Set(dailyTimes);
  const byTime = new Map<number, PremarketHigh>();
  for (const bar of bars) {
    if (bar.session !== "pre") continue;
    const time = seconds(bar.interval_end_utc);
    if (!shown.has(time)) continue;
    byTime.set(time, {
      time,
      high: Number(bar.high),
      final: bar.is_final,
    });
  }
  return [...byTime.values()].sort((a, b) => a.time - b.time);
}
