import type { ReactNode } from "react";

import { useI18n } from "./i18n";
import type { InstrumentListing, SessionQuote } from "./market-api";
import type { QuoteRow } from "./session-quotes";

interface WatchlistProps {
  instruments: InstrumentListing[];
  /** Quotes by instrument ID, shared by every watchlist view (see useSessionQuotes). */
  rows: Record<string, QuoteRow>;
  sessionDate: string;
  activeInstrumentId: string;
  onPick: (instrumentId: string) => void;
  /** Instruments the user added through search; only these can be removed. */
  removable?: ReadonlySet<string>;
  onRemove?: (instrumentId: string) => void;
  /** Buttons shown beside the title (pop out, dock). */
  actions?: ReactNode;
  /** Hide the title when the surrounding window already shows one. */
  hideTitle?: boolean;
}

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
  instruments,
  rows,
  sessionDate,
  activeInstrumentId,
  onPick,
  removable,
  onRemove,
  actions,
  hideTitle = false,
}: WatchlistProps) {
  const { t } = useI18n();

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
            const row: QuoteRow = rows[id] ?? { status: "loading" };
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
                  {onRemove && removable?.has(id) ? (
                    <button
                      type="button"
                      className="watch-remove"
                      aria-label={t("watch.remove", {
                        symbol: item.instrument.symbol,
                      })}
                      title={t("watch.remove", {
                        symbol: item.instrument.symbol,
                      })}
                      onClick={() => onRemove(id)}
                    >
                      ×
                    </button>
                  ) : null}
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
