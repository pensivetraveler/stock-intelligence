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
  | { status: "error"; message: string };

interface ChartPanelProps {
  chartId: string;
  title: string;
  api: MarketApi;
  config: ChartConfig;
  instruments: InstrumentListing[];
  sync: ChartSyncGroup;
  onChange: (config: ChartConfig) => void;
  journal: {
    manual: ManualExecution[];
    imported: ImportedExecution[];
    scenarios: ScenarioDefinition[];
  };
  onJournalChange: () => void;
}

function describeError(error: unknown): string {
  if (error instanceof MarketApiError) {
    return error.requestId
      ? `${error.message} (request ${error.requestId})`
      : error.message;
  }
  return error instanceof Error ? error.message : "Request failed";
}

export function ChartPanel({
  chartId,
  title,
  api,
  config,
  instruments,
  sync,
  onChange,
  journal,
  onJournalChange,
}: ChartPanelProps) {
  const [load, setLoad] = useState<Load>({ status: "loading" });
  const [selected, setSelected] = useState<PriceBar | null>(null);
  const { instrumentId, timeframe, sessionDate, sessionScope } = config;

  useEffect(() => {
    let current = true;
    setLoad({ status: "loading" });
    setSelected(null);
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
        if (current)
          setLoad({ status: "error", message: describeError(error) });
      }
    })();
    return () => {
      current = false;
    };
  }, [api, instrumentId, timeframe, sessionDate, sessionScope]);

  const series = useMemo(
    () => (load.status === "ready" ? toSeries(load.response.bars) : null),
    [load],
  );
  const summary = load.status === "ready" ? summarize(load.response) : null;
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
          Instrument
          <select
            value={instrumentId}
            onChange={(event) =>
              onChange({ ...config, instrumentId: event.target.value })
            }
          >
            {instruments.length === 0 ? (
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
          Timeframe
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
          Session date
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
          Hours
          <select
            value={sessionScope}
            onChange={(event) =>
              onChange({
                ...config,
                sessionScope: event.target.value as SessionScope,
              })
            }
          >
            <option value="regular">Regular</option>
            <option value="extended">Extended</option>
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
            {load.status === "loading" && "Loading bars…"}
            {load.status === "closed" &&
              `${load.session.session_date} is not a trading day.`}
            {load.status === "error" && load.message}
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
        <p className="workspace-note">
          Click a candle to record an actual execution or a hypothetical
          scenario.
        </p>
      ) : null}

      {summary && load.status === "ready" ? (
        <dl className="chart-meta">
          <div>
            <dt>Coverage</dt>
            <dd>
              <span className={`coverage coverage-${summary.coverage}`}>
                {summary.coverage}
              </span>{" "}
              {summary.intervals}
            </dd>
          </div>
          <div>
            <dt>Bar quality</dt>
            <dd>{summary.quality}</dd>
          </div>
          <div>
            <dt>Sessions</dt>
            <dd>
              {summary.sessions}
              {load.session.is_early_close ? " · early close" : ""}
            </dd>
          </div>
          <div>
            <dt>Source</dt>
            <dd className="lineage">{summary.lineage}</dd>
          </div>
          {summary.missing.length > 0 ? (
            <div>
              <dt>Missing</dt>
              <dd>
                {summary.missing.slice(0, 3).join("; ")}
                {summary.missing.length > 3
                  ? ` and ${summary.missing.length - 3} more`
                  : ""}
              </dd>
            </div>
          ) : null}
        </dl>
      ) : null}
    </section>
  );
}
