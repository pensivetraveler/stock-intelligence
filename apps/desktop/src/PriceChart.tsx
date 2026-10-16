import {
  CandlestickSeries,
  ColorType,
  createChart,
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
}: PriceChartProps) {
  const container = useRef<HTMLDivElement | null>(null);
  const chart = useRef<IChartApi | null>(null);
  const candleSeries = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeries = useRef<ISeriesApi<"Histogram"> | null>(null);
  const markerLayer = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const applyingSyncedRange = useRef(false);
  const clickHandler = useRef(onCandleClick);

  useEffect(() => {
    clickHandler.current = onCandleClick;
  }, [onCandleClick]);

  useEffect(() => {
    if (!container.current) {
      return;
    }
    const instance = createChart(container.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "#131a24" },
        textColor: "#9eabba",
      },
      grid: {
        vertLines: { color: "#1f2834" },
        horzLines: { color: "#1f2834" },
      },
      rightPriceScale: { borderColor: "#2d3746" },
      timeScale: {
        borderColor: "#2d3746",
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
    instance.timeScale().subscribeVisibleTimeRangeChange(onRange);
    instance.subscribeClick(onClick);
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
