import type { MarketBarsResponse, PriceBar } from "@siw/contracts/types";
import { US_ZONE, zoneFormat, type MarketZone } from "./market-zone";

import { translateDynamic, translator, type Translate } from "./i18n";

export interface CandlePoint {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  color?: string;
  borderColor?: string;
  wickColor?: string;
}

export interface VolumePoint {
  time: number;
  value: number;
  color: string;
}

export const COLORS = {
  up: "#3fb68b",
  down: "#e5566b",
  uncertain: "#8a94a6",
  volumeUp: "rgba(63, 182, 139, 0.35)",
  volumeDown: "rgba(229, 86, 107, 0.35)",
  volumeUncertain: "rgba(138, 148, 166, 0.35)",
};

function epochSeconds(utc: string): number {
  return Math.floor(Date.parse(utc) / 1000);
}

/** Bars that are not final (partial or provisional) are drawn grey, never as settled. */
export function isUncertain(bar: PriceBar): boolean {
  return !bar.is_final || bar.quality !== "complete";
}

/**
 * Chart series for display only. Prices become JS numbers here for rendering;
 * calculations elsewhere keep the contract's decimal strings.
 */
export function toSeries(bars: PriceBar[]): {
  candles: CandlePoint[];
  volume: VolumePoint[];
} {
  const candles: CandlePoint[] = [];
  const volume: VolumePoint[] = [];
  for (const bar of bars) {
    const time = epochSeconds(bar.interval_start_utc);
    const open = Number(bar.open);
    const close = Number(bar.close);
    const rising = close >= open;
    const uncertain = isUncertain(bar);
    const candle: CandlePoint = {
      time,
      open,
      high: Number(bar.high),
      low: Number(bar.low),
      close,
    };
    if (uncertain) {
      candle.color = COLORS.uncertain;
      candle.borderColor = COLORS.uncertain;
      candle.wickColor = COLORS.uncertain;
    }
    candles.push(candle);
    volume.push({
      time,
      value: Number(bar.volume),
      color: uncertain
        ? COLORS.volumeUncertain
        : rising
          ? COLORS.volumeUp
          : COLORS.volumeDown,
    });
  }
  return { candles, volume };
}

export interface ResponseSummary {
  coverage: MarketBarsResponse["coverage"]["coverage_status"];
  intervals: string;
  missing: string[];
  quality: string;
  sessions: string;
  lineage: string;
}

const TIME_OPTIONS: Intl.DateTimeFormatOptions = {
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
};

/** "09:30 ET" or "09:00 KST": exchange-local time of an instant. */
export function exchangeTime(
  utc: string | number,
  zone: MarketZone = US_ZONE,
): string {
  const date = typeof utc === "number" ? new Date(utc * 1000) : new Date(utc);
  return `${zoneFormat(zone, "en-US", TIME_OPTIONS).format(date)} ${zone.label}`;
}

export function summarize(
  response: MarketBarsResponse,
  t: Translate = translator("en"),
  zone: MarketZone = US_ZONE,
): ResponseSummary {
  const { bars, coverage } = response;
  const counts = new Map<string, number>();
  const sessions = new Map<string, number>();
  for (const bar of bars) {
    if (isUncertain(bar)) {
      const quality = translateDynamic(
        t,
        `quality.${bar.quality}`,
        bar.quality,
      );
      const label = bar.is_final ? quality : t("summary.notFinal", { quality });
      counts.set(label, (counts.get(label) ?? 0) + 1);
    }
    sessions.set(bar.session, (sessions.get(bar.session) ?? 0) + 1);
  }
  const uncertain = [...counts].map(([label, count]) => `${count} ${label}`);
  return {
    coverage: coverage.coverage_status,
    intervals: t("summary.intervals", {
      observed: coverage.observed_interval_count,
      expected: coverage.expected_interval_count,
      timeframe: coverage.timeframe,
    }),
    missing: coverage.missing_intervals.map(
      (gap) =>
        `${exchangeTime(gap.start_utc, zone)}–${exchangeTime(gap.end_utc, zone)} (${translateDynamic(t, `reason.${gap.reason}`, gap.reason.replaceAll("_", " "))})`,
    ),
    quality: uncertain.length ? uncertain.join(", ") : t("summary.allFinal"),
    sessions:
      [...sessions]
        .map(
          ([session, count]) =>
            `${translateDynamic(t, `session.${session}`, session)} ${count}`,
        )
        .join(" · ") || t("summary.noBars"),
    lineage: `${coverage.source_id} · ${coverage.source_feed_scope} · ${coverage.adjustment} · ${coverage.dataset_revision} · ${coverage.verification_status}`,
  };
}
