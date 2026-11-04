/**
 * The Toss stock rankings the Rankings panel offers (ADR 0049, official OpenAPI 1.2.28),
 * and the display helpers for their exact decimal strings.
 */
import type { StockRankingsResponse } from "@siw/contracts/types";

export type RankingKind = StockRankingsResponse["ranking"];
export type RankingDuration = NonNullable<StockRankingsResponse["duration"]>;
export type RankingInvestor = NonNullable<StockRankingsResponse["investor"]>;
export type RankingMarket = StockRankingsResponse["market"];
export type RankingRow = StockRankingsResponse["rows"][number];

export interface RankingRequest {
  ranking: RankingKind;
  market: RankingMarket;
  /** Price rankings only. */
  duration?: RankingDuration;
  /** Investor rankings (NET_BUY, NET_SELL) only. */
  investor?: RankingInvestor;
  excludeCaution?: boolean;
  count?: number;
}

export const RANKING_KINDS: readonly RankingKind[] = [
  "MARKET_TRADING_AMOUNT",
  "MARKET_TRADING_VOLUME",
  "TOP_GAINERS",
  "TOP_LOSERS",
  "TOSS_SECURITIES_TRADING_AMOUNT",
  "TOSS_SECURITIES_TRADING_VOLUME",
  "NET_BUY",
  "NET_SELL",
];
export const RANKING_DURATIONS: readonly RankingDuration[] = [
  "realtime",
  "1d",
  "1w",
  "1mo",
  "3mo",
  "6mo",
  "1y",
];
export const RANKING_INVESTORS: readonly RankingInvestor[] = [
  "foreigner",
  "institution",
  "individual",
];
export const RANKING_COUNTS = [20, 50, 100] as const;

/** Korean investors' net buying or selling: KR only, latest business day, no duration. */
export function isInvestorRanking(kind: RankingKind): boolean {
  return kind === "NET_BUY" || kind === "NET_SELL";
}

/** Toss has no realtime gainers or losers (400 unsupported-ranking-duration). */
export function supportsRealtime(kind: RankingKind): boolean {
  return kind !== "TOP_GAINERS" && kind !== "TOP_LOSERS";
}

/** Which value a ranking is ordered by, shown as its last column. */
export function rankedValue(
  kind: RankingKind,
): "trading_amount" | "trading_volume" | "net_amount" {
  if (isInvestorRanking(kind)) return "net_amount";
  return kind.endsWith("_VOLUME") ? "trading_volume" : "trading_amount";
}

/** A decimal string with thousands separators; digits are never changed. */
export function groupDigits(value: string): string {
  const [whole, fraction] = value.split(".");
  const sign = whole.startsWith("-") ? "-" : "";
  const digits = sign ? whole.slice(1) : whole;
  const grouped = digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${sign}${grouped}${fraction !== undefined ? `.${fraction}` : ""}`;
}

/** A decimal ratio ("0.0125") as a signed percentage ("+1.25%"), for display only. */
export function ratioPercent(value: string): { text: string; rising: boolean } {
  const percent = Number(value) * 100;
  return {
    text: `${percent > 0 ? "+" : ""}${percent.toFixed(2)}%`,
    rising: percent >= 0,
  };
}
