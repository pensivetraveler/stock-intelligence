/**
 * Numbers in the symbol band (디자인-siw-2 2번): open, high, low, last, change from the open
 * and volume of the chart's session, from the same quote the watchlist uses (the regular
 * session's 60m bars, an existing API). Hidden during replay, so no later bar shows.
 * Not shown, for want of a source in the current APIs: 52-week range, market cap, next
 * earnings date.
 */
import { useEffect, useState } from "react";

import { useI18n } from "./i18n";
import type { MarketApi, SessionQuote } from "./market-api";
import type { QuoteRow } from "./session-quotes";

export function SymbolBandNumbers({
  api,
  instrumentId,
  sessionDate,
  hidden,
  ready,
  shared,
}: {
  api: MarketApi;
  instrumentId: string;
  sessionDate: string;
  hidden: boolean;
  /** The visible charts finished loading: their bars request is not queued behind this one. */
  ready: boolean;
  /** The watchlist's row for this instrument while the watchlist is showing: reused, not fetched again. */
  shared?: QuoteRow | null;
}) {
  const { t } = useI18n();
  const [fetched, setQuote] = useState<SessionQuote | null>(null);
  const reuse = shared !== undefined;
  useEffect(() => {
    if (hidden || !ready || reuse) return;
    let live = true;
    setQuote(null);
    api.quote(instrumentId, sessionDate).then(
      (value) => {
        if (live) setQuote(value);
      },
      () => {
        if (live) setQuote(null);
      },
    );
    return () => {
      live = false;
    };
  }, [api, instrumentId, sessionDate, hidden, ready, reuse]);
  if (hidden) {
    return <span className="symbol-band-note">{t("band.replay")}</span>;
  }
  const quote = reuse
    ? shared?.status === "ready"
      ? shared.quote
      : null
    : fetched;
  if (!quote) return null;
  const open = Number(quote.open);
  const percent = open === 0 ? 0 : ((Number(quote.last) - open) / open) * 100;
  const stat = (label: string, value: string | undefined) => (
    <span className="symbol-band-stat">
      <small>{label}</small>
      <b>{value ?? "—"}</b>
    </span>
  );
  return (
    <span className="symbol-band-numbers">
      <span className="symbol-band-price">
        <b>{quote.last}</b>
        <span className={percent >= 0 ? "legend-up" : "legend-down"}>
          {`${percent >= 0 ? "+" : ""}${percent.toFixed(2)}%`}
        </span>
        {quote.lastFinal ? null : (
          <small className="legend-provisional">{t("watch.provisional")}</small>
        )}
      </span>
      {stat(t("band.open"), quote.open)}
      {stat(t("band.high"), quote.high)}
      {stat(t("band.low"), quote.low)}
      {stat(
        t("band.volume"),
        quote.volume ? Number(quote.volume).toLocaleString() : undefined,
      )}
    </span>
  );
}
