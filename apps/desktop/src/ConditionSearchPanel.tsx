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

interface ConditionSearchPanelProps {
  api: MarketApi;
  /** Show a matched ticker on the active chart, as a search result would. */
  onPick: (listing: InstrumentListing) => void;
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * Kiwoom's US condition search (ADR 0048): one button per condition SIW is connected
 * to, found by name in 영웅문Global, and one run of the pressed one. Figures are Kiwoom's at the time of the run, shown as sent and
 * never stored; a ticker the app does not know cannot be opened on a chart.
 */
export function ConditionSearchPanel({
  api,
  onPick,
}: ConditionSearchPanelProps) {
  const { t } = useI18n();
  const [list, setList] = useState<BrokerConditionList | null>(null);
  const [listError, setListError] = useState<unknown>(null);
  const [result, setResult] = useState<BrokerConditionResult | null>(null);
  const [runError, setRunError] = useState<unknown>(null);
  const [running, setRunning] = useState<string | null>(null);
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
  const run = (name: string) => {
    setRunning(name);
    setRunError(null);
    api
      .runKiwoomCondition(name)
      .then(setResult)
      .catch((reason: unknown) => {
        setResult(null);
        setRunError(reason);
      })
      .finally(() => setRunning(null));
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
      {list && list.missing.length > 0 ? (
        <p className="workspace-note" role="status">
          {t("screener.missing", { names: list.missing.join(", ") })}
        </p>
      ) : null}
      {conditions.length > 0 ? (
        <div
          className="trade-fields"
          role="group"
          aria-label={t("screener.condition")}
        >
          {conditions.map((item) => (
            <button
              key={item.name}
              type="button"
              className={
                result?.condition.name === item.name ? undefined : "secondary"
              }
              aria-pressed={result?.condition.name === item.name}
              disabled={running !== null}
              onClick={() => run(item.name)}
            >
              {running === item.name ? t("screener.running") : item.name}
            </button>
          ))}
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
                      <td className="condition-name" title={row.name}>
                        {row.name}
                      </td>
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
