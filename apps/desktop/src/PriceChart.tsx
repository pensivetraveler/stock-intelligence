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
  type LineWidth,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type MouseEventParams,
  type SeriesType,
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
import type { LineStyleName, RenderedIndicator } from "./indicators";
import { thinLabels, type ChartMarker } from "./journal-markers";
import { hasGraphics } from "./pine-drawings";
import { PineGraphicsPrimitive } from "./pine-graphics";
import { OLDER_TRIGGER_BARS, USER_MOVE_MS } from "./older-bars";

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
  /** Called when the view is scrolled or zoomed out past the first bar. */
  onReachStart?: () => void;
}

const DAY_TICK: Intl.DateTimeFormatOptions = { month: "short", day: "numeric" };
const DAY_LABEL: Intl.DateTimeFormatOptions = {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
};

/** Bars in view when a chart first loads; the rest of the history is scrollable. */
const INITIAL_VISIBLE_BARS = 150;

/** The candle at `time`, or the nearest one (for magnet snapping). */
function nearestCandle(points: CandlePoint[], time: number): CandlePoint {
  let low = 0;
  let high = points.length - 1;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (points[middle].time < time) low = middle + 1;
    else high = middle;
  }
  const after = points[low];
  const before = points[Math.max(0, low - 1)];
  return Math.abs(before.time - time) < Math.abs(after.time - time)
    ? before
    : after;
}

const LINE_STYLE: Record<LineStyleName, LineStyle> = {
  solid: LineStyle.Solid,
  dashed: LineStyle.Dashed,
  dotted: LineStyle.Dotted,
};

function timeOf(value: Time): number {
  return typeof value === "number" ? value : 0;
}

/** Candlestick and volume chart; the time axis and crosshair are labelled in ET. */
/** Room a marker label needs before the next one on the same side, in pixels. */
const LABEL_WIDTH = 90;

