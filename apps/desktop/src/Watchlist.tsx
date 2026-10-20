import { useEffect, useState, type ReactNode } from "react";

import { useI18n } from "./i18n";
import type { InstrumentListing, MarketApi, SessionQuote } from "./market-api";

interface WatchlistProps {
  api: MarketApi;
  instruments: InstrumentListing[];
  sessionDate: string;
  activeInstrumentId: string;
  onPick: (instrumentId: string) => void;
  /** Buttons shown beside the title (pop out, dock). */
  actions?: ReactNode;
  /** Hide the title when the surrounding window already shows one. */
  hideTitle?: boolean;
}

type Row =
  | { status: "loading" }
  | { status: "ready"; quote: SessionQuote | null }
  | { status: "error" };

/** Change from the session open, for display only (prices stay strings elsewhere). */
function change(quote: SessionQuote): { text: string; rising: boolean } {
  const open = Number(quote.open);
  const percent = open === 0 ? 0 : ((Number(quote.last) - open) / open) * 100;
  return {
    text: `${percent >= 0 ? "+" : ""}${percent.toFixed(2)}%`,
    rising: percent >= 0,
  };
}

/**
 * Every listed instrument with its regular-session open-to-last change on the active
 * chart's day. Clicking a row shows that instrument in the active chart.
 */
export function Watchlist({
  api,
  instruments,
  sessionDate,
  activeInstrumentId,
  onPick,
  actions,
  hideTitle = false,
}: WatchlistProps) {
  const { t } = useI18n();
  const [rows, setRows] = useState<Record<string, Row>>({});

  useEffect(() => {
    let current = true;
    setRows({});
    for (const item of instruments) {
      const id = item.instrument.instrument_id;
      api
        .quote(id, sessionDate)
        .then((quote) => {
          if (current)
            setRows((previous) => ({
              ...previous,
              [id]: { status: "ready", quote },
            }));
        })
        .catch(() => {
          if (current)
            setRows((previous) => ({ ...previous, [id]: { status: "error" } }));
        });
    }
    return () => {
      current = false;
    };
  }, [api, instruments, sessionDate]);

  return (
    <div className="watchlist">
      {hideTitle ? null : (
        <div className="watchlist-head">
          <h2>{t("watch.title")}</h2>
          {actions}
        </div>
      )}
      <table>
        <thead>
          <tr>
            <th>{t("watch.symbol")}</th>
            <th>{t("watch.last")}</th>
            <th>{t("watch.change")}</th>
          </tr>
        </thead>
        <tbody>
          {instruments.map((item) => {
            const id = item.instrument.instrument_id;
            const row = rows[id] ?? { status: "loading" };
            const quote = row.status === "ready" ? row.quote : null;
            const delta = quote ? change(quote) : null;
            return (
              <tr
                key={id}
                className={id === activeInstrumentId ? "selected" : undefined}
              >
                <td>
                  <button
                    type="button"
                    className="watch-symbol"
                    aria-label={t("watch.show", {
                      symbol: item.instrument.symbol,
                    })}
                    onClick={() => onPick(id)}
                  >
                    {item.instrument.symbol}
                  </button>
                </td>
                <td>
                  {row.status === "loading"
                    ? t("watch.loading")
                    : row.status === "error"
                      ? "—"
                      : quote
                        ? quote.last
                        : t("watch.noData")}
                  {quote && !quote.lastFinal ? (
                    <small className="legend-provisional">
                      {" "}
                      {t("watch.provisional")}
                    </small>
                  ) : null}
                </td>
                <td
                  className={
                    delta
                      ? delta.rising
                        ? "legend-up"
                        : "legend-down"
                      : undefined
                  }
                >
                  {delta ? delta.text : ""}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="workspace-note">{t("watch.note", { date: sessionDate })}</p>
    </div>
  );
}
