import type { PriceBar } from "@siw/contracts/types";

import type { BarsRequest } from "./market-api";
import { zoneFormat, zoneOf } from "./market-zone";
import type { ReviewTrade } from "./review-api";

const DAY_MS = 86_400_000;
/** Daily bars shown before a trade's first fill and after its last one. */
const DAILY_BEFORE_DAYS = 90;
const DAILY_AFTER_DAYS = 30;

function shiftDays(isoDate: string, days: number): string {
  return new Date(Date.parse(`${isoDate}T00:00:00Z`) + days * DAY_MS)
    .toISOString()
    .slice(0, 10);
}

/**
 * The bars a trade is drawn on: 5-minute bars for a trade inside one or two sessions whose
 * fills all have a time, daily bars around it otherwise. Bars after the exit are included
 * (hindsight) and the screen says so.
 *
 * Intraday bars come one UTC day per request (the API serves at most 1,440 minutes at a
 * time), from the first session day through the day after the last: a US session's
 * after-hours run past midnight UTC.
 */
export function tradeChartRequests(
  trade: ReviewTrade,
  today: string,
): BarsRequest[] {
  const days = trade.executions.map((item) => item.session_date).sort();
  const first = days[0] ?? trade.opened_on;
  const last = days[days.length - 1] ?? first;
  const timed = trade.executions.every((item) => item.executed_at_utc);
  const spanDays = (Date.parse(last) - Date.parse(first)) / DAY_MS;
  if (timed && spanDays <= 1) {
    const requests: BarsRequest[] = [];
    // Midnight UTC falls on a 5-minute bucket edge of both US and Korean sessions; the
    // API refuses a range that cuts a bucket.
    for (let day = first; day <= shiftDays(last, 1); day = shiftDays(day, 1)) {
      requests.push({
        instrumentId: trade.instrument_id,
        timeframe: "5m",
        startUtc: `${day}T00:00:00Z`,
        endUtc: `${shiftDays(day, 1)}T00:00:00Z`,
      });
    }
    return requests;
  }
  const end = shiftDays(last, DAILY_AFTER_DAYS + 1);
  return [
    {
      instrumentId: trade.instrument_id,
      timeframe: "1d",
      startUtc: `${shiftDays(first, -DAILY_BEFORE_DAYS)}T00:00:00Z`,
      endUtc: `${end < today ? end : shiftDays(today, 1)}T00:00:00Z`,
    },
  ];
}

/** Bars of several requests in time order, each once. */
export function joinBars(parts: PriceBar[][]): PriceBar[] {
  const byStart = new Map<string, PriceBar>();
  for (const bars of parts) {
    for (const bar of bars) byStart.set(bar.interval_start_utc, bar);
  }
  return [...byStart.values()].sort(
    (a, b) =>
      Date.parse(a.interval_start_utc) - Date.parse(b.interval_start_utc),
  );
}

export interface TradeMarker {
  time: number;
  position: "aboveBar" | "belowBar";
  shape: "arrowUp" | "arrowDown";
  color: string;
  text: string;
}

export const TRADE_COLORS = { buy: "#3fb68b", sell: "#e5566b" };

/** A daily bar's session date at its exchange. */
function barDay(bar: PriceBar, instrumentId: string): string {
  // en-CA formats dates as YYYY-MM-DD.
  return zoneFormat(zoneOf(instrumentId), "en-CA", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(bar.interval_start_utc));
}

/**
 * One marker per execution of the trade, on the bar that holds it: by time on intraday
 * bars, by session date on daily bars (or when only the day is known). An execution
 * with no drawn bar is left out.
 */
export function tradeMarkers(
  trade: ReviewTrade,
  bars: PriceBar[],
  daily: boolean,
): TradeMarker[] {
  const markers: TradeMarker[] = [];
  for (const item of trade.executions) {
    let bar: PriceBar | undefined;
    if (!daily && item.executed_at_utc) {
      const at = Date.parse(item.executed_at_utc);
      bar = bars.find(
        (candidate) =>
          Date.parse(candidate.interval_start_utc) <= at &&
          at < Date.parse(candidate.interval_end_utc),
      );
    } else {
      bar = bars.find(
        (candidate) =>
          barDay(candidate, trade.instrument_id) === item.session_date,
      );
    }
    if (!bar) continue;
    const buy = item.side === "buy";
    markers.push({
      time: Math.floor(Date.parse(bar.interval_start_utc) / 1000),
      position: buy ? "belowBar" : "aboveBar",
      shape: buy ? "arrowUp" : "arrowDown",
      color: buy ? TRADE_COLORS.buy : TRADE_COLORS.sell,
      text: `${buy ? "B" : "S"} ${item.quantity}@${item.price}`,
    });
  }
  // The chart library needs markers in time order.
  return markers.sort((a, b) => a.time - b.time);
}
