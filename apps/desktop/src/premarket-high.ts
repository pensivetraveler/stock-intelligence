import type { PriceBar, SessionWindow } from "@siw/contracts/types";

/**
 * The pre-market on daily charts (ADR 0041). Daily bars cover the regular session
 * only, so the pre-market (04:00–09:30 ET) comes from its own request: one "960m"
 * bucket per session, which the API cuts at the session boundary, so each request
 * for a session's pre-market window answers with one `pre` bar. It is drawn as its
 * own candle and never merged into the daily bar.
 */

/** Newest sessions whose pre-market is fetched (one request each). */
export const PREMARKET_SESSIONS = 60;
/** A bucket longer than any session, so the API returns the whole pre-market as one bar. */
export const PREMARKET_TIMEFRAME = "960m";

export interface PremarketBar {
  /** Start of the daily bar it belongs to (the regular open), epoch seconds. */
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
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
 * Pre-market bars placed on daily bars, oldest first. A `pre` bar ends at the
 * regular open, which is where that session's daily bar starts; bars that match no
 * shown daily bar are dropped.
 */
export function premarketBars(
  bars: PriceBar[],
  dailyTimes: number[],
): PremarketBar[] {
  const shown = new Set(dailyTimes);
  const byTime = new Map<number, PremarketBar>();
  for (const bar of bars) {
    if (bar.session !== "pre") continue;
    const time = seconds(bar.interval_end_utc);
    if (!shown.has(time)) continue;
    byTime.set(time, {
      time,
      open: Number(bar.open),
      high: Number(bar.high),
      low: Number(bar.low),
      close: Number(bar.close),
      final: bar.is_final,
    });
  }
  return [...byTime.values()].sort((a, b) => a.time - b.time);
}
