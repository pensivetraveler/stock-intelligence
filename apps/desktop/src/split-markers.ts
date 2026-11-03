import type { FilingSplitsResponse, PriceBar } from "@siw/contracts/types";

import type { Translate } from "./i18n";
import type { ChartMarker } from "./journal-markers";

type SplitEvent = FilingSplitsResponse["events"][number];

/** Splits are not trades: one neutral colour, and another where the two sides differ. */
export const SPLIT_COLORS = { agreed: "#8fa3bf", differs: "#e8a33d" };

const DIFFERS = new Set(["ratio_differs", "filing_only", "provider_only"]);

function localDate(ms: number, timeZone: string): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(ms));
}

function trimmed(value: number): string {
  return String(Number(value.toFixed(4)));
}

/** "10:1" for a split, "1:15" for a reverse split: new shares to old. */
export function splitRatio(numerator: number, denominator: number): string {
  return numerator >= denominator
    ? `${trimmed(numerator / denominator)}:1`
    : `1:${trimmed(denominator / numerator)}`;
}

function eventDate(event: SplitEvent): string {
  return event.provider?.on ?? event.filing?.effective ?? "";
}

function sideRatio(side: { numerator: number; denominator: number }): string {
  return splitRatio(side.numerator, side.denominator);
}

function detail(t: Translate, event: SplitEvent): string {
  const provider = event.provider ? sideRatio(event.provider) : "";
  const filing = event.filing ? sideRatio(event.filing) : "";
  switch (event.kind) {
    case "same":
      return t("split.same");
    case "not_compared":
      return t("split.notCompared");
    case "provider_only":
      return t("split.providerOnly");
    case "filing_only":
      return t("split.filingOnly");
    case "ratio_differs":
      return t("split.ratioDiffers", { provider, filing });
    default:
      return t("split.filingUnconfirmed", {
        status: event.filing?.status ?? "",
      });
  }
}

/**
 * One marker per split on the bar that holds its date in the exchange's zone. A split
 * outside the drawn bars, or on a day with no bar, is not drawn.
 */
export function splitMarkers(
  bars: PriceBar[],
  events: SplitEvent[],
  timeZone: string,
  t: Translate,
): ChartMarker[] {
  const markers: ChartMarker[] = [];
  for (const event of events) {
    const on = eventDate(event);
    const side = event.provider ?? event.filing;
    if (!on || !side) continue;
    const bar = bars.find(
      (item) =>
        localDate(Date.parse(item.interval_start_utc), timeZone) <= on &&
        on <= localDate(Date.parse(item.interval_end_utc) - 1, timeZone),
    );
    if (!bar) continue;
    const ratio = sideRatio(side);
    const text = t(
      side.numerator >= side.denominator ? "split.forward" : "split.reverse",
      { ratio },
    );
    markers.push({
      id: `split-${on}-${event.kind}`,
      time: Math.floor(Date.parse(bar.interval_start_utc) / 1000),
      position: "aboveBar",
      shape: "square",
      color: DIFFERS.has(event.kind)
        ? SPLIT_COLORS.differs
        : SPLIT_COLORS.agreed,
      text,
      details: [`${text} · ${on} · ${detail(t, event)}`],
    });
  }
  return markers;
}
