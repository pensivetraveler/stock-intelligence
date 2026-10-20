import type { BacktestResults } from "@siw/contracts/types";
import {
  AreaSeries,
  ColorType,
  createChart,
  LineSeries,
  type IChartApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef } from "react";

const BASE = {
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
  // The wheel scrolls the results column; dragging and pinching still pan and zoom.
  handleScroll: { mouseWheel: false },
  handleScale: { mouseWheel: false },
} as const;

const CURVE_COLORS = {
  equity: "#5b8def",
  benchmark: "#9eabba",
  drawdown: "#f23645",
};

const seconds = (utc: string) =>
  Math.floor(Date.parse(utc) / 1000) as UTCTimestamp;

/** Equity against the buy-and-hold benchmark, and the drawdown below it. */
export function BacktestChart({
  curve,
  labels,
}: {
  curve: BacktestResults["equity_curve"];
  labels: { equity: string; benchmark: string; drawdown: string };
}) {
  const top = useRef<HTMLDivElement | null>(null);
  const bottom = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!top.current || !bottom.current) return;
    const charts: IChartApi[] = [
      createChart(top.current, BASE),
      createChart(bottom.current, BASE),
    ];
    const [equityChart, drawdownChart] = charts;
    const equity = equityChart.addSeries(LineSeries, {
      color: CURVE_COLORS.equity,
      lineWidth: 2,
      title: labels.equity,
    });
    const benchmark = equityChart.addSeries(LineSeries, {
      color: CURVE_COLORS.benchmark,
      lineWidth: 1,
      title: labels.benchmark,
    });
    const drawdown = drawdownChart.addSeries(AreaSeries, {
      lineColor: CURVE_COLORS.drawdown,
      topColor: "rgba(242, 54, 69, 0.05)",
      bottomColor: "rgba(242, 54, 69, 0.35)",
      lineWidth: 1,
      title: labels.drawdown,
      priceFormat: { type: "percent" },
    });
    equity.setData(
      curve.map((point) => ({
        time: seconds(point.time_utc),
        value: Number(point.equity),
      })),
    );
    benchmark.setData(
      curve.map((point) => ({
        time: seconds(point.time_utc),
        value: Number(point.benchmark_equity),
      })),
    );
    drawdown.setData(
      curve.map((point) => ({
        time: seconds(point.time_utc),
        value: -Number(point.drawdown) * 100,
      })),
    );
    for (const chart of charts) chart.timeScale().fitContent();
    // Scrolling one chart scrolls the other, so a drawdown lines up with its equity.
    let syncing = false;
    const follow = (from: IChartApi, to: IChartApi) =>
      from.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        if (syncing || range === null) return;
        syncing = true;
        to.timeScale().setVisibleLogicalRange(range);
        syncing = false;
      });
    follow(equityChart, drawdownChart);
    follow(drawdownChart, equityChart);
    return () => {
      for (const chart of charts) chart.remove();
    };
  }, [curve, labels.equity, labels.benchmark, labels.drawdown]);

  return (
    <div className="bt-charts" data-points={curve.length}>
      <div ref={top} className="bt-chart-equity" />
      <div ref={bottom} className="bt-chart-drawdown" />
    </div>
  );
}
