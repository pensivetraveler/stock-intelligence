import type {
  ImportedExecution,
  ManualExecution,
  MarketBarsResponse,
  PriceBar,
  ScenarioDefinition,
  SessionWindow,
} from "@siw/contracts/types";
import { useCallback, useEffect, useMemo, useState } from "react";

import { isUncertain, summarize, toSeries } from "./chart-data";
import { useI18n, type Translate } from "./i18n";
import type { ChartSyncGroup } from "./chart-sync";
import { journalMarkers } from "./journal-markers";
import {
  MarketApiError,
  TIMEFRAMES,
  type InstrumentListing,
  type MarketApi,
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
  /** The chart that the watchlist and keyboard act on. */
  active?: boolean;
  onActivate?: () => void;
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

function shiftDate(date: string, days: number): string {
  const value = new Date(`${date}T00:00:00Z`);
  value.setUTCDate(value.getUTCDate() + days);
  return value.toISOString().slice(0, 10);
}

/** Change of the bar from its open, for the legend only (display, not accounting). */
function barChange(bar: PriceBar): { text: string; rising: boolean } {
  const open = Number(bar.open);
  const delta = Number(bar.close) - open;
  const percent = open === 0 ? 0 : (delta / open) * 100;
  const sign = delta >= 0 ? "+" : "";
  return {
    text: `${sign}${delta.toFixed(2)} (${sign}${percent.toFixed(2)}%)`,
    rising: delta >= 0,
  };
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
  active = false,
  onActivate,
}: ChartPanelProps) {
  const { t } = useI18n();
  const [load, setLoad] = useState<Load>({ status: "loading" });
  const [selected, setSelected] = useState<PriceBar | null>(null);
  const [hovered, setHovered] = useState<number | null>(null);
  const [magnet, setMagnet] = useState(false);
  const [logScale, setLogScale] = useState(false);
  const [fitRequest, setFitRequest] = useState(0);
  const [search, setSearch] = useState<string | null>(null);
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
  const legendBar =
    (hovered !== null &&
      bars?.find(
        (item) =>
          Math.floor(Date.parse(item.interval_start_utc) / 1000) === hovered,
      )) ||
    bars?.at(-1) ||
    null;
  const change = legendBar ? barChange(legendBar) : null;
  const matches = instruments.filter((item) =>
    item.instrument.symbol
      .toLowerCase()
      .includes((search ?? "").trim().toLowerCase()),
  );
  const chooseInstrument = (id: string) => {
    setSearch(null);
    onChange({ ...config, instrumentId: id });
  };

  return (
    <section
      className={active ? "chart-panel active" : "chart-panel"}
      aria-label={`${title}: ${symbol} ${timeframe}`}
      onPointerDown={onActivate}
    >
      <header className="chart-toolbar">
        <h2>{title}</h2>
        <div className="symbol-search">
          <button
            type="button"
            className="toolbar-button symbol-button"
            aria-label={t("chart.instrument")}
            aria-expanded={search !== null}
            onClick={() => setSearch(search === null ? "" : null)}
          >
            {symbol}
          </button>
          {search !== null ? (
            <div
              className="symbol-popover"
              role="dialog"
              aria-label={t("chart.searchSymbol")}
            >
              <input
                autoFocus
                value={search}
                placeholder={t("chart.searchSymbol")}
                aria-label={t("chart.searchSymbol")}
                onChange={(event) => setSearch(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") setSearch(null);
                  if (event.key === "Enter" && matches[0])
                    chooseInstrument(matches[0].instrument.instrument_id);
                }}
              />
              <ul>
                {matches.map((item) => (
                  <li key={item.instrument.instrument_id}>
                    <button
                      type="button"
                      onClick={() =>
                        chooseInstrument(item.instrument.instrument_id)
                      }
                    >
                      <strong>{item.instrument.symbol}</strong>
                      <small>
                        {item.instrument.venue} · {item.source_id}
                      </small>
                    </button>
                  </li>
                ))}
                {matches.length === 0 ? (
                  <li className="symbol-empty">{t("chart.noMatch")}</li>
                ) : null}
              </ul>
            </div>
          ) : null}
        </div>
        <div
          className="segmented"
          role="group"
          aria-label={t("chart.timeframe")}
        >
          {TIMEFRAMES.map((value) => (
            <button
              key={value}
              type="button"
              aria-pressed={value === timeframe}
              onClick={() => onChange({ ...config, timeframe: value })}
            >
              {value}
            </button>
          ))}
        </div>
        <div className="segmented" role="group" aria-label={t("chart.hours")}>
          {(["regular", "extended"] as SessionScope[]).map((value) => (
            <button
              key={value}
              type="button"
              aria-pressed={value === sessionScope}
              onClick={() => onChange({ ...config, sessionScope: value })}
            >
              {t(value === "regular" ? "chart.regular" : "chart.extended")}
            </button>
          ))}
        </div>
        <div className="date-stepper">
          <button
            type="button"
            className="toolbar-button"
            aria-label={t("chart.previousDay")}
            onClick={() =>
              onChange({ ...config, sessionDate: shiftDate(sessionDate, -1) })
            }
          >
            ‹
          </button>
          <input
            type="date"
            aria-label={t("chart.sessionDate")}
            value={sessionDate}
            onChange={(event) =>
              event.target.value &&
              onChange({ ...config, sessionDate: event.target.value })
            }
          />
          <button
            type="button"
            className="toolbar-button"
            aria-label={t("chart.nextDay")}
            onClick={() =>
              onChange({ ...config, sessionDate: shiftDate(sessionDate, 1) })
            }
          >
            ›
          </button>
        </div>
      </header>

      <div className="chart-frame">
        <div
          className="tool-strip"
          role="toolbar"
          aria-label={t("chart.tools")}
          aria-orientation="vertical"
        >
          <button
            type="button"
            aria-pressed={magnet}
            title={t("chart.magnet")}
            aria-label={t("chart.magnet")}
            onClick={() => setMagnet(!magnet)}
          >
            <svg viewBox="0 0 16 16" aria-hidden="true">
              <path d="M3 2h3v6a2 2 0 0 0 4 0V2h3v6a5 5 0 0 1-10 0z" />
            </svg>
          </button>
          <button
            type="button"
            aria-pressed={logScale}
            title={t("chart.logScale")}
            aria-label={t("chart.logScale")}
            onClick={() => setLogScale(!logScale)}
          >
            <span className="tool-text">log</span>
          </button>
          <button
            type="button"
            title={t("chart.fit")}
            aria-label={t("chart.fit")}
            onClick={() => setFitRequest((count) => count + 1)}
          >
            <svg viewBox="0 0 16 16" aria-hidden="true">
              <path d="M2 6V2h4M10 2h4v4M14 10v4h-4M6 14H2v-4" />
            </svg>
          </button>
        </div>
        <div className="chart-body">
          {load.status === "ready" && series ? (
            <>
              <PriceChart
                chartId={chartId}
                candles={series.candles}
                volume={series.volume}
                sync={sync}
                markers={markers}
                onCandleClick={selectCandle}
                onHover={setHovered}
                magnet={magnet}
                logScale={logScale}
                fitRequest={fitRequest}
              />
              {legendBar && change ? (
                <div className="chart-legend" aria-live="off">
                  <span className="legend-title">
                    {symbol} · {timeframe} · {load.response.coverage.source_id}
                  </span>
                  <span>
                    {t("legend.open")} <b>{legendBar.open}</b>
                  </span>
                  <span>
                    {t("legend.high")} <b>{legendBar.high}</b>
                  </span>
                  <span>
                    {t("legend.low")} <b>{legendBar.low}</b>
                  </span>
                  <span>
                    {t("legend.close")} <b>{legendBar.close}</b>
                  </span>
                  <span className={change.rising ? "legend-up" : "legend-down"}>
                    {change.text}
                  </span>
                  <span>
                    {t("legend.volume")} <b>{legendBar.volume}</b>
                  </span>
                  {isUncertain(legendBar) ? (
                    <span className="legend-provisional">
                      {t("legend.notFinal")}
                    </span>
                  ) : null}
                </div>
              ) : null}
            </>
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
