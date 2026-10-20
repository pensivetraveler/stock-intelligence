import type { ResearchTable } from "@siw/contracts/types";
import { useEffect, useState } from "react";

import { useI18n, type MessageKey } from "./i18n";
import { MarketApiError, type MarketApi } from "./market-api";
import {
  datasetById,
  INDICATORS,
  RESEARCH_DATASETS,
  researchParams,
} from "./research";

interface ResearchPanelProps {
  api: MarketApi;
}

/**
 * Toss research tables for reading: stock and sector rankings, Korean market indicators
 * and investor flows, sectors, and Korean stock trading trends. Values are shown exactly
 * as Toss sent them.
 */
export function ResearchPanel({ api }: ResearchPanelProps) {
  const { t } = useI18n();
  const [datasetId, setDatasetId] = useState<string>("rankings");
  const [choices, setChoices] = useState<Record<string, string>>({});
  const [key, setKey] = useState("");
  const [cursor, setCursor] = useState<string | null>(null);
  const [table, setTable] = useState<ResearchTable | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  const dataset = datasetById(datasetId);
  const keyValue =
    dataset.key === "indicator" ? key || INDICATORS[0] : key.trim();
  const ready = !dataset.key || keyValue !== "";
  const params = researchParams(dataset, choices, cursor);
  const requestKey = JSON.stringify([dataset.id, keyValue, params]);

  useEffect(() => {
    if (!ready) return;
    let current = true;
    setLoading(true);
    api
      .research(dataset.id, dataset.key ? keyValue : null, params)
      .then((result) => {
        if (!current) return;
        setTable(result);
        setError(null);
      })
      .catch((reason: unknown) => {
        if (current) {
          setTable(null);
          setError(reason);
        }
      })
      .finally(() => current && setLoading(false));
    return () => {
      current = false;
    };
    // requestKey stands for the dataset, key and parameters of this request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, requestKey, ready]);

  const pick = (next: string) => {
    setDatasetId(next);
    setChoices({});
    setKey("");
    setCursor(null);
  };
  const noKey =
    error instanceof MarketApiError &&
    error.code === "quote_source_unavailable";

  return (
    <section
      className="quote-panel research-panel"
      aria-label={t("research.title")}
    >
      <h2>{t("research.title")}</h2>
      <div className="trade-fields">
        <label>
          {t("research.dataset")}
          <select
            value={dataset.id}
            onChange={(event) => pick(event.target.value)}
          >
            {RESEARCH_DATASETS.map((item) => (
              <option key={item.id} value={item.id}>
                {t(`research.ds.${item.id}` as MessageKey)}
              </option>
            ))}
          </select>
        </label>
        {dataset.key === "indicator" ? (
          <label>
            {t("research.indicator")}
            <select
              value={keyValue}
              onChange={(event) => {
                setKey(event.target.value);
                setCursor(null);
              }}
            >
              {INDICATORS.map((item) => (
                <option key={item}>{item}</option>
              ))}
            </select>
          </label>
        ) : dataset.key ? (
          <label>
            {t(
              dataset.key === "sector"
                ? "research.sectorId"
                : "research.krSymbol",
            )}
            <input
              value={key}
              placeholder={dataset.key === "sector" ? "100" : "005930"}
              onChange={(event) => {
                setKey(event.target.value);
                setCursor(null);
              }}
            />
          </label>
        ) : null}
        {dataset.options.map((option) => (
          <label key={option.name}>
            {option.name}
            <select
              value={choices[option.name] ?? option.values[0]}
              onChange={(event) => {
                setChoices({ ...choices, [option.name]: event.target.value });
                setCursor(null);
              }}
            >
              {option.values.map((value) => (
                <option key={value}>{value}</option>
              ))}
            </select>
          </label>
        ))}
      </div>
      {dataset.koreanOnly ? (
        <p className="workspace-note">{t("research.koreanOnly")}</p>
      ) : null}
      {dataset.key === "sector" ? (
        <p className="workspace-note">{t("research.sectorHint")}</p>
      ) : null}
      {error ? (
        <p className="chart-error" role="alert">
          {noKey
            ? t("research.noKey")
            : t("research.failed", {
                error: error instanceof Error ? error.message : String(error),
              })}
        </p>
      ) : null}
      {loading ? (
        <p className="workspace-note">{t("research.loading")}</p>
      ) : null}
      {table ? (
        <>
          <p className="workspace-note">
            {t("research.meta", {
              rows: table.rows.length,
              time: table.as_of_utc ?? table.observed_at_utc,
            })}
          </p>
          <div className="research-scroll">
            <table className="quote-trades research-table">
              <thead>
                <tr>
                  {table.columns.map((column) => (
                    <th key={column}>{column}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {table.rows.map((row, index) => (
                  <tr key={index}>
                    {row.map((cell, column) => (
                      <td key={column}>{cell === null ? "—" : String(cell)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {table.next_cursor && dataset.cursor ? (
            <button
              type="button"
              className="secondary"
              onClick={() => setCursor(table.next_cursor)}
            >
              {t("research.older")}
            </button>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
