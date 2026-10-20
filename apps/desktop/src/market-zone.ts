/**
 * The market an instrument trades in and how its times and prices are shown: US stocks in
 * New York time with cents, Korean stocks (`kr:` IDs) in Korean time with whole won.
 */
export interface MarketZone {
  market: "US" | "KR";
  timeZone: string;
  /** Shown after times, e.g. "09:30 ET". */
  label: string;
  /** Exchange calendar for session lookups. */
  calendarId: "XNAS" | "XKRX";
  currency: "USD" | "KRW";
  priceDecimals: number;
}

export const US_ZONE: MarketZone = {
  market: "US",
  timeZone: "America/New_York",
  label: "ET",
  calendarId: "XNAS",
  currency: "USD",
  priceDecimals: 2,
};

export const KR_ZONE: MarketZone = {
  market: "KR",
  timeZone: "Asia/Seoul",
  label: "KST",
  calendarId: "XKRX",
  currency: "KRW",
  priceDecimals: 0,
};

export function zoneOf(instrumentId: string): MarketZone {
  return instrumentId.startsWith("kr:") ? KR_ZONE : US_ZONE;
}

const formats = new Map<string, Intl.DateTimeFormat>();

/** A cached formatter for one zone and set of options. */
export function zoneFormat(
  zone: MarketZone,
  locale: string,
  options: Intl.DateTimeFormatOptions,
): Intl.DateTimeFormat {
  const key = `${zone.timeZone}|${locale}|${JSON.stringify(options)}`;
  let format = formats.get(key);
  if (!format) {
    format = new Intl.DateTimeFormat(locale, {
      timeZone: zone.timeZone,
      ...options,
    });
    formats.set(key, format);
  }
  return format;
}
