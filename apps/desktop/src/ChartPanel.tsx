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
import {
  cleanParams,
  paramsText,
  renderIndicators,
  type IndicatorInstance,
  type OhlcvBar,
} from "./indicators";
import { useI18n, type Translate } from "./i18n";
import type { ChartSyncGroup } from "./chart-sync";
import { journalMarkers } from "./journal-markers";
import {
  MarketApiError,
  type InstrumentListing,
  type MarketApi,
} from "./market-api";
import { PriceChart } from "./PriceChart";
import { TradeEntry } from "./TradeEntry";
import { sessionRange, type ChartConfig } from "./workspace-state";

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
  journal: {
    manual: ManualExecution[];
    imported: ImportedExecution[];
    scenarios: ScenarioDefinition[];
  };
  onJournalChange: () => void;
  /** The chart that the toolbars and the watchlist act on. */
  active?: boolean;
  onActivate?: () => void;
  magnet?: boolean;
  logScale?: boolean;
  fitRequest?: number;
  /** Told whether this chart has finished loading (ready, closed or failed). */
  onSettledChange?: (settled: boolean) => void;
  /** Replace this chart's indicators (settings, visibility, removal). */
  onIndicatorsChange?: (indicators: IndicatorInstance[]) => void;
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
  journal,
  onJournalChange,
  active = false,
  onActivate,
  magnet = false,
  logScale = false,
  fitRequest = 0,
  onSettledChange,
  onIndicatorsChange,
}: ChartPanelProps) {
  const { t, language } = useI18n();
  const [load, setLoad] = useState<Load>({ status: "loading" });
  const [selected, setSelected] = useState<PriceBar | null>(null);
  const [hovered, setHovered] = useState<number | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
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

  const settled = load.status !== "loading";
  useEffect(() => onSettledChange?.(settled), [onSettledChange, settled]);

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
  const indicatorList = config.indicators ?? [];
  const ohlcv = useMemo<OhlcvBar[]>(
    () =>
      (bars ?? []).map((bar) => ({
        time: Math.floor(Date.parse(bar.interval_start_utc) / 1000),
        open: Number(bar.open),
        high: Number(bar.high),
        low: Number(bar.low),
        close: Number(bar.close),
        volume: Number(bar.volume),
      })),
    [bars],
  );
  const rendered = useMemo(
    () => renderIndicators(ohlcv, indicatorList),
    // The list is replaced (never mutated) whenever settings change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [ohlcv, config.indicators],
  );
  const changeIndicator = (
    key: string,
    update: (item: IndicatorInstance) => IndicatorInstance | null,
  ) =>
    onIndicatorsChange?.(
      indicatorList.flatMap((item) => {
        if (item.key !== key) return [item];
        const next = update(item);
        return next ? [next] : [];
      }),
    );
  const legendBar =
    (hovered !== null &&
      bars?.find(
        (item) =>
          Math.floor(Date.parse(item.interval_start_utc) / 1000) === hovered,
      )) ||
    bars?.at(-1) ||
    null;
  const change = legendBar ? barChange(legendBar) : null;
  const legendIndex = legendBar
    ? ohlcv.findIndex(
        (item) =>
          item.time ===
          Math.floor(Date.parse(legendBar.interval_start_utc) / 1000),
      )
    : -1;
  const formatValue = (value: number | null | undefined) =>
    value === null || value === undefined
      ? "—"
      : Math.abs(value) >= 1000
        ? value.toFixed(0)
        : value.toFixed(Math.abs(value) >= 10 ? 2 : 4);
  return (
    <section
      className={active ? "chart-panel active" : "chart-panel"}
      aria-label={`${title}: ${symbol} ${timeframe}`}
      onPointerDown={onActivate}
    >
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
              indicators={rendered}
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
                {rendered.length > 0 ? (
                  <ul
                    className="indicator-legend"
                    aria-label={t("ind.onChart")}
                  >
                    {rendered.map((item) => (
                      <li
                        key={item.key}
                        className={item.hidden ? "is-hidden" : undefined}
                      >
                        <span className="indicator-name">
                          {item.definition.short}
                          {item.definition.inputs.length > 0
                            ? ` ${paramsText(item.definition, item.params)}`
                            : ""}
                        </span>
                        {item.lines.map((line) => (
                          <b key={line.id} style={{ color: line.color }}>
                            {formatValue(
                              legendIndex >= 0
                                ? item.values[line.id.split(":")[1]][
                                    legendIndex
                                  ]
                                : null,
                            )}
                          </b>
                        ))}
                        <span className="indicator-actions">
                          {item.definition.inputs.length > 0 ? (
                            <button
                              type="button"
                              aria-label={t("ind.settings", {
                                name: item.definition.short,
                              })}
                              onClick={() =>
                                setEditing(
                                  editing === item.key ? null : item.key,
                                )
                              }
                            >
                              ⚙
                            </button>
                          ) : null}
                          <button
                            type="button"
                            aria-label={t(
                              item.hidden ? "ind.show" : "ind.hide",
                              {
                                name: item.definition.short,
                              },
                            )}
                            onClick={() =>
                              changeIndicator(item.key, (current) => ({
                                ...current,
                                hidden: !current.hidden,
                              }))
                            }
                          >
                            {item.hidden ? "◌" : "●"}
                          </button>
                          <button
                            type="button"
                            aria-label={t("ind.remove", {
                              name: item.definition.short,
                            })}
                            onClick={() =>
                              changeIndicator(item.key, () => null)
                            }
                          >
                            ✕
                          </button>
                        </span>
                        {editing === item.key ? (
                          <div
                            className="popover indicator-settings"
                            role="dialog"
                            aria-label={t("ind.settings", {
                              name: item.definition.short,
                            })}
                          >
                            <strong>{item.definition.name[language]}</strong>
                            {item.definition.inputs.map((field) => (
                              <label key={field.key}>
                                {field.label[language]}
                                <input
                                  type="number"
                                  min={field.min}
                                  max={field.max}
                                  step={field.step ?? 1}
                                  defaultValue={item.params[field.key]}
                                  onChange={(event) => {
                                    const value = Number(event.target.value);
                                    changeIndicator(item.key, (current) => ({
                                      ...current,
                                      params: cleanParams(item.definition, {
                                        ...current.params,
                                        [field.key]: value,
                                      }),
                                    }));
                                  }}
                                />
                              </label>
                            ))}
                            <button
                              type="button"
                              className="tb-button"
                              onClick={() => setEditing(null)}
                            >
                              {t("ind.done")}
                            </button>
                          </div>
                        ) : null}
                      </li>
                    ))}
                  </ul>
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

      {selected ? (
        <div className="trade-overlay">
          <TradeEntry
            key={selected.interval_start_utc}
            api={api}
            bar={selected}
            symbol={symbol}
            sessionDate={sessionDate}
            onRecorded={onJournalChange}
            onClose={() => setSelected(null)}
          />
        </div>
      ) : null}

      {summary && load.status === "ready" ? (
        <details className="chart-info">
          <summary>
            <span className={`coverage coverage-${summary.coverage}`}>
              {t(`coverage.${summary.coverage}`)}
            </span>{" "}
            {summary.intervals} · {t("chart.clickHint")}
          </summary>
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
        </details>
      ) : null}
    </section>
  );
}
