import {
  type CandlestickData,
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  type SeriesMarker,
  type UTCTimestamp,
} from "lightweight-charts";

import { toSeries } from "./chart-data";
import type { OhlcvBar } from "./indicators";
import type { ReportImage, ToolImage } from "./review-api";
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
export function renderChartImage(
  image: ReportImage | ToolImage,
): Promise<string> {
  return drawCandles(
    toSeries(image.bars).candles.map((point) => ({
      ...point,
      time: point.time as UTCTimestamp,
    })),
    image.timeframe,
    image.markers
      .map((marker) => {
        const buy = marker.side === "buy";
        return {
          time: Math.floor(Date.parse(marker.time_utc) / 1000) as UTCTimestamp,
          position: buy ? ("belowBar" as const) : ("aboveBar" as const),
          shape: buy ? ("arrowUp" as const) : ("arrowDown" as const),
          color: buy ? TRADE_COLORS.buy : TRADE_COLORS.sell,
          text: `${buy ? "B" : "S"} ${marker.quantity}@${marker.price}`,
        };
      })
      .sort((a, b) => a.time - b.time),
  );
}

/**
 * The bars in view as base64 PNG, for the indicator assistant (ADR 0042). The same off
 * screen drawing, so only these bars and no account marks can appear in the picture.
 */
export function renderBarsImage(
  bars: OhlcvBar[],
  timeframe: string,
): Promise<string> {
  return drawCandles(
    bars.map(({ time, open, high, low, close }) => ({
      time: time as UTCTimestamp,
      open,
      high,
      low,
      close,
    })),
    timeframe,
    [],
  );
}

async function drawCandles(
  points: CandlestickData<UTCTimestamp>[],
  timeframe: string,
  markers: SeriesMarker<UTCTimestamp>[],
): Promise<string> {
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
    timeScale: { timeVisible: timeframe.endsWith("m") },
  });
  try {
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: "#3fb68b",
      downColor: "#e5566b",
      borderVisible: false,
      wickUpColor: "#3fb68b",
      wickDownColor: "#e5566b",
    });
    candles.setData(points);
    createSeriesMarkers(candles, markers);
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
