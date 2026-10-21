import type {
  BacktestFill,
  ImportedExecution,
  ManualExecution,
  MarketBarsResponse,
  PriceBar,
  ScenarioDefinition,
  SessionWindow,
} from "@siw/contracts/types";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { zoneOf } from "./market-zone";
import { isUncertain, summarize, toSeries } from "./chart-data";
import type { Drawing, DrawingTool } from "./drawings";
import { barEnd, barsAsOf } from "./replay";
import {
  dailyRange,
  DEFAULT_CANDLES,
  isDailyTimeframe,
  sessionsForCandles,
  timeframeLabel,
  timeframeSeconds,
} from "./timeframes";
import {
  cleanParams,
  instanceLookback,
  paramsText,
  renderIndicators,
  warmupBars,
  type IndicatorInstance,
  type OhlcvBar,
} from "./indicators";
import { useI18n, type Translate } from "./i18n";
import type { ChartSyncGroup } from "./chart-sync";
import type { CustomIndicatorApi } from "./custom-indicator-api";
import { FormulaFields } from "./FormulaEditor";
import {
  higherBarsOf,
  higherChoices,
  higherRange,
  isHigherTimeframe,
  type HigherBars,
  type HigherTimeframe,
} from "./higher-timeframe";
import {
  customIndicator,
  hasResult,
  latestCustom,
  isCustomType,
  signature,
  storeResult,
} from "./custom-indicators";
import { backtestMarkers, journalMarkers } from "./journal-markers";
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

/** Earlier sessions searched for indicator warm-up. */
const WARMUP_SESSIONS = 10;
/** Earlier sessions requested at the same time while history loads. */
const HISTORY_REQUESTS = 4;

function previousDay(date: string): string {
  const value = new Date(`${date}T00:00:00Z`);
  value.setUTCDate(value.getUTCDate() - 1);
  return value.toISOString().slice(0, 10);
}

