import type {
  ImportedExecution,
  ManualExecution,
  MarketBarsResponse,
  PriceBar,
  ScenarioDefinition,
  SessionWindow,
} from "@siw/contracts/types";
import { useCallback, useEffect, useMemo, useState } from "react";

import { summarize, toSeries } from "./chart-data";
import { useI18n, type Translate } from "./i18n";
import type { ChartSyncGroup } from "./chart-sync";
import { journalMarkers } from "./journal-markers";
import {
  MarketApiError,
  TIMEFRAMES,
  type InstrumentListing,
  type MarketApi,
  type Timeframe,
} from "./market-api";
import { PriceChart } from "./PriceChart";
import { TradeEntry } from "./TradeEntry";
import {
  sessionRange,
  type ChartConfig,
  type SessionScope,
} from "./workspace-state";

type Load =
  | { status: "loading" }
  | { status: "closed"; session: SessionWindow }
  | { status: "ready"; session: SessionWindow; response: MarketBarsResponse }
  // The error is kept raw and worded at render time, in the current language.
  | { status: "error"; error: unknown };

const NOT_OFFERED = Symbol("not offered");

interface ChartPanelProps {
  chartId: string;
  title: string;
  api: MarketApi;
  config: ChartConfig;
  instruments: InstrumentListing[];
  /** False until the instrument list has loaded (or failed), so no request races it. */
  instrumentsReady?: boolean;
  sync: ChartSyncGroup;
  onChange: (config: ChartConfig) => void;
  journal: {
    manual: ManualExecution[];
    imported: ImportedExecution[];
    scenarios: ScenarioDefinition[];
  };
  onJournalChange: () => void;
}

function describeError(t: Translate, error: unknown): string {
  if (error === NOT_OFFERED) {
    return t("chart.notOffered");
  }
  if (error instanceof MarketApiError) {
    return error.requestId
      ? t("chart.request", { message: error.message, id: error.requestId })
      : error.message;
  }
  return error instanceof Error ? error.message : t("common.requestFailed");
}

