import {
  BaselineSeries,
  ColorType,
  createChart,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef } from "react";

import { curveTimes } from "./period-review";

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
      timeScale: { timeVisible: true },
      handleScroll: { mouseWheel: false },
      handleScale: { mouseWheel: false },
    });
    const series = chart.addSeries(BaselineSeries, {
      baseValue: { type: "price", price: 0 },
      topLineColor: "#3fb68b",
      bottomLineColor: "#e5566b",
    });
    const times = curveTimes(points);
    series.setData(
      points.map((point, index) => ({
        time: times[index] as UTCTimestamp,
        value: Number(point.cumulative),
      })),
    );
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [points]);

  return <div ref={host} className="rv-curve" data-testid="period-curve" />;
}
