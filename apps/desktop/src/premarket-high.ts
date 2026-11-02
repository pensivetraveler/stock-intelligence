import type { PriceBar, SessionWindow } from "@siw/contracts/types";

/**
 * The pre-market on daily charts (ADR 0041). Daily bars cover the regular session
 * only, so the pre-market (04:00–09:30 ET) comes from its own request: one "960m"
 * bucket per session, which the API cuts at the session boundary, so each request
 * for a session's pre-market window answers with one `pre` bar. It is drawn as its
 * own candle and never merged into the daily bar.
 *
 * Pre-market bars are as traded, because no source here adjusts minute bars. Over
 * split-adjusted daily candles they are scaled by that session's own ratio, so a
 * candle before a split sits with its daily candle (ADR 0041, 2026-11-02).
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
  /** Set when a split moved this candle away from what traded. */
  adjusted?: true;
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

/** Adjusted prices come rounded to cents, so a smaller gap is rounding, not a split. */
const ROUNDING = 0.01;

/**
 * What each session's traded prices are multiplied by to match its adjusted daily
 * bar, by the bar's start. A session missing from either list has no factor.
 */
export function splitFactors(
  adjusted: PriceBar[],
  asTraded: PriceBar[],
): Map<number, number> {
  const traded = new Map(
    asTraded.map((bar) => [bar.interval_start_utc, Number(bar.close)]),
  );
  const factors = new Map<number, number>();
  for (const bar of adjusted) {
    const close = traded.get(bar.interval_start_utc);
    if (!close) continue;
    const adjusted = Number(bar.close);
    factors.set(
      seconds(bar.interval_start_utc),
      Math.abs(adjusted - close) < ROUNDING ? 1 : adjusted / close,
    );
  }
  return factors;
}

/**
 * Pre-market bars placed on daily bars, oldest first. A `pre` bar ends at the
 * regular open, which is where that session's daily bar starts; bars that match no
 * shown daily bar are dropped. With `factors` (the daily candles are adjusted), so are
 * bars whose factor is not known: an unscaled candle would sit at the wrong price.
 */
export function premarketBars(
  bars: PriceBar[],
  dailyTimes: number[],
  factors: Map<number, number> | null = null,
): PremarketBar[] {
  const shown = new Set(dailyTimes);
  const byTime = new Map<number, PremarketBar>();
  for (const bar of bars) {
    if (bar.session !== "pre") continue;
    const time = seconds(bar.interval_end_utc);
    if (!shown.has(time)) continue;
    const factor = factors ? factors.get(time) : 1;
    if (factor === undefined) continue;
    byTime.set(time, {
      time,
      open: Number(bar.open) * factor,
      high: Number(bar.high) * factor,
      low: Number(bar.low) * factor,
      close: Number(bar.close) * factor,
      final: bar.is_final,
      ...(factor === 1 ? {} : { adjusted: true as const }),
    });
  }
  return [...byTime.values()].sort((a, b) => a.time - b.time);
}
