import {
  CandlestickSeries,
  ColorType,
  createChart,
  CrosshairMode,
  PriceScaleMode,
  createSeriesMarkers,
  HistogramSeries,
  LineSeries,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type MouseEventParams,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { CandlePoint, VolumePoint } from "./chart-data";
import { COLORS, exchangeTime } from "./chart-data";
import { zoneFormat, zoneOf } from "./market-zone";
import type { ChartSyncGroup } from "./chart-sync";
import { DrawingLayer, type Projection } from "./DrawingLayer";
import { snapPrice, type Drawing, type DrawingTool } from "./drawings";
import type { RenderedIndicator } from "./indicators";
import type { ChartMarker } from "./journal-markers";

interface PriceChartProps {
  chartId: string;
  candles: CandlePoint[];
  volume: VolumePoint[];
  /** US day-market candles, drawn as their own series in their own colors (ADR 0030). */
  dayMarket?: CandlePoint[];
  sync: ChartSyncGroup;
  markers?: ChartMarker[];
  /** Called with the start time (epoch seconds) of the clicked candle. */
  onCandleClick?: (time: number) => void;
  /** Called with the start time under the crosshair, or null when it leaves the chart. */
  onHover?: (time: number | null) => void;
  /** Snap the crosshair to the nearest open, high, low or close. */
  magnet?: boolean;
  logScale?: boolean;
  /** Changing this number fits all bars into view again. */
  fitRequest?: number;
  /** Daily or weekly bars: the time axis shows dates, not clock times. */
  daily?: boolean;
  /** Bars are fitted into view when this changes (a new chart, not a replay step). */
  fitKey?: string;
  /** Indicators to draw: price overlays on the candles, others in panes below. */
  indicators?: RenderedIndicator[];
  /** Saved drawings for the shown instrument, and the active drawing tool. */
  drawings?: Drawing[];
  tool?: DrawingTool;
  instrumentId?: string;
  barSeconds?: number;
  onDrawingCreate?: (drawing: Omit<Drawing, "id">) => void;
  onDrawingDelete?: (id: string) => void;
  onDrawingUpdate?: (id: string, patch: Partial<Drawing>) => void;
  onToolDone?: () => void;
  /** False while clicks must reach the candles (e.g. picking a replay start). */
  drawingsSelectable?: boolean;
}

const DAY_TICK: Intl.DateTimeFormatOptions = { month: "short", day: "numeric" };
const DAY_LABEL: Intl.DateTimeFormatOptions = {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
};

/** Bars in view when a chart first loads; the rest of the history is scrollable. */
const INITIAL_VISIBLE_BARS = 150;

function timeOf(value: Time): number {
  return typeof value === "number" ? value : 0;
}

/** Candlestick and volume chart; the time axis and crosshair are labelled in ET. */
export function PriceChart({
  chartId,
  candles,
  volume,
  dayMarket = [],
  sync,
  markers = [],
  onCandleClick,
  onHover,
  magnet = false,
  logScale = false,
  fitRequest = 0,
  indicators = [],
  fitKey = "",
  daily = false,
  drawings = [],
  tool = "cursor",
  instrumentId = "",
  barSeconds = 60,
  onDrawingCreate,
  onDrawingDelete,
  onDrawingUpdate,
  onToolDone,
  drawingsSelectable = true,
}: PriceChartProps) {
  const container = useRef<HTMLDivElement | null>(null);
  const chart = useRef<IChartApi | null>(null);
  const candleSeries = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const dayMarketSeries = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeries = useRef<ISeriesApi<"Histogram"> | null>(null);
  const markerLayer = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const applyingSyncedRange = useRef(false);
  const indicatorSeries = useRef<ISeriesApi<"Line" | "Histogram">[]>([]);
  const fittedKey = useRef<string | null>(null);
  const [ready, setReady] = useState(false);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [redraw, setRedraw] = useState(0);
  const candlesRef = useRef(candles);
  candlesRef.current = candles;
  const magnetRef = useRef(magnet);
  magnetRef.current = magnet;
  const clickHandler = useRef(onCandleClick);
  const hoverHandler = useRef(onHover);

  useEffect(() => {
    clickHandler.current = onCandleClick;
    hoverHandler.current = onHover;
  }, [onCandleClick, onHover]);

  useEffect(() => {
    if (!container.current) {
      return;
    }
    const instance = createChart(container.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "#10131a" },
        textColor: "#8b95a5",
      },
      grid: {
        vertLines: { color: "#1a202a" },
        horzLines: { color: "#1a202a" },
      },
      rightPriceScale: { borderColor: "#232a36" },
      timeScale: {
        borderColor: "#232a36",
        timeVisible: true,
        secondsVisible: false,
        // Times are shown in the instrument's exchange zone (ET or KST).
        tickMarkFormatter: (time: Time) =>
          dailyRef.current
            ? zoneFormat(zoneRef.current, "en-US", DAY_TICK).format(
                new Date(timeOf(time) * 1000),
              )
            : exchangeTime(timeOf(time), zoneRef.current).replace(
                ` ${zoneRef.current.label}`,
                "",
              ),
      },
      localization: {
        timeFormatter: (time: Time) =>
          dailyRef.current
            ? zoneFormat(zoneRef.current, "en-CA", DAY_LABEL).format(
                new Date(timeOf(time) * 1000),
              )
            : exchangeTime(timeOf(time), zoneRef.current),
      },
    });
    const candlesApi = instance.addSeries(CandlestickSeries, {
      upColor: COLORS.up,
      downColor: COLORS.down,
      wickUpColor: COLORS.up,
      wickDownColor: COLORS.down,
      borderVisible: false,
    });
    candleSeries.current = candlesApi;
    dayMarketSeries.current = instance.addSeries(CandlestickSeries, {
      upColor: COLORS.dayUp,
      downColor: COLORS.dayDown,
      wickUpColor: COLORS.dayUp,
      wickDownColor: COLORS.dayDown,
      borderVisible: false,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    markerLayer.current = createSeriesMarkers(candlesApi, []);
    volumeSeries.current = instance.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "",
    });
    volumeSeries.current.priceScale().applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });
    chart.current = instance;

    const onRange = (range: { from: Time; to: Time } | null) => {
      if (range === null || applyingSyncedRange.current) {
        return;
      }
      sync.publish(chartId, { from: timeOf(range.from), to: timeOf(range.to) });
    };
    const onClick = (param: MouseEventParams<Time>) => {
      if (param.time !== undefined) {
        clickHandler.current?.(timeOf(param.time));
      }
    };
    const onMove = (param: MouseEventParams<Time>) => {
      hoverHandler.current?.(
        param.time === undefined ? null : timeOf(param.time),
      );
    };
    const bump = () => setRedraw((count) => count + 1);
    const onMoveAndRedraw = (param: MouseEventParams<Time>) => {
      onMove(param);
      bump();
    };
    instance.timeScale().subscribeVisibleTimeRangeChange(onRange);
    instance.timeScale().subscribeVisibleLogicalRangeChange(bump);
    instance.subscribeClick(onClick);
    instance.subscribeCrosshairMove(onMoveAndRedraw);
    const element = container.current;
    const observer =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(() => {
            setSize({
              width: element.clientWidth,
              height: element.clientHeight,
            });
            bump();
          });
    observer?.observe(element);
    setSize({ width: element.clientWidth, height: element.clientHeight });
    setReady(true);
    const unregister = sync.register(chartId, (range) => {
      applyingSyncedRange.current = true;
      try {
        instance.timeScale().setVisibleRange({
          from: range.from as UTCTimestamp,
          to: range.to as UTCTimestamp,
        });
      } catch {
        // The other chart may show times this chart has no bars for yet.
      } finally {
        applyingSyncedRange.current = false;
      }
    });
    return () => {
      unregister();
      observer?.disconnect();
      setReady(false);
      instance.timeScale().unsubscribeVisibleLogicalRangeChange(bump);
      instance.unsubscribeCrosshairMove(onMoveAndRedraw);
      instance.unsubscribeClick(onClick);
      instance.timeScale().unsubscribeVisibleTimeRangeChange(onRange);
      instance.remove();
      indicatorSeries.current = [];
      chart.current = null;
      markerLayer.current = null;
    };
  }, [chartId, sync]);

  const dailyRef = useRef(daily);
  dailyRef.current = daily;
  const zone = zoneOf(instrumentId);
  const zoneRef = useRef(zone);
  zoneRef.current = zone;

  useEffect(() => {
    // Whole won for Korean stocks, cents for US ones.
    candleSeries.current?.applyOptions({
      priceFormat: {
        type: "price",
        precision: zone.priceDecimals,
        minMove: 10 ** -zone.priceDecimals,
      },
    });
  }, [zone]);
  const shownCount = useRef(0);
  const shownFirst = useRef<number | null>(null);

  useEffect(() => {
    // When the view already reaches the latest bar, keep following new bars (as a live
    // chart does, and as replay steps need); a view panned into the past stays put.
    const scale = chart.current?.timeScale();
    const range = scale?.getVisibleLogicalRange();
    const followLatest =
      range !== null &&
      range !== undefined &&
      shownCount.current > 0 &&
      range.to >= shownCount.current - 1.5;
    // Older bars added at the front shift every index; keep the same times in view.
    const prepended =
      shownFirst.current !== null &&
      candles.length > 0 &&
      candles[0].time < shownFirst.current;
    const visibleTimes = prepended ? scale?.getVisibleRange() : null;
    candleSeries.current?.setData(
      candles.map((point) => ({ ...point, time: point.time as UTCTimestamp })),
    );
    volumeSeries.current?.setData(
      volume.map((point) => ({ ...point, time: point.time as UTCTimestamp })),
    );
    // Fit once per chart; replay steps add bars without resetting the view.
    if (fittedKey.current !== fitKey && candles.length > 0) {
      fittedKey.current = fitKey;
      // Show the latest bars at a readable width; older ones are a scroll away.
      if (candles.length > INITIAL_VISIBLE_BARS) {
        scale?.setVisibleLogicalRange({
          from: candles.length - INITIAL_VISIBLE_BARS,
          to: candles.length + 2,
        });
      } else {
        scale?.fitContent();
      }
    } else if (visibleTimes) {
      scale?.setVisibleRange(visibleTimes);
    } else if (followLatest && candles.length > shownCount.current) {
      scale?.scrollToRealTime();
    }
    shownCount.current = candles.length;
    shownFirst.current = candles[0]?.time ?? null;
  }, [candles, volume, fitKey]);

  useEffect(() => {
    dayMarketSeries.current?.setData(
      dayMarket.map((point) => ({
        ...point,
        time: point.time as UTCTimestamp,
      })),
    );
  }, [dayMarket]);

  useEffect(() => {
    chart.current?.applyOptions({
      crosshair: {
        mode: magnet ? CrosshairMode.Magnet : CrosshairMode.Normal,
      },
      rightPriceScale: {
        mode: logScale ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal,
      },
    });
  }, [magnet, logScale]);

  useEffect(() => {
    if (fitRequest > 0) {
      chart.current?.timeScale().fitContent();
    }
  }, [fitRequest]);

  useEffect(() => {
    const instance = chart.current;
    if (!instance) return;
    for (const series of indicatorSeries.current) {
      instance.removeSeries(series);
    }
    indicatorSeries.current = [];
    while (instance.panes().length > 1) {
      instance.removePane(instance.panes().length - 1);
    }
    let nextPane = 1;
    for (const indicator of indicators) {
      if (indicator.hidden) continue;
      const pane = indicator.definition.pane === "price" ? 0 : nextPane++;
      indicator.lines.forEach((line, index) => {
        const series =
          line.kind === "histogram"
            ? instance.addSeries(
                HistogramSeries,
                {
                  color: line.color,
                  priceLineVisible: false,
                  lastValueVisible: false,
                },
                pane,
              )
            : instance.addSeries(
                LineSeries,
                {
                  color: line.color,
                  lineWidth: 1,
                  priceLineVisible: false,
                  lastValueVisible: pane > 0,
                  crosshairMarkerVisible: false,
                },
                pane,
              );
        series.setData(
          line.data.map((point) => ({
            ...point,
            time: point.time as UTCTimestamp,
          })),
        );
        if (index === 0) {
          for (const level of indicator.definition.levels ?? []) {
            series.createPriceLine({
              price: level,
              color: "#4a566b",
              lineWidth: 1,
              lineStyle: LineStyle.Dashed,
              axisLabelVisible: false,
            });
          }
        }
        indicatorSeries.current.push(series);
      });
    }
  }, [indicators]);

  useEffect(() => {
    markerLayer.current?.setMarkers(
      markers.map((marker) => ({
        ...marker,
        time: marker.time as UTCTimestamp,
      })),
    );
  }, [markers, candles]);

  const projection = useMemo<Projection>(
    () => ({
      toX: (time) =>
        chart.current?.timeScale().timeToCoordinate(time as UTCTimestamp) ??
        null,
      toY: (price) => candleSeries.current?.priceToCoordinate(price) ?? null,
      toAnchor: (x, y) => {
        const instance = chart.current;
        const series = candleSeries.current;
        const points = candlesRef.current;
        if (!instance || !series || points.length === 0) return null;
        const logical = instance.timeScale().coordinateToLogical(x);
        const price = series.coordinateToPrice(y);
        if (logical === null || price === null) return null;
        const index = Math.min(
          points.length - 1,
          Math.max(0, Math.round(logical)),
        );
        const bar = points[index];
        return {
          time: bar.time,
          price: magnetRef.current ? snapPrice(price, bar) : price,
        };
      },
    }),
    [],
  );

  const zoomTo = useCallback((from: number, to: number) => {
    chart.current?.timeScale().setVisibleRange({
      from: from as UTCTimestamp,
      to: to as UTCTimestamp,
    });
  }, []);

  const formatPrice = useCallback(
    (price: number) =>
      candleSeries.current?.priceFormatter().format(price) ?? price.toFixed(2),
    [],
  );

  return (
    <div className="price-chart-wrap">
      <div
        className="price-chart"
        ref={container}
        data-testid={`chart-${chartId}`}
      />
      {ready && size.width > 0 ? (
        <DrawingLayer
          width={size.width}
          height={size.height}
          projection={projection}
          redraw={redraw}
          drawings={drawings}
          tool={tool}
          instrumentId={instrumentId}
          barSeconds={barSeconds}
          formatPrice={formatPrice}
          selectable={drawingsSelectable}
          onCreate={(drawing) => onDrawingCreate?.(drawing)}
          onDelete={(id) => onDrawingDelete?.(id)}
          onUpdate={(id, patch) => onDrawingUpdate?.(id, patch)}
          onToolDone={() => onToolDone?.()}
          onZoom={zoomTo}
        />
      ) : null}
    </div>
  );
}
