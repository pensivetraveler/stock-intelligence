import { useState, type ReactNode } from "react";

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

/** Watchlist views (디자인-siw-1 W1 기본 · W2 두 줄 · W3 등락 막대). */
type WatchView = "table" | "rows" | "movers";
const VIEW_KEY = "siw.watchlistView";

function loadView(): WatchView {
  try {
    const saved = globalThis.localStorage?.getItem(VIEW_KEY);
    return saved === "rows" || saved === "movers" ? saved : "table";
  } catch {
    return "table";
  }
}

/** Percent change from the open, or null while unknown. */
function percentOf(quote: SessionQuote | null): number | null {
  if (!quote) return null;
  const open = Number(quote.open);
  return open === 0 ? 0 : ((Number(quote.last) - open) / open) * 100;
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
  const [view, setView] = useState<WatchView>(loadView);
  const choose = (next: WatchView) => {
    setView(next);
    try {
      globalThis.localStorage?.setItem(VIEW_KEY, next);
    } catch {
      // Not saved: the view still changes until the page reloads.
    }
  };
  const switcher = (
    <div className="watch-views" role="group" aria-label={t("watch.view")}>
      {(["table", "rows", "movers"] as const).map((id) => (
        <button
          key={id}
          type="button"
          className="tb-button"
          aria-pressed={view === id}
          onClick={() => choose(id)}
        >
          {t(`watch.view.${id}`)}
        </button>
      ))}
    </div>
  );
  const symbolButton = (item: InstrumentListing) => (
    <button
      type="button"
      className="watch-symbol"
      aria-label={t("watch.show", { symbol: item.instrument.symbol })}
      onClick={() => onPick(item.instrument.instrument_id)}
    >
      {item.instrument.symbol}
    </button>
  );
  const quoteOf = (item: InstrumentListing) => {
    const row: QuoteRow = rows[item.instrument.instrument_id] ?? {
      status: "loading",
    };
    return { row, quote: row.status === "ready" ? row.quote : null };
  };
  const lastText = (row: QuoteRow, quote: SessionQuote | null) =>
    row.status === "loading"
      ? t("watch.loading")
      : row.status === "error"
        ? "—"
        : quote
          ? quote.last
          : t("watch.noData");

  if (view !== "table") {
    const listed = instruments.map((item) => ({ item, ...quoteOf(item) }));
    const ordered =
      view === "movers"
        ? [...listed].sort(
            (a, b) =>
              (percentOf(b.quote) ?? -Infinity) -
              (percentOf(a.quote) ?? -Infinity),
          )
        : listed;
    const widest = Math.max(
      1,
      ...listed.map((entry) => Math.abs(percentOf(entry.quote) ?? 0)),
    );
    return (
      <div className="watchlist">
        {hideTitle ? (
          switcher
        ) : (
          <div className="watchlist-head">
            <h2>{t("watch.title")}</h2>
            {switcher}
            {actions}
          </div>
        )}
        <ul className={`watch-list watch-${view}`}>
          {ordered.map(({ item, row, quote }) => {
            const id = item.instrument.instrument_id;
            const percent = percentOf(quote);
            const delta = quote ? change(quote) : null;
            return (
              <li
                key={id}
                className={id === activeInstrumentId ? "selected" : undefined}
              >
                <span className="watch-name">
                  {symbolButton(item)}
                  {quote && !quote.lastFinal ? (
                    <small className="legend-provisional">
                      {t("watch.provisional")}
                    </small>
                  ) : null}
                </span>
                {view === "movers" ? (
                  <span className="watch-bar" aria-hidden="true">
                    <i
                      className={
                        percent !== null && percent < 0 ? "neg" : "pos"
                      }
                      style={{
                        width: `${(Math.abs(percent ?? 0) / widest) * 50}%`,
                      }}
                    />
                  </span>
                ) : (
                  <span className="watch-last">{lastText(row, quote)}</span>
                )}
                <span
                  className={
                    delta
                      ? delta.rising
                        ? "watch-change legend-up"
                        : "watch-change legend-down"
                      : "watch-change"
                  }
                >
                  {delta ? delta.text : ""}
                </span>
                {view === "movers" ? (
                  <span className="watch-last">{lastText(row, quote)}</span>
                ) : null}
              </li>
            );
          })}
        </ul>
        <p className="workspace-note">
          {t("watch.note", { date: sessionDate })}
        </p>
      </div>
    );
  }

  return (
    <div className="watchlist">
      {hideTitle ? (
        switcher
      ) : (
        <div className="watchlist-head">
          <h2>{t("watch.title")}</h2>
          {switcher}
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
