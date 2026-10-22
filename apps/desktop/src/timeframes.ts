/**
 * Timeframes are written as whole minutes, "Nm" (1 to 960), as the API expects, or as
 * "1d" / "1w" for daily and weekly bars. Hours are only a display: "120m" shows as "2h".
 */

export const MAX_TIMEFRAME_MINUTES = 960;

/** Offered in the timeframe menu; any other whole-minute value can be typed in. */
export const PRESET_TIMEFRAMES = [
  "1m",
  "2m",
  "3m",
  "5m",
  "10m",
  "15m",
  "30m",
  "45m",
  "60m",
  "120m",
  "180m",
  "240m",
  "1d",
  "1w",
] as const;

/** Daily and weekly bars: one request for the whole history, no sessions or hours. */
export function isDailyTimeframe(timeframe: string): boolean {
  return timeframe === "1d" || timeframe === "1w";
}

export const DEFAULT_FAVORITES = ["1m", "5m", "15m", "30m", "60m"];
export const FAVORITES_STORAGE_KEY = "siw.timeframeFavorites";

const PATTERN = /^([1-9][0-9]{0,2})m$/;

/** Minutes in a timeframe, or null when it is not a supported "Nm" value. */
export function timeframeMinutes(timeframe: string): number | null {
  const match = PATTERN.exec(timeframe);
  if (!match) return null;
  const minutes = Number(match[1]);
  return minutes <= MAX_TIMEFRAME_MINUTES ? minutes : null;
}

export function isTimeframe(value: unknown): value is string {
  return (
    typeof value === "string" &&
    (timeframeMinutes(value) !== null || isDailyTimeframe(value))
  );
}

export function timeframeSeconds(timeframe: string): number {
  if (timeframe === "1d") return 86_400;
  if (timeframe === "1w") return 7 * 86_400;
  return (timeframeMinutes(timeframe) ?? 1) * 60;
}

/** "45m", "2h", "1h 30m" — how a timeframe is shown on buttons and in the legend. */
export function timeframeLabel(timeframe: string): string {
  if (timeframe === "1d") return "1D";
  if (timeframe === "1w") return "1W";
  const minutes = timeframeMinutes(timeframe);
  if (minutes === null) return timeframe;
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest === 0 ? `${hours}h` : `${hours}h ${rest}m`;
}

/** A timeframe from a typed amount and unit, or null when out of range. */
export function customTimeframe(
  amount: number,
  unit: "minutes" | "hours",
): string | null {
  if (!Number.isInteger(amount) || amount < 1) return null;
  const minutes = unit === "hours" ? amount * 60 : amount;
  return minutes <= MAX_TIMEFRAME_MINUTES ? `${minutes}m` : null;
}

/** Favorites sorted from shortest to longest, without duplicates or bad values. */
export function normalizeFavorites(values: unknown): string[] {
  if (!Array.isArray(values)) return [...DEFAULT_FAVORITES];
  const unique = [...new Set(values.filter(isTimeframe))];
  return unique.sort((a, b) => timeframeSeconds(a) - timeframeSeconds(b));
}

export function loadFavorites(storage: Storage | null | undefined): string[] {
  try {
    const raw = storage?.getItem(FAVORITES_STORAGE_KEY);
    return raw ? normalizeFavorites(JSON.parse(raw)) : [...DEFAULT_FAVORITES];
  } catch {
    return [...DEFAULT_FAVORITES];
  }
}

export function saveFavorites(
  storage: Storage | null | undefined,
  favorites: string[],
): void {
  try {
    storage?.setItem(FAVORITES_STORAGE_KEY, JSON.stringify(favorites));
  } catch {
    // Not saved: the favorites still apply until the page reloads.
  }
}

/** Default number of candles a chart loads (across earlier sessions if needed). */
export const DEFAULT_CANDLES = 1_200;
/** Intraday charts look back at most this many sessions to reach DEFAULT_CANDLES. */
export const MAX_HISTORY_SESSIONS = 60;

/** Sessions to load so a chart shows about DEFAULT_CANDLES bars, capped. */
export function sessionsForCandles(
  timeframe: string,
  scope: "regular" | "extended" | "overnight",
): number {
  if (isDailyTimeframe(timeframe)) return 1; // one request already spans the history
  const minutes = timeframeMinutes(timeframe) ?? 1;
  const sessionMinutes = scope === "regular" ? 390 : 960;
  const barsPerSession = Math.max(1, Math.ceil(sessionMinutes / minutes));
  return Math.min(
    MAX_HISTORY_SESSIONS,
    Math.max(1, Math.ceil(DEFAULT_CANDLES / barsPerSession)),
  );
}

/** Calendar days a daily or weekly chart looks back to show about DEFAULT_CANDLES. */
export function dailyLookbackDays(timeframe: string): number {
  // About 252 sessions a year; weekly bars reach as far back as the provider allows.
  return timeframe === "1w" ? 8_400 : Math.ceil((DEFAULT_CANDLES * 365) / 252);
}

function shiftDays(date: string, days: number): string {
  const value = new Date(`${date}T00:00:00Z`);
  value.setUTCDate(value.getUTCDate() + days);
  return value.toISOString().slice(0, 10);
}

/** UTC request range of a daily or weekly chart that ends with `sessionDate`. */
export function dailyRange(
  timeframe: string,
  sessionDate: string,
): { startUtc: string; endUtc: string } {
  return {
    startUtc: `${shiftDays(sessionDate, -dailyLookbackDays(timeframe))}T00:00:00Z`,
    endUtc: `${shiftDays(sessionDate, 1)}T00:00:00Z`,
  };
}