function toOhlcv(bars: PriceBar[]): OhlcvBar[] {
  return bars.map((bar) => ({
    time: Math.floor(Date.parse(bar.interval_start_utc) / 1000),
    open: Number(bar.open),
    high: Number(bar.high),
    low: Number(bar.low),
    close: Number(bar.close),
    volume: Number(bar.volume),
  }));
}

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
  /** Ask the review Agent about this chart up to a candle's end. */
  onAskAgent?: (request: {
    instrumentId: string;
    timeframe: string;
    cutoffUtc: string;
  }) => void;
  /** Computes Python indicators (ADR 0023); without it they are not drawn. */
  customApi?: CustomIndicatorApi;
  /** Bumped when the list of Python indicator versions changes. */
  customVersion?: number;
  /** Told the bars indicators are computed on (warm-up included), e.g. for previews. */
  onBars?: (bars: OhlcvBar[]) => void;
  /** Fills of the backtest run the user chose to show on the charts. */
  backtestFills?: BacktestFill[];
  /** The chart that the toolbars and the watchlist act on. */
  active?: boolean;
  onActivate?: () => void;
  magnet?: boolean;
  logScale?: boolean;
  fitRequest?: number;
  /** Told whether this chart has finished loading (ready, closed or failed). */
  onSettledChange?: (settled: boolean) => void;
  /** Replay "now" in epoch seconds; bars ending later are hidden. Null when off. */
  replayTime?: number | null;
  /** When true, a candle click picks the replay start instead of opening trade entry. */
  replayChoosing?: boolean;
  onReplayPick?: (time: number) => void;
  /** End times of the first and last loaded bar, for replay stepping limits. */
  onReplayBounds?: (first: number | null, last: number | null) => void;
  /** Replace this chart's indicators (settings, visibility, removal). */
  onIndicatorsChange?: (indicators: IndicatorInstance[]) => void;
  /** The drawing tool in use; only the active chart receives one. */
  tool?: DrawingTool;
  onDrawingsChange?: (drawings: Drawing[]) => void;
  onToolDone?: () => void;
  newId?: () => string;
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
  onAskAgent,
  customApi,
  customVersion = 0,
  onBars,
  backtestFills,
  active = false,
  onActivate,
  magnet = false,
  logScale = false,
  fitRequest = 0,
  onSettledChange,
  replayTime = null,
  replayChoosing = false,
  onReplayPick,
  onReplayBounds,
  onIndicatorsChange,
  tool = "cursor",
  onDrawingsChange,
  onToolDone,
  newId = () => crypto.randomUUID(),
}: ChartPanelProps) {
  const { t, language } = useI18n();
  const [load, setLoad] = useState<Load>({ status: "loading" });
  const [selected, setSelected] = useState<PriceBar | null>(null);
  const [hovered, setHovered] = useState<number | null>(null);
  // Bars from earlier sessions, used only to warm the indicators up; never drawn.
  const [warmup, setWarmup] = useState<PriceBar[]>([]);
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
        const session = await api.session(
          sessionDate,
          zoneOf(instrumentId).calendarId,
        );
        // Daily and weekly charts end with the chosen day even when it is closed.
        const range = isDailyTimeframe(timeframe)
          ? dailyRange(timeframe, sessionDate)
          : sessionRange(session, sessionScope);
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

  // Earlier sessions shown before the chart's day, so it holds about DEFAULT_CANDLES
  // bars. They arrive newest first, a few at a time, and fill in behind today's bars.
  const historySessions = sessionsForCandles(timeframe, sessionScope) - 1;
  const [history, setHistory] = useState<{
    sessions: SessionWindow[];
    bars: Map<string, PriceBar[]>;
    /** All requested sessions answered (or failed); warm-up waits for this. */
    done: boolean;
  }>({ sessions: [], bars: new Map(), done: false });
  useEffect(() => {
    let current = true;
    const finish = () => {
      if (current) setHistory((previous) => ({ ...previous, done: true }));
    };
    setHistory({ sessions: [], bars: new Map(), done: false });
    // Not ready yet: stay "not done" so warm-up does not start from the wrong day.
    if (unavailable || !instrumentsReady) return;
    if (historySessions <= 0) {
      finish();
      return;
    }
    void (async () => {
      let sessions: SessionWindow[];
      try {
        sessions = await api.sessions(
          previousDay(sessionDate),
          historySessions,
          zoneOf(instrumentId).calendarId,
        );
      } catch {
        finish(); // History is best effort: the chart still shows its own day.
        return;
      }
      if (!current) return;
      setHistory({ sessions, bars: new Map(), done: false });
      const queue = [...sessions].reverse(); // newest first
      const worker = async () => {
        for (let item = queue.shift(); item; item = queue.shift()) {
          const range = sessionRange(item, sessionScope);
          if (range === null) continue;
          try {
            const response = await api.bars({
              instrumentId,
              timeframe,
              ...range,
            });
            if (!current) return;
            setHistory((previous) => {
              const bars = new Map(previous.bars);
              bars.set(item.session_date, response.bars);
              return { ...previous, bars };
            });
          } catch {
            // A missing session leaves a gap in history, never wrong bars.
          }
        }
      };
      await Promise.all(Array.from({ length: HISTORY_REQUESTS }, worker));
      finish();
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
    historySessions,
    unavailable,
    instrumentsReady,
  ]);
  // Only a contiguous run of loaded sessions back from the chart's day is shown, so a
  // slow older session never appears before the ones between it and today.
  const historyBars = useMemo(() => {
    const loaded: PriceBar[][] = [];
    for (let i = history.sessions.length - 1; i >= 0; i -= 1) {
      const bars = history.bars.get(history.sessions[i].session_date);
      if (!bars) break;
      loaded.unshift(bars);
    }
    return loaded.flat();
  }, [history]);
  const earliestDate =
    history.sessions.length > 0 && historyBars.length > 0
      ? (history.sessions.find((item) => history.bars.has(item.session_date))
          ?.session_date ?? sessionDate)
      : sessionDate;

  // Daily and weekly charts already load about 1,200 bars, enough to warm up.
  const neededWarmup = isDailyTimeframe(timeframe)
    ? 0
    : warmupBars(config.indicators ?? []);
  useEffect(() => {
    let current = true;
    setWarmup([]);
    // Start once history is complete, so warm-up begins before the earliest shown day.
    if (
      neededWarmup === 0 ||
      unavailable ||
      !instrumentsReady ||
      !history.done
    ) {
      return;
    }
    void (async () => {
      const collected: PriceBar[][] = [];
      let count = 0;
      try {
        const sessions = await api.sessions(
          previousDay(earliestDate),
          WARMUP_SESSIONS,
          zoneOf(instrumentId).calendarId,
        );
        for (const item of [...sessions].reverse()) {
          if (count >= neededWarmup || !current) break;
          const range = sessionRange(item, sessionScope);
          if (range === null) continue;
          const response = await api.bars({
            instrumentId,
            timeframe,
            ...range,
          });
          collected.unshift(response.bars);
          count += response.bars.length;
        }
      } catch {
        // Warm-up is best effort: indicators then start later in the day.
      }
      if (current) setWarmup(collected.flat());
    })();
    return () => {
      current = false;
    };
  }, [
    api,
    instrumentId,
    timeframe,
    earliestDate,
    history.done,
    sessionScope,
    neededWarmup,
    unavailable,
    instrumentsReady,
  ]);
  const dayBars = load.status === "ready" ? load.response.bars : null;
  const allBars = useMemo(
    () =>
      dayBars === null
        ? null
        : // The most recent DEFAULT_CANDLES bars, always including the whole day.
          [...historyBars, ...dayBars].slice(
            -Math.max(DEFAULT_CANDLES, dayBars.length),
          ),
    [historyBars, dayBars],
  );

  // Replay asks the API for the session as of the replay time, so bars this account
  // could not yet have had (a feed's publication delay, unfinished bars) never reach
  // the screen. Requests are coalesced: while one is in flight, only the latest
  // replay time is requested next.
  const [asOf, setAsOf] = useState<{
    time: number;
    response: MarketBarsResponse;
  } | null>(null);
  const wantedAsOf = useRef<number | null>(null);
  const asOfBusy = useRef(false);
  const asOfKey = `${instrumentId}|${timeframe}|${sessionDate}|${sessionScope}`;
  const asOfKeyRef = useRef(asOfKey);
  asOfKeyRef.current = asOfKey;
  const loadSession = load.status === "ready" ? load.session : null;
  useEffect(() => {
    setAsOf(null);
  }, [asOfKey]);
  useEffect(() => {
    wantedAsOf.current = replayTime;
    if (replayTime === null) {
      setAsOf(null);
      return;
    }
    if (asOfBusy.current || loadSession === null) return;
    const range = isDailyTimeframe(timeframe)
      ? dailyRange(timeframe, sessionDate)
      : sessionRange(loadSession, sessionScope);
    if (range === null) return;
    const key = asOfKey;
    asOfBusy.current = true;
    void (async () => {
      try {
        while (wantedAsOf.current !== null && asOfKeyRef.current === key) {
          const time = wantedAsOf.current;
          const response = await api.bars({
            instrumentId,
            timeframe,
            ...range,
            asOfUtc: new Date(time * 1000).toISOString().replace(".000Z", "Z"),
          });
          if (asOfKeyRef.current !== key) break;
          setAsOf({ time, response });
          if (wantedAsOf.current === time) break;
        }
      } catch {
        // Keep the last as-of bars; the client-side cut below still applies.
      } finally {
        asOfBusy.current = false;
      }
    })();
  }, [
    api,
    replayTime,
    loadSession,
    instrumentId,
    timeframe,
    sessionDate,
    sessionScope,
    asOfKey,
  ]);

  // During replay everything below (chart, legend, indicators, markers, candle
  // clicks) sees only the bars that had closed by the replay time; once the API's
  // as-of answer arrives, only the bars it says were available by then.
  const bars = useMemo(() => {
    if (replayTime === null) return allBars;
    const source = asOf ? [...historyBars, ...asOf.response.bars] : allBars;
    return source ? barsAsOf(source, replayTime) : null;
  }, [allBars, historyBars, replayTime, asOf]);
  const series = useMemo(() => (bars ? toSeries(bars) : null), [bars]);
  // In replay the summary describes the as-of answer; the whole-day one is hidden.
  const summary =
    load.status !== "ready"
      ? null
      : replayTime === null
        ? summarize(load.response, t, zoneOf(instrumentId))
        : asOf
          ? summarize(asOf.response, t, zoneOf(instrumentId))
          : null;
  useEffect(() => {
    onReplayBounds?.(
      allBars && allBars.length > 0 ? barEnd(allBars[0]) : null,
      allBars && allBars.length > 0
        ? barEnd(allBars[allBars.length - 1])
        : null,
    );
  }, [allBars, onReplayBounds]);
  const markers = useMemo(
    () =>
      bars
        ? [
            ...journalMarkers(
              bars,
              instrumentId,
              journal.manual,
              journal.scenarios,
              journal.imported,
            ),
            ...backtestMarkers(bars, instrumentId, backtestFills ?? []),
          ].sort((a, b) => a.time - b.time || a.id.localeCompare(b.id))
        : [],
    [bars, instrumentId, journal, backtestFills],
  );
  const selectCandle = useCallback(
    (time: number) => {
      const bar = bars?.find(
        (item) =>
          Math.floor(Date.parse(item.interval_start_utc) / 1000) === time,
      );
      if (!bar) return;
      if (replayChoosing) {
        onReplayPick?.(barEnd(bar));
        return;
      }
      setSelected(bar);
    },
    [bars, replayChoosing, onReplayPick],
  );
  const symbol =
    instruments.find((item) => item.instrument.instrument_id === instrumentId)
      ?.instrument.symbol ?? instrumentId;
  const indicatorList = config.indicators ?? [];
  const ohlcv = useMemo<OhlcvBar[]>(() => {
    const shown = toOhlcv(bars ?? []);
    const firstShown = shown[0]?.time ?? Number.POSITIVE_INFINITY;
    // Only bars strictly before the shown day can warm up (no overlap, no future).
    return [
      ...toOhlcv(warmup).filter((bar) => bar.time < firstShown),
      ...shown,
    ];
  }, [bars, warmup]);
  const warmupCount = ohlcv.length - (bars?.length ?? 0);
  useEffect(() => onBars?.(ohlcv), [ohlcv, onBars]);
  // Each chart bar's end, for placing larger-timeframe values without look-ahead.
  const ohlcvEnds = useMemo<number[]>(() => {
    const shown = bars ?? [];
    const firstShown = shown[0]
      ? Date.parse(shown[0].interval_start_utc)
      : Number.POSITIVE_INFINITY;
    return [
      ...warmup.filter(
        (bar) => Date.parse(bar.interval_start_utc) < firstShown,
      ),
      ...shown,
    ].map((bar) => Math.floor(Date.parse(bar.interval_end_utc) / 1000));
  }, [bars, warmup]);
  // Larger-timeframe bars (daily, weekly) for indicators set to a larger timeframe:
  // one request per timeframe, kept by request so a finished one is never dropped.
  const higherRequests = useMemo(() => {
    const wanted = new Map<HigherTimeframe, number>();
    for (const instance of indicatorList) {
      if (!instance.timeframe) continue;
      wanted.set(
        instance.timeframe,
        Math.max(
          wanted.get(instance.timeframe) ?? 0,
          instanceLookback(instance),
        ),
      );
    }
    if (ohlcv.length === 0) return [];
    return [...wanted].map(([timeframe, lookback]) => {
      const range = higherRange(
        ohlcv[0].time,
        ohlcvEnds[ohlcvEnds.length - 1],
        lookback,
        timeframe,
      );
      // A new request when the instrument, the start or the last day changes.
      return {
        timeframe,
        range,
        key: `${instrumentId}|${timeframe}|${range.startUtc}|${range.endUtc.slice(0, 10)}`,
      };
    });
    // The list is replaced (never mutated) whenever settings change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [instrumentId, ohlcv, ohlcvEnds, config.indicators]);
  const [higherCache, setHigherCache] = useState<Record<string, HigherBars>>(
    {},
  );
  const [higherErrors, setHigherErrors] = useState<Record<string, string>>({});
  const higherPending = useRef(new Set<string>());
  useEffect(() => {
    for (const { timeframe, range, key } of higherRequests) {
      if (higherCache[key] || higherPending.current.has(key)) continue;
      higherPending.current.add(key);
      api
        .bars({ instrumentId: key.split("|")[0], timeframe, ...range })
        .then((response) => {
          setHigherCache((cache) => ({
            ...cache,
            [key]: higherBarsOf(response.bars),
          }));
          setHigherErrors((errors) => {
            const rest = { ...errors };
            delete rest[timeframe];
            return rest;
          });
        })
        .catch((error: unknown) =>
          setHigherErrors((errors) => ({
            ...errors,
            [timeframe]: t("ind.higherFailed", {
              timeframe: timeframe.toUpperCase(),
              error: error instanceof Error ? error.message : String(error),
            }),
          })),
        )
        .finally(() => higherPending.current.delete(key));
    }
  }, [api, higherRequests, higherCache, t]);
  const higher = useMemo(
    () =>
      Object.fromEntries(
        higherRequests.flatMap(({ timeframe, key }) =>
          higherCache[key] ? [[timeframe, higherCache[key]]] : [],
        ),
      ) as Partial<Record<HigherTimeframe, HigherBars>>,
    [higherRequests, higherCache],
  );
  // Python indicators: computed by the API for exactly these bars and settings.
  const [customTick, setCustomTick] = useState(0);
  const [customErrors, setCustomErrors] = useState<Record<string, string>>({});
  const customPending = useRef(new Set<string>());
  useEffect(() => {
    if (!customApi || ohlcv.length === 0) return;
    for (const instance of indicatorList) {
      if (!isCustomType(instance.type)) continue;
      const item = customIndicator(instance.type);
      if (!item) continue;
      const key = signature(instance.type, instance.params, ohlcv);
      if (hasResult(key) || customPending.current.has(key)) continue;
      customPending.current.add(key);
      customApi
        .compute(
          item.indicator_id,
          item.version,
          instance.params,
          ohlcv,
          zoneOf(instrumentId).timeZone,
        )
        .then((result) => {
          storeResult(key, result.outputs as Record<string, (number | null)[]>);
          setCustomErrors((errors) => {
            const rest = { ...errors };
            delete rest[instance.key];
            return rest;
          });
          setCustomTick((tick) => tick + 1);
        })
        .catch((error: unknown) =>
          setCustomErrors((errors) => ({
            ...errors,
            [instance.key]: `${item.name}: ${error instanceof Error ? error.message : String(error)}`,
          })),
        )
        .finally(() => customPending.current.delete(key));
    }
    // The list is replaced (never mutated) whenever settings change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [customApi, ohlcv, config.indicators, customVersion, instrumentId]);
  const rendered = useMemo(
    () =>
      renderIndicators(
        ohlcv,
        indicatorList,
        warmupCount,
        { timeZone: zoneOf(instrumentId).timeZone },
        { bars: higher, ends: ohlcvEnds },
      ),
    // The list is replaced (never mutated) whenever settings change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      ohlcv,
      config.indicators,
      warmupCount,
      instrumentId,
      customTick,
      customVersion,
      higher,
      ohlcvEnds,
    ],
  );
  // Chart's own timeframe or a larger one; Python indicators stay on the chart's bars.
  const timeframeField = (instance: IndicatorInstance) =>
    isCustomType(instance.type) ||
    higherChoices(config.timeframe).length === 0 ? null : (
      <label>
        {t("ind.timeframe")}
        <select
          value={instance.timeframe ?? ""}
          onChange={(event) => {
            const value = event.target.value;
            changeIndicator(instance.key, (current) => {
              const next = { ...current };
              if (isHigherTimeframe(value)) next.timeframe = value;
              else delete next.timeframe;
              return next;
            });
          }}
        >
          <option value="">
            {t("ind.timeframeChart", { timeframe: config.timeframe })}
          </option>
          {higherChoices(config.timeframe).map((choice) => (
            <option key={choice} value={choice}>
              {choice.toUpperCase()}
            </option>
          ))}
        </select>
      </label>
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
              fitKey={`${instrumentId}|${timeframe}|${sessionDate}|${sessionScope}`}
              daily={isDailyTimeframe(timeframe)}
              drawings={(config.drawings ?? []).filter(
                (item) => item.instrumentId === instrumentId,
              )}
              tool={tool}
              instrumentId={instrumentId}
              barSeconds={timeframeSeconds(timeframe)}
              onDrawingCreate={(drawing) =>
                onDrawingsChange?.([
                  ...(config.drawings ?? []),
                  { ...drawing, id: newId() },
                ])
              }
              onDrawingDelete={(id) =>
                onDrawingsChange?.(
                  (config.drawings ?? []).filter((item) => item.id !== id),
                )
              }
              onToolDone={onToolDone}
              drawingsSelectable={!replayChoosing}
            />
            {legendBar && change ? (
              <div className="chart-legend" aria-live="off">
                <span className="legend-title">
                  {symbol} · {timeframeLabel(timeframe)} ·{" "}
                  {load.response.coverage.source_id}
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
                          {item.instance.timeframe
                            ? ` · ${item.instance.timeframe.toUpperCase()}`
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
                          {item.definition.inputs.length > 0 ||
                          item.instance.type === "formula" ||
                          (higherChoices(config.timeframe).length > 0 &&
                            !isCustomType(item.instance.type)) ? (
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
                          {(() => {
                            const current = customIndicator(item.instance.type);
                            const latest = current
                              ? latestCustom().find(
                                  (entry) =>
                                    entry.indicator_id === current.indicator_id,
                                )
                              : undefined;
                            if (
                              !current ||
                              !latest ||
                              latest.version <= current.version
                            )
                              return null;
                            return (
                              <button
                                type="button"
                                aria-label={t("pyind.upgrade", {
                                  name: item.definition.short,
                                  version: latest.version,
                                })}
                                title={t("pyind.upgrade", {
                                  name: item.definition.short,
                                  version: latest.version,
                                })}
                                onClick={() =>
                                  changeIndicator(item.key, (instance) => ({
                                    ...instance,
                                    type: latest.type,
                                    params: Object.fromEntries(
                                      latest.inputs.map((input) => [
                                        input.key,
                                        instance.params[input.key] ??
                                          input.default,
                                      ]),
                                    ),
                                  }))
                                }
                              >
                                ↑v{latest.version}
                              </button>
                            );
                          })()}
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
                        {editing === item.key &&
                        item.instance.type === "formula" ? (
                          <div
                            className="popover indicator-settings formula-settings"
                            role="dialog"
                            aria-label={t("ind.settings", {
                              name: item.definition.short,
                            })}
                          >
                            {timeframeField(item.instance)}
                            <FormulaFields
                              initial={{
                                label: item.instance.label ?? "",
                                formula: item.instance.formula ?? "",
                                pane: item.instance.pane ?? "separate",
                              }}
                              submitLabel={t("ind.done")}
                              onCancel={() => setEditing(null)}
                              onSubmit={(value) => {
                                changeIndicator(item.key, (current) => ({
                                  ...current,
                                  ...value,
                                }));
                                setEditing(null);
                              }}
                            />
                          </div>
                        ) : null}
                        {editing === item.key &&
                        item.instance.type !== "formula" ? (
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
                            {timeframeField(item.instance)}
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
            onAskAgent={
              onAskAgent
                ? () => {
                    onAskAgent({
                      instrumentId,
                      timeframe: config.timeframe,
                      cutoffUtc: selected.interval_end_utc,
                    });
                    setSelected(null);
                  }
                : undefined
            }
          />
        </div>
      ) : null}

      {Object.values(higherErrors).map((message) => (
        <p key={message} className="bt-problem chart-custom-error" role="alert">
          {message}
        </p>
      ))}
      {Object.values(customErrors).map((message) => (
        <p key={message} className="bt-problem chart-custom-error" role="alert">
          {message}
        </p>
      ))}
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
