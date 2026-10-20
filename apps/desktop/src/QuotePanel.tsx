import type { MarketQuote } from "@siw/contracts/types";
import { useEffect, useState } from "react";

import { useI18n } from "./i18n";
import { MarketApiError, type MarketApi } from "./market-api";
import type { LiveSymbol } from "./realtime";
import { zoneFormat, zoneOf, type MarketZone } from "./market-zone";

const SECOND_TIME: Intl.DateTimeFormatOptions = {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
};

/** Refresh period while the panel is open (Toss MARKET_DATA allows 15 requests/s). */
export const QUOTE_REFRESH_MS = 5_000;

function atIn(zone: MarketZone, utc: string): string {
  return `${zoneFormat(zone, "en-US", SECOND_TIME).format(new Date(utc))} ${zone.label}`;
}

interface QuotePanelProps {
  api: MarketApi;
  instrumentId: string;
  symbol: string;
  /** Live data would show the present during a replay of the past, so it pauses. */
  replayOn: boolean;
  /** Trades and order book pushed by the realtime relay for this symbol. */
  live?: LiveSymbol | null;
  liveConnected?: boolean;
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
  live = null,
  liveConnected = false,
}: QuotePanelProps) {
  const { t } = useI18n();
  const zone = zoneOf(instrumentId);
  const at = (utc: string) => atIn(zone, utc);
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
  // Pushed data replaces polled data when it is newer.
  const liveBook =
    live?.orderbook &&
    (!quote?.orderbook ||
      live.orderbook.exchange_time_utc > quote.orderbook.at_utc)
      ? live.orderbook
      : null;
  const book = liveBook ?? quote?.orderbook ?? null;
  const asks = book ? [...book.asks].reverse() : [];
  const newestLive = live?.trades[0];
  const last =
    newestLive &&
    (!quote?.last || newestLive.exchange_time_utc > quote.last.at_utc)
      ? { price: newestLive.price, at_utc: newestLive.exchange_time_utc }
      : (quote?.last ?? null);
  const seen = new Set(
    (live?.trades ?? []).map(
      (trade) => `${trade.exchange_time_utc}|${trade.price}|${trade.volume}`,
    ),
  );
  const trades = [
    ...(live?.trades ?? []).map((trade) => ({
      price: trade.price,
      volume: trade.volume,
      at_utc: trade.exchange_time_utc,
    })),
    ...(quote?.trades ?? []).filter(
      (trade) => !seen.has(`${trade.at_utc}|${trade.price}|${trade.volume}`),
    ),
  ]
    .sort((a, b) => (a.at_utc < b.at_utc ? 1 : -1))
    .slice(0, 30);
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
            <strong>{last ? last.price : "—"}</strong>{" "}
            <small>
              {quote.currency}
              {last ? ` · ${at(last.at_utc)}` : ""}
              {" · "}
              {liveConnected ? t("quote.live") : t("quote.polling")}
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
              {(book?.bids ?? []).map((level) => (
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
              {trades.map((trade, index) => (
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
