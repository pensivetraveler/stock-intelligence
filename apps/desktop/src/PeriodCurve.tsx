import {
  BaselineSeries,
  ColorType,
  createChart,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef } from "react";

import { curveTimes } from "./period-review";
import { chartChrome, DECK, followTheme } from "./theme";

/** Cumulative net profit over the period: green above zero, red below. */
export function PeriodCurve({
  points,
}: {
  points: {
    closed_on: string;
    closed_at_utc: string | null;
    cumulative: string;
  }[];
}) {
  const host = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!host.current || points.length === 0) return;
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
      timeScale: { timeVisible: true },
      handleScroll: { mouseWheel: false },
      handleScale: { mouseWheel: false },
    });
    const series = chart.addSeries(BaselineSeries, {
      baseValue: { type: "price", price: 0 },
      topLineColor: DECK.up,
      bottomLineColor: DECK.down,
    });
    const times = curveTimes(points);
    series.setData(
      points.map((point, index) => ({
        time: times[index] as UTCTimestamp,
        value: Number(point.cumulative),
      })),
    );
    chart.timeScale().fitContent();
    const stopTheme = followTheme(chart);
    return () => {
      stopTheme();
      chart.remove();
    };
  }, [points]);

  return <div ref={host} className="rv-curve" data-testid="period-curve" />;
}
