import { useEffect, useRef, useState } from "react";

import type { InstrumentListing, MarketApi, SessionQuote } from "./market-api";

export type QuoteRow =
  | { status: "loading" }
  | { status: "ready"; quote: SessionQuote | null }
  | { status: "error" };

interface Request {
  api: MarketApi;
  instruments: InstrumentListing[];
  sessionDate: string;
}

/**
 * Watchlist quotes for one session date, fetched once and shared by every watchlist
 * view (side panel and floating window).
 *
 * Quote requests start only while `enabled` is true. The workspace enables them once
 * the visible charts have loaded, so the chart's own bars request is not queued
 * behind one session and one 60m bars request per watchlist instrument. Turning
 * `enabled` off again (for example while a chart reloads) does not cancel or repeat
 * quotes already requested for the same date.
 */
export function useSessionQuotes(
  api: MarketApi,
  instruments: InstrumentListing[],
  sessionDate: string,
  enabled: boolean,
): Record<string, QuoteRow> {
  const requested = useRef<Request | null>(null);
  const [loaded, setLoaded] = useState<{
    request: Request | null;
    rows: Record<string, QuoteRow>;
  }>({ request: null, rows: {} });

  useEffect(() => {
    const previous = requested.current;
    if (
      !enabled ||
      instruments.length === 0 ||
      (previous?.api === api &&
        previous.instruments === instruments &&
        previous.sessionDate === sessionDate)
    ) {
      return;
    }
    const request = { api, instruments, sessionDate };
    requested.current = request;
    setLoaded({ request, rows: {} });
    const settle = (id: string, row: QuoteRow) => {
      // A newer date or instrument list replaced this request: drop its answers.
      if (requested.current !== request) return;
      setLoaded((current) =>
        current.request === request
          ? { request, rows: { ...current.rows, [id]: row } }
          : current,
      );
    };
    for (const item of instruments) {
      const id = item.instrument.instrument_id;
      api.quote(id, sessionDate).then(
        (quote) => settle(id, { status: "ready", quote }),
        () => settle(id, { status: "error" }),
      );
    }
  }, [api, instruments, sessionDate, enabled]);

  const current = loaded.request;
  return current?.api === api &&
    current.instruments === instruments &&
    current.sessionDate === sessionDate
    ? loaded.rows
    : {};
}
