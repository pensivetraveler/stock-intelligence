import {
  CandlestickSeries,
  ColorType,
  createChart,
  CrosshairMode,
  PriceScaleMode,
  createSeriesMarkers,
  HistogramSeries,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type MouseEventParams,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef } from "react";

import type { CandlePoint, VolumePoint } from "./chart-data";
import { COLORS, exchangeTime } from "./chart-data";
import type { ChartSyncGroup } from "./chart-sync";
import type { ChartMarker } from "./journal-markers";

interface PriceChartProps {
  chartId: string;
  candles: CandlePoint[];
  volume: VolumePoint[];
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
}

function timeOf(value: Time): number {
  return typeof value === "number" ? value : 0;
}

/** Candlestick and volume chart; the time axis and crosshair are labelled in ET. */
export function PriceChart({
  chartId,
  candles,
  volume,
  sync,
  markers = [],
  onCandleClick,
  onHover,
  magnet = false,
  logScale = false,
  fitRequest = 0,
}: PriceChartProps) {
  const container = useRef<HTMLDivElement | null>(null);
  const chart = useRef<IChartApi | null>(null);
  const candleSeries = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeries = useRef<ISeriesApi<"Histogram"> | null>(null);
  const markerLayer = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const applyingSyncedRange = useRef(false);
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
        tickMarkFormatter: (time: Time) =>
          exchangeTime(timeOf(time)).replace(" ET", ""),
      },
      localization: {
        timeFormatter: (time: Time) => exchangeTime(timeOf(time)),
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
    instance.timeScale().subscribeVisibleTimeRangeChange(onRange);
    instance.subscribeClick(onClick);
    instance.subscribeCrosshairMove(onMove);
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
      instance.unsubscribeCrosshairMove(onMove);
      instance.unsubscribeClick(onClick);
      instance.timeScale().unsubscribeVisibleTimeRangeChange(onRange);
      instance.remove();
      chart.current = null;
      markerLayer.current = null;
    };
  }, [chartId, sync]);

  useEffect(() => {
    candleSeries.current?.setData(
      candles.map((point) => ({ ...point, time: point.time as UTCTimestamp })),
    );
    volumeSeries.current?.setData(
      volume.map((point) => ({ ...point, time: point.time as UTCTimestamp })),
    );
    chart.current?.timeScale().fitContent();
  }, [candles, volume]);

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
    markerLayer.current?.setMarkers(
      markers.map((marker) => ({
        ...marker,
        time: marker.time as UTCTimestamp,
      })),
    );
  }, [markers, candles]);

  return (
    <div
      className="price-chart"
      ref={container}
      data-testid={`chart-${chartId}`}
    />
  );
}
