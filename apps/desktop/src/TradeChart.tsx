import type { PriceBar } from "@siw/contracts/types";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef } from "react";

import { toSeries } from "./chart-data";
import type { TradeMarker } from "./trade-review";

/** Candles of one trade's instrument with its entries and exits marked. */
export function TradeChart({
  bars,
  markers,
}: {
  bars: PriceBar[];
  markers: TradeMarker[];
}) {
  const host = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!host.current) return;
    const chart = createChart(host.current, {
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
      timeScale: { borderColor: "#232a36", timeVisible: true },
    });
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: "#3fb68b",
      downColor: "#e5566b",
      borderVisible: false,
      wickUpColor: "#3fb68b",
      wickDownColor: "#e5566b",
    });
    candles.setData(
      toSeries(bars).candles.map((point) => ({
        ...point,
        time: point.time as UTCTimestamp,
      })),
    );
    createSeriesMarkers(
      candles,
      markers.map((marker) => ({
        ...marker,
        time: marker.time as UTCTimestamp,
      })),
    );
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [bars, markers]);

  return <div ref={host} className="rv-chart" data-testid="trade-chart" />;
}
