import type { FilingSplitsResponse } from "@siw/contracts/types";

import type { Translate } from "./i18n";
import type { ChartPriceLevel } from "./PriceChart";

const ALPACA = "alpaca-corporate-actions";

/**
 * Prices with a filing date drawn on a split-adjusted chart (ADR 0052 C, user decision
 * 2026-11-20): scaled by Alpaca's splits after that date, the splits the candles are
 * adjusted by. A reverse split 1:58 multiplies by 58. Without Alpaca's splits the line
 * keeps its price and says the splits are unknown. An as-traded chart (a replay) and a
 * level without a date are left as they are.
 */
export function levelsOnChart(
  levels: ChartPriceLevel[] | undefined,
  adjustment: string | undefined,
  splits: FilingSplitsResponse | null,
  t: Translate,
): ChartPriceLevel[] | undefined {
  if (!levels || adjustment !== "split_adjusted") return levels;
  return levels.map((level) => {
    if (!level.asOf) return level;
    if (!splits || splits.compared_with !== ALPACA) {
      return { ...level, title: `${level.title} ${t("levels.splitsUnknown")}` };
    }
    let factor = 1;
    for (const event of splits.events) {
      const split = event.provider;
      if (!split || split.on <= level.asOf) continue;
      factor *= split.denominator / split.numerator;
    }
    if (factor === 1) return level;
    return {
      ...level,
      price: level.price * factor,
      title: `${level.title} ${t("levels.splitScaled", { factor: String(Number(factor.toPrecision(6))) })}`,
    };
  });
}