export function PriceChart({
  chartId,
  candles,
  volume,
  dayMarket = [],
  sync,
  markers = [],
  onCandleClick,
  onReachStart,
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
  // Pine drawings (ADR 0040), each attached to a series in its indicator's pane.
  const pineLayers = useRef<
    { series: ISeriesApi<SeriesType>; primitive: PineGraphicsPrimitive }[]
  >([]);
  const fittedKey = useRef<string | null>(null);
  const [ready, setReady] = useState(false);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [redraw, setRedraw] = useState(0);
  // Pixels per bar, so marker labels thin out as the chart zooms out.
  const [barSpacing, setBarSpacing] = useState(6);
  const candlesRef = useRef(candles);
  candlesRef.current = candles;
  const magnetRef = useRef(magnet);
  magnetRef.current = magnet;
  const clickHandler = useRef(onCandleClick);
  const hoverHandler = useRef(onHover);
  const reachStartHandler = useRef(onReachStart);
  const lastUserMove = useRef(-Infinity);

  useEffect(() => {
    clickHandler.current = onCandleClick;
    hoverHandler.current = onHover;
    reachStartHandler.current = onReachStart;
  }, [onCandleClick, onHover, onReachStart]);

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
    const onZoom = () => {
      bump();
      setBarSpacing(Math.round(instance.timeScale().options().barSpacing));
      // The user scrolled or zoomed out until less than one screen of bars is left
      // before the view: they want to see further back. Zooming out stops at a
      // minimum bar width, so waiting for the very first bar would never ask. Views
      // set by the chart itself (fitting new data) never ask.
      const range = instance.timeScale().getVisibleLogicalRange();
      if (
        range !== null &&
        range.from < Math.max(OLDER_TRIGGER_BARS, range.to - range.from) &&
        performance.now() - lastUserMove.current < USER_MOVE_MS
      ) {
        reachStartHandler.current?.();
      }
    };
    const onUserMove = (event: Event) => {
      // A plain hover moves nothing; a drag (a button held) or a wheel does.
      if (event instanceof PointerEvent && event.type === "pointermove") {
        if (event.buttons === 0) return;
      }
      lastUserMove.current = performance.now();
    };
    const onMoveAndRedraw = (param: MouseEventParams<Time>) => {
      onMove(param);
      bump();
    };
    instance.timeScale().subscribeVisibleTimeRangeChange(onRange);
    instance.timeScale().subscribeVisibleLogicalRangeChange(onZoom);
    instance.subscribeClick(onClick);
    instance.subscribeCrosshairMove(onMoveAndRedraw);
    const element = container.current;
    for (const name of ["wheel", "pointerdown", "pointermove", "touchmove"]) {
      element.addEventListener(name, onUserMove, { passive: true });
    }
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
      for (const name of ["wheel", "pointerdown", "pointermove", "touchmove"]) {
        element.removeEventListener(name, onUserMove);
      }
      setReady(false);
      instance.timeScale().unsubscribeVisibleLogicalRangeChange(onZoom);
      instance.unsubscribeCrosshairMove(onMoveAndRedraw);
      instance.unsubscribeClick(onClick);
      instance.timeScale().unsubscribeVisibleTimeRangeChange(onRange);
      instance.remove();
      indicatorSeries.current = [];
      pineLayers.current = [];
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

  // Pine `barcolor()` on price-pane scripts recolours those candles.
  const barColors = useMemo(() => {
    const colors = new Map<number, string>();
    for (const indicator of indicators) {
      if (indicator.hidden || indicator.definition.pane !== "price") continue;
      for (const item of indicator.graphics?.barColors ?? []) {
        colors.set(item.time, item.color);
      }
    }
    return colors;
  }, [indicators]);
  const coloredCandles = useRef(false);
  useEffect(() => {
    if (barColors.size === 0 && !coloredCandles.current) return;
    coloredCandles.current = barColors.size > 0;
    candleSeries.current?.setData(
      candles.map((point) => {
        const color = barColors.get(point.time);
        return {
          ...point,
          time: point.time as UTCTimestamp,
          ...(color ? { color, wickColor: color, borderColor: color } : {}),
        };
      }),
    );
  }, [candles, barColors]);

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
    for (const layer of pineLayers.current) {
      if (layer.series === candleSeries.current) {
        layer.series.detachPrimitive(layer.primitive);
      }
    }
    pineLayers.current = [];
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
      const graphics =
        indicator.graphics && hasGraphics(indicator.graphics)
          ? indicator.graphics
          : null;
      if (!graphics && !indicator.lines.some((line) => line.visible)) continue;
      const pane = indicator.definition.pane === "price" ? 0 : nextPane++;
      let host: ISeriesApi<SeriesType> | null =
        pane === 0 ? candleSeries.current : null;
      // Levels go on the first output drawn, even when an earlier one is hidden.
      let levelsPlaced = false;
      indicator.lines.forEach((line) => {
        if (!line.visible) return;
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
                  lineWidth: line.width as LineWidth,
                  lineStyle: LINE_STYLE[line.line],
                  priceLineVisible: false,
                  lastValueVisible: pane > 0,
                  crosshairMarkerVisible: false,
                },
                pane,
              );
        if (indicator.precision !== undefined) {
          series.applyOptions({
            priceFormat: {
              type: "price",
              precision: indicator.precision,
              minMove: 10 ** -indicator.precision,
            },
          });
        }
        series.setData(
          line.data.map((point) => ({
            ...point,
            time: point.time as UTCTimestamp,
          })),
        );
        if (!levelsPlaced) {
          levelsPlaced = true;
          for (const level of indicator.levels) {
            if (!level.visible) continue;
            series.createPriceLine({
              price: level.value,
              color: level.color,
              lineWidth: 1,
              lineStyle: LineStyle.Dashed,
              axisLabelVisible: false,
            });
          }
        }
        indicatorSeries.current.push(series);
        host ??= series;
      });
      if (graphics) {
        let autoscale = false;
        if (!host) {
          // Drawings only: an empty series gives the pane its price scale.
          const empty = instance.addSeries(
            LineSeries,
            {
              color: "rgba(0, 0, 0, 0)",
              priceLineVisible: false,
              lastValueVisible: false,
              crosshairMarkerVisible: false,
            },
            pane,
          );
          empty.setData(
            candlesRef.current.map((point) => ({
              time: point.time as UTCTimestamp,
            })),
          );
          indicatorSeries.current.push(empty);
          host = empty;
          autoscale = true;
        }
        const primitive = new PineGraphicsPrimitive(
          graphics,
          () => candlesRef.current.map((point) => point.time),
          autoscale,
        );
        host.attachPrimitive(primitive);
        pineLayers.current.push({ series: host, primitive });
      }
    }
  }, [indicators]);

  useEffect(() => {
    // About one label per LABEL_WIDTH pixels on each side of the candles.
    const gapBars = Math.max(
      1,
      Math.ceil(LABEL_WIDTH / Math.max(barSpacing, 1)),
    );
    markerLayer.current?.setMarkers(
      thinLabels(markers, barSeconds, gapBars).map(
        ({ id, position, shape, color, text, time }) => ({
          id,
          position,
          shape,
          color,
          text,
          time: time as UTCTimestamp,
        }),
      ),
    );
  }, [markers, candles, barSeconds, barSpacing]);

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
        const price = series.coordinateToPrice(y);
        if (price === null) return null;
        // The time axis also holds the day-market series' times, so a logical index is
        // not an index into these candles: ask the axis for the time under the pointer.
        const scaleTime = instance.timeScale().coordinateToTime(x);
        let time: number;
        if (scaleTime !== null) {
          time = timeOf(scaleTime);
        } else {
          // Past either end of the data: the first or the last candle.
          const logical = instance.timeScale().coordinateToLogical(x);
          if (logical === null) return null;
          time = (logical < 0 ? points[0] : points[points.length - 1]).time;
        }
        const bar = nearestCandle(points, time);
        return {
          time,
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
