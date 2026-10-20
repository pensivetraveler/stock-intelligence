import type { MarketQuote } from "@siw/contracts/types";
import { useEffect, useState } from "react";

import { useI18n } from "./i18n";
import { MarketApiError, type MarketApi } from "./market-api";

const SECOND_TIME = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

/** Refresh period while the panel is open (Toss MARKET_DATA allows 15 requests/s). */
export const QUOTE_REFRESH_MS = 5_000;

function at(utc: string): string {
  return `${SECOND_TIME.format(new Date(utc))} ET`;
}

interface QuotePanelProps {
  api: MarketApi;
  instrumentId: string;
  symbol: string;
  /** Live data would show the present during a replay of the past, so it pauses. */
  replayOn: boolean;
}

/**
 * Live quote of the active chart's instrument from the broker: last price, order book,
 * recent trades and daily price limits. Read on demand and refreshed while open; nothing
 * is stored.
 */
export function QuotePanel({
  api,
  instrumentId,
  symbol,
  replayOn,
}: QuotePanelProps) {
  const { t } = useI18n();
  const [quote, setQuote] = useState<MarketQuote | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    if (replayOn) return;
    let current = true;
    setQuote(null);
    setError(null);
    const load = () =>
      api
        .liveQuote(instrumentId)
        .then((result) => {
          if (!current) return;
          setQuote(result);
          setError(null);
        })
        .catch((reason: unknown) => {
          if (current) setError(reason);
        });
    void load();
    const timer = window.setInterval(() => void load(), QUOTE_REFRESH_MS);
    return () => {
      current = false;
      window.clearInterval(timer);
    };
  }, [api, instrumentId, replayOn]);

  if (replayOn) {
    return (
      <section className="quote-panel" aria-label={t("quote.title")}>
        <h2>{t("quote.title")}</h2>
        <p className="workspace-note" role="status">
          {t("quote.replay")}
        </p>
      </section>
    );
  }

  const unavailable =
    error instanceof MarketApiError &&
    error.code === "quote_source_unavailable";
  const asks = quote?.orderbook ? [...quote.orderbook.asks].reverse() : [];
  return (
    <section className="quote-panel" aria-label={t("quote.title")}>
      <h2>
        {t("quote.title")} · {symbol}
      </h2>
      {error ? (
        <p className="chart-error" role="alert">
          {unavailable
            ? t("quote.noKey")
            : t("quote.failed", {
                error: error instanceof Error ? error.message : String(error),
              })}
        </p>
      ) : null}
      {quote === null && !error ? (
        <p className="workspace-note">{t("quote.loading")}</p>
      ) : null}
      {quote ? (
        <>
          <p className="quote-last">
            <strong>{quote.last ? quote.last.price : "—"}</strong>{" "}
            <small>
              {quote.currency}
              {quote.last ? ` · ${at(quote.last.at_utc)}` : ""}
            </small>
          </p>
          {quote.price_limits ? (
            <p className="workspace-note">
              {t("quote.limits", {
                upper: quote.price_limits.upper,
                lower: quote.price_limits.lower,
              })}
            </p>
          ) : null}
          <h3>{t("quote.orderbook")}</h3>
          <table className="quote-book">
            <thead>
              <tr>
                <th>{t("quote.price")}</th>
                <th>{t("quote.volume")}</th>
              </tr>
            </thead>
            <tbody>
              {asks.map((level) => (
                <tr key={`ask-${level.price}`} className="ask">
                  <td>{level.price}</td>
                  <td>{level.volume}</td>
                </tr>
              ))}
              {(quote.orderbook?.bids ?? []).map((level) => (
                <tr key={`bid-${level.price}`} className="bid">
                  <td>{level.price}</td>
                  <td>{level.volume}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <h3>{t("quote.trades")}</h3>
          <table className="quote-trades">
            <thead>
              <tr>
                <th>{t("quote.time")}</th>
                <th>{t("quote.price")}</th>
                <th>{t("quote.volume")}</th>
              </tr>
            </thead>
            <tbody>
              {quote.trades.map((trade, index) => (
                <tr key={`${trade.at_utc}-${index}`}>
                  <td>{at(trade.at_utc)}</td>
                  <td>{trade.price}</td>
                  <td>{trade.volume}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="workspace-note">
            {t("quote.source", {
              source: quote.source_id,
              time: at(quote.observed_at_utc),
            })}
          </p>
        </>
      ) : null}
    </section>
  );
}