export function ChartPanel({
  chartId,
  title,
  api,
  config,
  instruments,
  instrumentsReady = true,
  sync,
  onChange,
  journal,
  onJournalChange,
}: ChartPanelProps) {
  const { t } = useI18n();
  const [load, setLoad] = useState<Load>({ status: "loading" });
  const [selected, setSelected] = useState<PriceBar | null>(null);
  const { instrumentId, timeframe, sessionDate, sessionScope } = config;
  // A saved layout can name an instrument the current provider does not offer (for
  // example SYNTH after switching to Alpaca); say so instead of requesting it.
  const unavailable =
    instruments.length > 0 &&
    !instruments.some((item) => item.instrument.instrument_id === instrumentId);

  useEffect(() => {
    let current = true;
    setSelected(null);
    if (!instrumentsReady) {
      setLoad({ status: "loading" });
      return;
    }
    if (unavailable) {
      setLoad({ status: "error", error: NOT_OFFERED });
      return;
    }
    setLoad({ status: "loading" });
    void (async () => {
      try {
        const session = await api.session(sessionDate);
        const range = sessionRange(session, sessionScope);
        if (range === null) {
          if (current) setLoad({ status: "closed", session });
          return;
        }
        const response = await api.bars({ instrumentId, timeframe, ...range });
        if (current) setLoad({ status: "ready", session, response });
      } catch (error) {
        if (current) setLoad({ status: "error", error });
      }
    })();
    return () => {
      current = false;
    };
  }, [
    api,
    instrumentId,
    timeframe,
    sessionDate,
    sessionScope,
    unavailable,
    instrumentsReady,
  ]);

  const series = useMemo(
    () => (load.status === "ready" ? toSeries(load.response.bars) : null),
    [load],
  );
  const summary = load.status === "ready" ? summarize(load.response, t) : null;
  const bars = load.status === "ready" ? load.response.bars : null;
  const markers = useMemo(
    () =>
      bars
        ? journalMarkers(
            bars,
            instrumentId,
            journal.manual,
            journal.scenarios,
            journal.imported,
          )
        : [],
    [bars, instrumentId, journal],
  );
  const selectCandle = useCallback(
    (time: number) => {
      const bar = bars?.find(
        (item) =>
          Math.floor(Date.parse(item.interval_start_utc) / 1000) === time,
      );
      if (bar) setSelected(bar);
    },
    [bars],
  );
  const symbol =
    instruments.find((item) => item.instrument.instrument_id === instrumentId)
      ?.instrument.symbol ?? instrumentId;

  return (
    <section
      className="chart-panel"
      aria-label={`${title}: ${symbol} ${timeframe}`}
    >
      <header className="chart-controls">
        <h2>{title}</h2>
        <label>
          {t("chart.instrument")}
          <select
            value={instrumentId}
            onChange={(event) =>
              onChange({ ...config, instrumentId: event.target.value })
            }
          >
            {instruments.length === 0 || unavailable ? (
              <option value={instrumentId}>{instrumentId}</option>
            ) : null}
            {instruments.map((item) => (
              <option
                key={item.instrument.instrument_id}
                value={item.instrument.instrument_id}
              >
                {item.instrument.symbol}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t("chart.timeframe")}
          <select
            value={timeframe}
            onChange={(event) =>
              onChange({
                ...config,
                timeframe: event.target.value as Timeframe,
              })
            }
          >
            {TIMEFRAMES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t("chart.sessionDate")}
          <input
            type="date"
            value={sessionDate}
            onChange={(event) =>
              event.target.value &&
              onChange({ ...config, sessionDate: event.target.value })
            }
          />
        </label>
        <label>
          {t("chart.hours")}
          <select
            value={sessionScope}
            onChange={(event) =>
              onChange({
                ...config,
                sessionScope: event.target.value as SessionScope,
              })
            }
          >
            <option value="regular">{t("chart.regular")}</option>
            <option value="extended">{t("chart.extended")}</option>
          </select>
        </label>
      </header>

      <div className="chart-body">
        {load.status === "ready" && series ? (
          <PriceChart
            chartId={chartId}
            candles={series.candles}
            volume={series.volume}
            sync={sync}
            markers={markers}
            onCandleClick={selectCandle}
          />
        ) : (
          <p
            className={`chart-placeholder ${load.status === "error" ? "chart-error" : ""}`}
            role="status"
          >
            {load.status === "loading" && t("chart.loading")}
            {load.status === "closed" &&
              t("chart.closed", { date: load.session.session_date })}
            {load.status === "error" && describeError(t, load.error)}
          </p>
        )}
      </div>

      {selected ? (
        <TradeEntry
          key={selected.interval_start_utc}
          api={api}
          bar={selected}
          symbol={symbol}
          sessionDate={sessionDate}
          onRecorded={onJournalChange}
          onClose={() => setSelected(null)}
        />
      ) : load.status === "ready" ? (
        <p className="workspace-note">{t("chart.clickHint")}</p>
      ) : null}

      {summary && load.status === "ready" ? (
        <dl className="chart-meta">
          <div>
            <dt>{t("chart.coverage")}</dt>
            <dd>
              <span className={`coverage coverage-${summary.coverage}`}>
                {t(`coverage.${summary.coverage}`)}
              </span>{" "}
              {summary.intervals}
            </dd>
          </div>
          <div>
            <dt>{t("chart.quality")}</dt>
            <dd>{summary.quality}</dd>
          </div>
          <div>
            <dt>{t("chart.sessions")}</dt>
            <dd>
              {summary.sessions}
              {load.session.is_early_close ? t("chart.earlyClose") : ""}
            </dd>
          </div>
          <div>
            <dt>{t("chart.source")}</dt>
            <dd className="lineage">{summary.lineage}</dd>
          </div>
          {summary.missing.length > 0 ? (
            <div>
              <dt>{t("chart.missing")}</dt>
              <dd>
                {summary.missing.slice(0, 3).join("; ")}
                {summary.missing.length > 3
                  ? t("chart.andMore", { n: summary.missing.length - 3 })
                  : ""}
              </dd>
            </div>
          ) : null}
        </dl>
      ) : null}
    </section>
  );
}
