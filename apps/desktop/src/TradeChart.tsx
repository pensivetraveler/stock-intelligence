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
import { chartChrome, DECK, followTheme } from "./theme";

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
    const chrome = chartChrome();
    const chart = createChart(host.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: chrome.background },
        textColor: chrome.text,
      },
      grid: {
        vertLines: { color: chrome.grid },
        horzLines: { color: chrome.grid },
      },
      rightPriceScale: { borderColor: chrome.border },
      timeScale: { borderColor: chrome.border, timeVisible: true },
    });
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: DECK.up,
      downColor: DECK.down,
      borderVisible: false,
      wickUpColor: DECK.up,
      wickDownColor: DECK.down,
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
    const stopTheme = followTheme(chart);
    return () => {
      stopTheme();
      chart.remove();
    };
  }, [bars, markers]);

  return <div ref={host} className="rv-chart" data-testid="trade-chart" />;
}
