import type {
  BrokerConditionList,
  BrokerConditionResult,
} from "@siw/contracts/types";
import { useEffect, useState } from "react";

import { useI18n } from "./i18n";
import {
  MarketApiError,
  type InstrumentListing,
  type MarketApi,
} from "./market-api";

const SEQ_STORAGE_KEY = "siw.screener.kiwoomSeq";

interface ConditionSearchPanelProps {
  api: MarketApi;
  /** Show a matched ticker on the active chart, as a search result would. */
  onPick: (listing: InstrumentListing) => void;
  storage?: Storage | null;
}

function savedSeq(storage: Storage | null | undefined): string {
  try {
    return storage?.getItem(SEQ_STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * Kiwoom's US condition search (ADR 0048): the conditions saved in 영웅문Global, and one
 * run of the chosen one. Figures are Kiwoom's at the time of the run, shown as sent and
 * never stored; a ticker the app does not know cannot be opened on a chart.
 */
export function ConditionSearchPanel({
  api,
  onPick,
  storage = typeof window === "undefined" ? null : window.localStorage,
}: ConditionSearchPanelProps) {
  const { t } = useI18n();
  const [list, setList] = useState<BrokerConditionList | null>(null);
  const [listError, setListError] = useState<unknown>(null);
  const [seq, setSeqState] = useState(() => savedSeq(storage));
  const [result, setResult] = useState<BrokerConditionResult | null>(null);
  const [runError, setRunError] = useState<unknown>(null);
  const [running, setRunning] = useState(false);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let current = true;
    setListError(null);
    api
      .kiwoomConditions()
      .then((found) => current && setList(found))
      .catch((reason: unknown) => current && setListError(reason));
    return () => {
      current = false;
    };
  }, [api, reload]);

  const conditions = list?.conditions ?? [];
  const chosen = conditions.some((item) => item.seq === seq)
    ? seq
    : (conditions[0]?.seq ?? "");
  const setSeq = (next: string) => {
    setSeqState(next);
    try {
      storage?.setItem(SEQ_STORAGE_KEY, next);
    } catch {
      // Not saved: the choice holds until the page reloads.
    }
  };
  const run = () => {
    if (!chosen) return;
    setRunning(true);
    setRunError(null);
    api
      .runKiwoomCondition(chosen)
      .then(setResult)
      .catch((reason: unknown) => {
        setResult(null);
        setRunError(reason);
      })
      .finally(() => setRunning(false));
  };
  const noKey =
    listError instanceof MarketApiError &&
    listError.code === "quote_source_unavailable";

  return (
    <section
      className="quote-panel research-panel"
      aria-label={t("screener.title")}
    >
      <h2>{t("screener.title")}</h2>
      {listError ? (
        <p className="chart-error" role="alert">
          {noKey
            ? t("screener.noKey")
            : t("screener.listFailed", { error: errorText(listError) })}{" "}
          {noKey ? null : (
            <button
              type="button"
              className="secondary"
              onClick={() => setReload(reload + 1)}
            >
              {t("screener.retry")}
            </button>
          )}
        </p>
      ) : null}
      {list === null && !listError ? (
        <p className="workspace-note">{t("screener.loading")}</p>
      ) : null}
      {list && conditions.length === 0 ? (
        <p className="workspace-note">{t("screener.none")}</p>
      ) : null}
      {conditions.length > 0 ? (
        <div className="trade-fields">
          <label>
            {t("screener.condition")}
            <select
              value={chosen}
              onChange={(event) => setSeq(event.target.value)}
            >
              {conditions.map((item) => (
                <option key={item.seq} value={item.seq}>
                  {item.seq}. {item.name}
                </option>
              ))}
            </select>
          </label>
          <button type="button" disabled={running} onClick={run}>
            {running ? t("screener.running") : t("screener.run")}
          </button>
        </div>
      ) : null}
      {runError ? (
        <p className="chart-error" role="alert">
          {t("screener.runFailed", { error: errorText(runError) })}
        </p>
      ) : null}
      {result ? (
        <>
          <p className="workspace-note">
            {t("screener.meta", {
              rows: result.rows.length,
              name: result.condition.name,
              time: result.fetched_at_utc,
            })}
          </p>
          {result.complete ? null : (
            <p className="chart-error" role="status">
              {t("screener.incomplete")}
            </p>
          )}
          <div className="research-scroll">
            <table className="quote-trades research-table">
              <thead>
                <tr>
                  <th>{t("screener.symbol")}</th>
                  <th>{t("screener.name")}</th>
                  <th>{t("screener.last")}</th>
                  <th>{t("screener.changePercent")}</th>
                  <th>{t("screener.volume")}</th>
                </tr>
              </thead>
              <tbody>
                {result.rows.map((row) => {
                  const listing = row.listing;
                  return (
                    <tr key={`${row.symbol}:${row.kiwoom_exchange}`}>
                      <td>
                        {listing ? (
                          <button
                            type="button"
                            className="watch-symbol"
                            aria-label={t("watch.show", { symbol: row.symbol })}
                            onClick={() => onPick(listing)}
                          >
                            {row.symbol}
                          </button>
                        ) : (
                          <span title={t("screener.unknown")}>
                            {row.symbol}
                          </span>
                        )}
                      </td>
                      <td>{row.name}</td>
                      <td>{row.last}</td>
                      <td
                        className={
                          row.change_percent.startsWith("-")
                            ? "legend-down"
                            : "legend-up"
                        }
                      >
                        {row.change_percent}%
                      </td>
                      <td>{row.volume}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
      <p className="workspace-note">{t("screener.note")}</p>
    </section>
  );
}
