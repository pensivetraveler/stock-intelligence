import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  type UTCTimestamp,
} from "lightweight-charts";

import { toSeries } from "./chart-data";
import type { ReportImage } from "./review-api";
import { TRADE_COLORS } from "./trade-review";

const WIDTH = 960;
const HEIGHT = 540;

function nextFrame(): Promise<void> {
  return new Promise((resolve) => requestAnimationFrame(() => resolve()));
}

/**
 * One report image as base64 PNG (ADR 0042, step 2a). It is drawn off screen from the
 * bars the API cut at the image's time, never captured from the visible chart, so it
 * cannot show a later bar.
 */
export async function renderChartImage(image: ReportImage): Promise<string> {
  const host = document.createElement("div");
  host.style.cssText = `position:fixed;left:-${WIDTH * 2}px;top:0;width:${WIDTH}px;height:${HEIGHT}px;`;
  document.body.appendChild(host);
  const chart = createChart(host, {
    width: WIDTH,
    height: HEIGHT,
    layout: {
      background: { type: ColorType.Solid, color: "#10131a" },
      textColor: "#c9d1dc",
    },
    grid: {
      vertLines: { color: "#1a202a" },
      horzLines: { color: "#1a202a" },
    },
    timeScale: { timeVisible: image.timeframe.endsWith("m") },
  });
  try {
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: "#3fb68b",
      downColor: "#e5566b",
      borderVisible: false,
      wickUpColor: "#3fb68b",
      wickDownColor: "#e5566b",
    });
    candles.setData(
      toSeries(image.bars).candles.map((point) => ({
        ...point,
        time: point.time as UTCTimestamp,
      })),
    );
    createSeriesMarkers(
      candles,
      image.markers
        .map((marker) => {
          const buy = marker.side === "buy";
          return {
            time: Math.floor(
              Date.parse(marker.time_utc) / 1000,
            ) as UTCTimestamp,
            position: buy ? ("belowBar" as const) : ("aboveBar" as const),
            shape: buy ? ("arrowUp" as const) : ("arrowDown" as const),
            color: buy ? TRADE_COLORS.buy : TRADE_COLORS.sell,
            text: `${buy ? "B" : "S"} ${marker.quantity}@${marker.price}`,
          };
        })
        .sort((a, b) => a.time - b.time),
    );
    chart.timeScale().fitContent();
    // The chart paints on animation frames; wait for it before taking the picture.
    await nextFrame();
    await nextFrame();
    const url = chart.takeScreenshot().toDataURL("image/png");
    return url.slice(url.indexOf(",") + 1);
  } finally {
    chart.remove();
    host.remove();
  }
}
