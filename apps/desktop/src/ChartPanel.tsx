import type {
  BacktestFill,
  BrokerOrderExecution,
  ImportedExecution,
  ManualExecution,
  MarketBarsResponse,
  PriceBar,
  ScenarioDefinition,
  SessionWindow,
} from "@siw/contracts/types";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { zoneOf } from "./market-zone";
import {
  isUncertain,
  summarize,
  toDayMarketCandles,
  toSeries,
} from "./chart-data";
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
  visibleOn,
  warmupBars,
  type IndicatorInstance,
  type OhlcvBar,
} from "./indicators";
import { useI18n, type Translate } from "./i18n";
import type { ChartSyncGroup } from "./chart-sync";
import type { CustomIndicatorApi } from "./custom-indicator-api";
import { FormulaFields } from "./FormulaEditor";
import { PineFields } from "./PineEditor";
import {
  monthlyBars,
  pineDefinition,
  pineRequests,
  type PineRequestData,
} from "./pine";
import { PineStrategyReport } from "./PineStrategyReport";
import {
  higherBarsOf,
  higherChoices,
  higherRange,
  lowerChoices,
  intradaySessions,
  isHigherTimeframe,
  isIntradayHigher,
  minutesOf,
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
import {
  backtestMarkers,
  groupMarkers,
  journalMarkers,
} from "./journal-markers";
import {
  MarketApiError,
  type InstrumentListing,
  type MarketApi,
} from "./market-api";
import {
  MAX_OLDER_BARS,
  olderDailyRange,
  olderSessionBars,
  prependOlder,
} from "./older-bars";
import { IndicatorSettings } from "./IndicatorSettings";
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
// How far back a halt is searched for the last trading day (daily bars).
const HALT_LOOKBACK_DAYS = 1_460;

function shiftDays(date: string, days: number): string {
  const value = new Date(`${date}T00:00:00Z`);
  value.setUTCDate(value.getUTCDate() + days);
  return value.toISOString().slice(0, 10);
}

function previousDay(date: string): string {
  return shiftDays(date, -1);
}

/** The day-market endpoint covers at most this many trading days per request. */
const DAY_MARKET_DATES = 14;

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
    /** Broker API fills of this chart's instrument, when the workspace has read them. */
    broker?: BrokerOrderExecution[];
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
  const { historyBars, historyFrom } = useMemo(() => {
    const loaded: PriceBar[][] = [];
    let from: string | null = null;
    for (let i = history.sessions.length - 1; i >= 0; i -= 1) {
      const bars = history.bars.get(history.sessions[i].session_date);
      if (!bars) break;
      loaded.unshift(bars);
      from = history.sessions[i].session_date;
    }
    return { historyBars: loaded.flat(), historyFrom: from };
  }, [history]);

  // Bars before the default history, loaded on request (the "older bars" button, or
  // scrolling or zooming out past the first bar), one batch of about DEFAULT_CANDLES at
  // a time. `from` is the first session date (or daily start) they cover.
  const olderKey = `${instrumentId}|${timeframe}|${sessionDate}|${sessionScope}`;
  const [older, setOlder] = useState<{
    key: string;
    bars: PriceBar[];
    from: string | null;
    busy: boolean;
    exhausted: boolean;
    error: boolean;
  }>({
    key: olderKey,
    bars: [],
    from: null,
    busy: false,
    exhausted: false,
    error: false,
  });
  // A different chart (instrument, timeframe, day or scope) starts with no older bars.
  const olderState = useMemo(
    () =>
      older.key === olderKey
        ? older
        : {
            key: olderKey,
            bars: [],
            from: null,
            busy: false,
            exhausted: false,
            error: false,
          },
    [older, olderKey],
  );
  const olderKeyRef = useRef(olderKey);
  olderKeyRef.current = olderKey;
  const olderBusy = useRef(false);
  // Start of the first bar on the chart before any older batch (history or the day).
  const firstShown = useRef<string | null>(null);
  const shownStart =
    load.status === "ready" && isDailyTimeframe(timeframe)
      ? dailyRange(timeframe, sessionDate).startUtc.slice(0, 10)
      : (historyFrom ?? sessionDate);
  const canLoadOlder =
    load.status === "ready" &&
    history.done &&
    replayTime === null &&
    !olderState.exhausted &&
    olderState.bars.length < MAX_OLDER_BARS;
  const loadOlder = useCallback(() => {
    if (!canLoadOlder || olderBusy.current) return;
    const key = olderKey;
    const before = olderState.from ?? shownStart;
    olderBusy.current = true;
    setOlder({ ...olderState, busy: true, error: false });
    void (async () => {
      let bars: PriceBar[] = [];
      let from: string | null = null;
      let failed = false;
      try {
        if (isDailyTimeframe(timeframe)) {
          const range = olderDailyRange(timeframe, before);
          bars = (await api.bars({ instrumentId, timeframe, ...range })).bars;
          from = range.startUtc.slice(0, 10);
        } else {
          // Sessions without trades (a halt) are skipped, not taken for the start.
          const found = await olderSessionBars(
            before,
            sessionsForCandles(timeframe, sessionScope),
            (endDate, count) =>
              api.sessions(endDate, count, zoneOf(instrumentId).calendarId),
            async (item) => {
              const range = sessionRange(item, sessionScope);
              if (range === null) return [];
              try {
                return (await api.bars({ instrumentId, timeframe, ...range }))
                  .bars;
              } catch {
                return null;
              }
            },
            // One daily request finds the last trading day before a long halt.
            async (date) => {
              const daily = await api.bars({
                instrumentId,
                timeframe: "1d",
                startUtc: `${shiftDays(date, -HALT_LOOKBACK_DAYS)}T00:00:00Z`,
                endUtc: `${date}T00:00:00Z`,
              });
              const last = daily.bars[daily.bars.length - 1];
              return last ? last.interval_start_utc.slice(0, 10) : null;
            },
          );
          ({ bars, from, failed } = found);
        }
      } catch {
        failed = true;
      } finally {
        olderBusy.current = false;
      }
      if (olderKeyRef.current !== key) return;
      setOlder((previous) => {
        const base =
          previous.key === key
            ? previous
            : { ...olderState, bars: [], from: null };
        const merged = prependOlder(base.bars, bars, firstShown.current);
        const added = merged.length - base.bars.length;
        return {
          key,
          bars: merged,
          from: from ?? base.from,
          busy: false,
          // Nothing earlier: the provider's history (or the instrument) starts here.
          exhausted: !failed && added === 0,
          error: failed && added === 0,
        };
      });
    })();
  }, [
    api,
    canLoadOlder,
    instrumentId,
    olderKey,
    olderState,
    sessionScope,
    shownStart,
    timeframe,
  ]);
  const olderBars = olderState.bars;
  const earliestDate = olderState.from ?? historyFrom ?? sessionDate;

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
  firstShown.current =
    (historyBars[0] ?? dayBars?.[0])?.interval_start_utc ?? null;
  const allBars = useMemo(() => {
    if (dayBars === null) return null;
    // Older bars continue the whole history, so nothing in between is cut.
    if (olderBars.length > 0) return [...olderBars, ...historyBars, ...dayBars];
    // The most recent DEFAULT_CANDLES bars, always including the whole day.
    return [...historyBars, ...dayBars].slice(
      -Math.max(DEFAULT_CANDLES, dayBars.length),
    );
  }, [olderBars, historyBars, dayBars]);

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
    const source = asOf
      ? [...olderBars, ...historyBars, ...asOf.response.bars]
      : allBars;
    return source ? barsAsOf(source, replayTime) : null;
  }, [allBars, olderBars, historyBars, replayTime, asOf]);
  const series = useMemo(() => (bars ? toSeries(bars) : null), [bars]);

  // The US day market (ADR 0030): its own request and series, never mixed into `bars`,
  // so indicators, markers and candle clicks keep using the main bars only.
  const dayMarketWanted =
    // On by default: extended hours always include the US day market (ADR 0030).
    sessionScope !== "regular" &&
    !isDailyTimeframe(timeframe) &&
    instrumentId.startsWith("us:");
  const [dayMarket, setDayMarket] = useState<{
    bars: PriceBar[];
    error: unknown;
  } | null>(null);
  useEffect(() => {
    let current = true;
    setDayMarket(null);
    if (!dayMarketWanted || unavailable || !instrumentsReady || !history.done) {
      return;
    }
    const oldest = shiftDays(sessionDate, 1 - DAY_MARKET_DATES);
    api
      .overnightBars({
        instrumentId,
        timeframe,
        fromDate: earliestDate > oldest ? earliestDate : oldest,
        toDate: sessionDate,
      })
      .then((response) => {
        if (current) setDayMarket({ bars: response.bars, error: null });
      })
      .catch((error: unknown) => {
        if (current) setDayMarket({ bars: [], error });
      });
    return () => {
      current = false;
    };
  }, [
    api,
    dayMarketWanted,
    instrumentId,
    timeframe,
    sessionDate,
    earliestDate,
    history.done,
    unavailable,
    instrumentsReady,
  ]);
  const dayMarketCandles = useMemo(() => {
    if (!dayMarket) return [];
    const shown =
      replayTime === null
        ? dayMarket.bars
        : barsAsOf(dayMarket.bars, replayTime);
    return toDayMarketCandles(shown);
  }, [dayMarket, replayTime]);
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
        ? groupMarkers([
            ...journalMarkers(
              bars,
              instrumentId,
              journal.manual,
              journal.scenarios,
              journal.imported,
              journal.broker,
            ),
            ...backtestMarkers(bars, instrumentId, backtestFills ?? []),
          ])
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
      if (isIntradayHigher(timeframe)) {
        // Larger intraday bars are session buckets: fetched one session at a time,
        // for the chart's sessions plus warm-up sessions before them (ADR 0034).
        const count = intradaySessions(
          timeframe,
          lookback,
          history.sessions.length + 1,
          sessionScope !== "regular",
        );
        return {
          timeframe,
          sessions: count,
          key: `${instrumentId}|${timeframe}|${sessionScope}|${sessionDate}|${count}`,
        };
      }
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
  }, [
    instrumentId,
    ohlcv,
    ohlcvEnds,
    config.indicators,
    history.sessions.length,
    sessionScope,
    sessionDate,
  ]);
  const loadHigher = useCallback(
    async (request: (typeof higherRequests)[number]): Promise<PriceBar[]> => {
      const instrument = request.key.split("|")[0];
      if (!("sessions" in request) || request.sessions === undefined) {
        const response = await api.bars({
          instrumentId: instrument,
          timeframe: request.timeframe,
          ...request.range!,
        });
        return response.bars;
      }
      const windows = await api.sessions(
        sessionDate,
        request.sessions,
        zoneOf(instrument).calendarId,
      );
      const bars: PriceBar[] = [];
      for (const window of windows) {
        const range = sessionRange(window, sessionScope);
        if (range === null) continue;
        const response = await api.bars({
          instrumentId: instrument,
          timeframe: request.timeframe,
          ...range,
        });
        bars.push(...response.bars);
      }
      return bars;
    },
    [api, sessionDate, sessionScope],
  );
  const [higherCache, setHigherCache] = useState<Record<string, HigherBars>>(
    {},
  );
  const [higherErrors, setHigherErrors] = useState<Record<string, string>>({});
  const higherPending = useRef(new Set<string>());
  useEffect(() => {
    for (const request of higherRequests) {
      const { timeframe, key } = request;
      if (higherCache[key] || higherPending.current.has(key)) continue;
      higherPending.current.add(key);
      loadHigher(request)
        .then((loaded) => {
          setHigherCache((cache) => ({
            ...cache,
            [key]: higherBarsOf(loaded),
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
  }, [loadHigher, higherRequests, higherCache, t]);
  const higher = useMemo(
    () =>
      Object.fromEntries(
        higherRequests.flatMap(({ timeframe, key }) =>
          higherCache[key] ? [[timeframe, higherCache[key]]] : [],
        ),
      ) as Partial<Record<HigherTimeframe, HigherBars>>,
    [higherRequests, higherCache],
  );
  // Pine request.security (ADR 0040): other timeframes and symbols a script asks for.
  const pineWanted = useMemo(
    () =>
      indicatorList.flatMap((instance) =>
        instance.type === "pine" && !instance.hidden
          ? pineRequests(instance.pine ?? "", instance.params, {
              symbol,
              timeframe: config.timeframe,
            })
          : [],
      ),
    // The list is replaced (never mutated) whenever settings change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [config.indicators, symbol, config.timeframe],
  );
  // Other tickers a script names, as instrument ids (null: not found).
  const [pineSymbols, setPineSymbols] = useState<Record<string, string | null>>(
    {},
  );
  const pineLookups = useRef(new Set<string>());
  useEffect(() => {
    for (const { symbol: ticker } of pineWanted) {
      if (
        ticker === "" ||
        ticker in pineSymbols ||
        pineLookups.current.has(ticker)
      ) {
        continue;
      }
      const known = instruments.find(
        (item) => item.instrument.symbol.toUpperCase() === ticker,
      );
      if (known) {
        setPineSymbols((found) => ({
          ...found,
          [ticker]: known.instrument.instrument_id,
        }));
        continue;
      }
      pineLookups.current.add(ticker);
      api
        .searchSymbols(ticker, 5)
        .then((response) => {
          const match = response.results.find(
            (item) => item.listing.instrument.symbol.toUpperCase() === ticker,
          );
          setPineSymbols((found) => ({
            ...found,
            [ticker]: match ? match.listing.instrument.instrument_id : null,
          }));
        })
        .catch(() => setPineSymbols((found) => ({ ...found, [ticker]: null })))
        .finally(() => pineLookups.current.delete(ticker));
    }
  }, [api, instruments, pineWanted, pineSymbols]);
  const intradayChart = minutesOf(config.timeframe) !== null;
  type PineFetch = {
    dataKey: string;
    monthly: boolean;
    request: (typeof higherRequests)[number];
  };
  const pineFetches = useMemo(() => {
    if (ohlcv.length === 0) return [];
    return pineWanted.flatMap((want): PineFetch[] => {
      const instrument =
        want.symbol === "" ? instrumentId : pineSymbols[want.symbol];
      if (!instrument) return [];
      // Months are built from daily bars.
      const monthly = want.timeframe === "1M";
      const timeframe = monthly ? "1d" : want.timeframe;
      if (!isHigherTimeframe(timeframe)) return [];
      if (isIntradayHigher(timeframe)) {
        if (!intradayChart) return [];
        const count = intradaySessions(
          timeframe,
          300,
          history.sessions.length + 1,
          sessionScope !== "regular",
        );
        return [
          {
            dataKey: want.key,
            monthly,
            request: {
              timeframe,
              sessions: count,
              key: `${instrument}|${timeframe}|${sessionScope}|${sessionDate}|${count}`,
            },
          },
        ];
      }
      const range = higherRange(
        ohlcv[0].time,
        ohlcvEnds[ohlcvEnds.length - 1],
        monthly ? 400 : 300,
        timeframe,
      );
      return [
        {
          dataKey: want.key,
          monthly,
          request: {
            timeframe,
            range,
            key: `${instrument}|${timeframe}|${range.startUtc}|${range.endUtc.slice(0, 10)}`,
          },
        },
      ];
    });
  }, [
    pineWanted,
    pineSymbols,
    instrumentId,
    ohlcv,
    ohlcvEnds,
    intradayChart,
    history.sessions.length,
    sessionScope,
    sessionDate,
  ]);
  const [pineErrors, setPineErrors] = useState<Record<string, string>>({});
  useEffect(() => {
    for (const { request, dataKey } of pineFetches) {
      const { key } = request;
      if (higherCache[key] || higherPending.current.has(key)) continue;
      higherPending.current.add(key);
      loadHigher(request)
        .then((loaded) => {
          setHigherCache((cache) => ({
            ...cache,
            [key]: higherBarsOf(loaded),
          }));
          setPineErrors((errors) => {
            const rest = { ...errors };
            delete rest[dataKey];
            return rest;
          });
        })
        .catch((error: unknown) =>
          setPineErrors((errors) => ({
            ...errors,
            [dataKey]: t("pine.requestFailed", {
              request: dataKey.replace("|", " "),
              error: error instanceof Error ? error.message : String(error),
            }),
          })),
        )
        .finally(() => higherPending.current.delete(key));
    }
  }, [loadHigher, pineFetches, higherCache, t]);
  const pineData = useMemo(() => {
    const data: Record<string, PineRequestData> = {};
    for (const { dataKey, monthly, request } of pineFetches) {
      const bars = higherCache[request.key];
      if (!bars) continue;
      data[dataKey] = monthly
        ? monthlyBars(bars, zoneOf(instrumentId).timeZone)
        : bars;
    }
    return data;
  }, [pineFetches, higherCache, instrumentId]);
  const pineProblems = [
    ...new Set(
      pineWanted.flatMap((want) => {
        if (want.symbol !== "" && pineSymbols[want.symbol] === null) {
          return [t("pine.symbolMissing", { symbol: want.symbol })];
        }
        const timeframe = want.timeframe === "1M" ? "1d" : want.timeframe;
        if (!intradayChart && minutesOf(timeframe) !== null) {
          return [t("pine.intradayOnDaily", { timeframe })];
        }
        return pineErrors[want.key] ? [pineErrors[want.key]] : [];
      }),
    ),
  ];
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
        {
          timeZone: zoneOf(instrumentId).timeZone,
          symbol,
          timeframe: config.timeframe,
          priceDecimals: zoneOf(instrumentId).priceDecimals,
          ends: ohlcvEnds,
          requests: pineData,
        },
        { bars: higher, ends: ohlcvEnds },
      ).map((item) =>
        // Visibility settings hide an indicator on some chart timeframes.
        visibleOn(item.instance, config.timeframe)
          ? item
          : { ...item, hidden: true },
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
      config.timeframe,
      pineData,
      symbol,
    ],
  );
  // Chart's own timeframe or a larger one; Python indicators stay on the chart's bars.
  const timeframeField = (instance: IndicatorInstance) =>
    isCustomType(instance.type) ||
    (higherChoices(config.timeframe).length === 0 &&
      lowerChoices(config.timeframe).length === 0) ? null : (
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
          {lowerChoices(config.timeframe).length > 0 ? (
            <optgroup label={t("ind.timeframeLower")}>
              {lowerChoices(config.timeframe).map((choice) => (
                <option key={choice} value={choice}>
                  {choice.toUpperCase()}
                </option>
              ))}
            </optgroup>
          ) : null}
          {higherChoices(config.timeframe).length > 0 ? (
            <optgroup label={t("ind.timeframeHigher")}>
              {higherChoices(config.timeframe).map((choice) => (
                <option key={choice} value={choice}>
                  {choice.toUpperCase()}
                </option>
              ))}
            </optgroup>
          ) : null}
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
  const legendMarkers = legendBar
    ? markers.filter(
        (marker) =>
          marker.time ===
          Math.floor(Date.parse(legendBar.interval_start_utc) / 1000),
      )
    : [];
  const selectedFills = selected
    ? markers
        .filter(
          (marker) =>
            marker.time ===
            Math.floor(Date.parse(selected.interval_start_utc) / 1000),
        )
        .flatMap((marker) => marker.details ?? [marker.text])
    : [];
  const legendIndex = legendBar
    ? ohlcv.findIndex(
        (item) =>
          item.time ===
          Math.floor(Date.parse(legendBar.interval_start_utc) / 1000),
      )
    : -1;
  const formatValue = (value: number | null | undefined, precision?: number) =>
    value === null || value === undefined
      ? "—"
      : precision !== undefined
        ? value.toFixed(precision)
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
              dayMarket={dayMarketCandles}
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
              onDrawingUpdate={(id, patch) =>
                onDrawingsChange?.(
                  (config.drawings ?? []).map((item) =>
                    item.id === id ? { ...item, ...patch } : item,
                  ),
                )
              }
              onToolDone={onToolDone}
              drawingsSelectable={!replayChoosing}
              onReachStart={loadOlder}
            />
            {replayTime === null ? (
              <div className="chart-older">
                <button
                  type="button"
                  onClick={loadOlder}
                  disabled={!canLoadOlder || olderState.busy}
                  title={t("chart.olderHint")}
                >
                  {olderState.busy
                    ? t("chart.olderLoading")
                    : olderState.exhausted
                      ? t("chart.olderNone")
                      : olderState.bars.length >= MAX_OLDER_BARS
                        ? t("chart.olderLimit")
                        : t("chart.older")}
                </button>
                {olderState.error ? (
                  <span role="status">{t("chart.olderFailed")}</span>
                ) : null}
              </div>
            ) : null}
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
                {legendMarkers.length > 0 ? (
                  <span className="legend-fills">
                    {legendMarkers.map((marker) => (
                      <b key={marker.id} style={{ color: marker.color }}>
                        {marker.text}
                      </b>
                    ))}
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
                          {visibleOn(item.instance, config.timeframe)
                            ? ""
                            : ` · ${t("ind.hiddenHere")}`}
                        </span>
                        {item.lines.map((line) => (
                          <b key={line.id} style={{ color: line.color }}>
                            {formatValue(
                              legendIndex >= 0
                                ? item.values[line.id.split(":")[1]][
                                    legendIndex
                                  ]
                                : null,
                              item.precision,
                            )}
                          </b>
                        ))}
                        <span className="indicator-actions">
                          {/* Every indicator has a Style tab, so settings always open. */}
                          {item.definition.outputs.length > 0 ? (
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
                        {editing === item.key ? (
                          <IndicatorSettings
                            definition={item.definition}
                            instance={item.instance}
                            onChange={(update) =>
                              changeIndicator(item.key, update)
                            }
                            onClose={() => setEditing(null)}
                            inputs={
                              item.instance.type === "formula" ? (
                                <>
                                  {timeframeField(item.instance)}
                                  <FormulaFields
                                    initial={{
                                      label: item.instance.label ?? "",
                                      formula: item.instance.formula ?? "",
                                      pane: item.instance.pane ?? "separate",
                                    }}
                                    submitLabel={t("ind.apply")}
                                    onCancel={() => setEditing(null)}
                                    onSubmit={(value) =>
                                      changeIndicator(item.key, (current) => ({
                                        ...current,
                                        ...value,
                                      }))
                                    }
                                  />
                                </>
                              ) : (
                                <>
                                  {item.definition.inputs.map((field) => {
                                    const setParam = (value: number) =>
                                      changeIndicator(item.key, (current) => ({
                                        ...current,
                                        params: cleanParams(item.definition, {
                                          ...current.params,
                                          [field.key]: value,
                                        }),
                                      }));
                                    return (
                                      <label key={field.key}>
                                        {field.label[language]}
                                        {field.options ? (
                                          <select
                                            value={item.params[field.key]}
                                            onChange={(event) =>
                                              setParam(
                                                Number(event.target.value),
                                              )
                                            }
                                          >
                                            {field.options.map((option) => (
                                              <option
                                                key={option.value}
                                                value={option.value}
                                              >
                                                {option.label}
                                              </option>
                                            ))}
                                          </select>
                                        ) : (
                                          <input
                                            type="number"
                                            min={field.min}
                                            max={field.max}
                                            step={field.step ?? 1}
                                            defaultValue={
                                              item.params[field.key]
                                            }
                                            onChange={(event) =>
                                              setParam(
                                                Number(event.target.value),
                                              )
                                            }
                                          />
                                        )}
                                      </label>
                                    );
                                  })}
                                  {timeframeField(item.instance)}
                                  {item.instance.type === "pine" ? (
                                    <PineFields
                                      initial={item.instance.pine ?? ""}
                                      submitLabel={t("ind.apply")}
                                      onCancel={() => setEditing(null)}
                                      onSubmit={(source) =>
                                        changeIndicator(
                                          item.key,
                                          (current) => ({
                                            ...current,
                                            pine: source,
                                            // Inputs that no longer exist drop; new ones take their defaults.
                                            params: cleanParams(
                                              pineDefinition(source),
                                              current.params,
                                            ),
                                          }),
                                        )
                                      }
                                    />
                                  ) : null}
                                </>
                              )
                            }
                          />
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
            fills={selectedFills}
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
      {rendered.flatMap((item) =>
        item.pineError
          ? [
              <p
                key={`pine-${item.key}`}
                className="bt-problem chart-custom-error"
                role="alert"
              >
                {t("pine.runError", {
                  name: item.definition.short,
                  error: item.pineError,
                })}
              </p>,
            ]
          : [],
      )}
      {pineProblems.map((message) => (
        <p key={message} className="bt-problem chart-custom-error" role="alert">
          {message}
        </p>
      ))}
      {rendered.flatMap((item) =>
        item.strategy && !item.hidden
          ? [
              <PineStrategyReport
                key={`strategy-${item.key}`}
                name={item.definition.short}
                report={item.strategy}
                zone={zoneOf(instrumentId)}
              />,
            ]
          : [],
      )}
      {dayMarketWanted && dayMarket ? (
        <p
          className={dayMarket.error ? "chart-error" : "workspace-note"}
          role={dayMarket.error ? "alert" : "status"}
        >
          {dayMarket.error
            ? t("chart.dayMarketFailed", {
                error:
                  dayMarket.error instanceof Error
                    ? dayMarket.error.message
                    : String(dayMarket.error),
              })
            : t("chart.dayMarketNote", { n: dayMarketCandles.length })}
        </p>
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
