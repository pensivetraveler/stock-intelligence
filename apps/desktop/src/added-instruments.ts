import type { InstrumentListing } from "./market-api";

/**
 * Stocks the user picked from ticker search, beyond the provider's configured list. They
 * can be charted and appear in the watchlist. Kept on this device only.
 */
export const ADDED_INSTRUMENTS_STORAGE_KEY = "siw.addedInstruments";
export const MAX_ADDED_INSTRUMENTS = 200;

function isListing(value: unknown): value is InstrumentListing {
  if (typeof value !== "object" || value === null) return false;
  const instrument = (value as { instrument?: unknown }).instrument;
  if (typeof instrument !== "object" || instrument === null) return false;
  const { instrument_id: id, symbol } = instrument as Record<string, unknown>;
  return typeof id === "string" && typeof symbol === "string";
}

export function loadAddedInstruments(
  storage: Storage | null | undefined,
): InstrumentListing[] {
  try {
    const raw = storage?.getItem(ADDED_INSTRUMENTS_STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed)
      ? parsed.filter(isListing).slice(0, MAX_ADDED_INSTRUMENTS)
      : [];
  } catch {
    return [];
  }
}

export function saveAddedInstruments(
  storage: Storage | null | undefined,
  listings: InstrumentListing[],
): void {
  try {
    storage?.setItem(ADDED_INSTRUMENTS_STORAGE_KEY, JSON.stringify(listings));
  } catch {
    // Not saved: the added stocks stay until the page reloads.
  }
}

/** Configured instruments first, then added ones not already configured. */
export function mergeInstruments(
  configured: InstrumentListing[],
  added: InstrumentListing[],
): InstrumentListing[] {
  const known = new Set(
    configured.map((item) => item.instrument.instrument_id),
  );
  return [
    ...configured,
    ...added.filter((item) => !known.has(item.instrument.instrument_id)),
  ];
}
