/**
 * Draws pre-market highs (ADR 0041) as a short level across each daily candle. The
 * marks are not joined from day to day, so they never read as one continuous
 * indicator line. Marks not yet final are dashed.
 */
import type {
  AutoscaleInfo,
  IChartApiBase,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  Logical,
  SeriesAttachedParameter,
  SeriesType,
  Time,
  UTCTimestamp,
} from "lightweight-charts";

import type { PremarketHigh } from "./premarket-high";

type Target = Parameters<IPrimitivePaneRenderer["draw"]>[0];

export const PREMARKET_COLOR = "#f0b429";

class Renderer implements IPrimitivePaneRenderer {
  constructor(private readonly owner: PremarketHighPrimitive) {}

  draw(target: Target): void {
    target.useMediaCoordinateSpace(({ context }) => {
      const marks = this.owner.marks();
      if (marks.length === 0) return;
      context.save();
      try {
        context.strokeStyle = PREMARKET_COLOR;
        context.lineWidth = 2;
        for (const mark of marks) {
          context.setLineDash(mark.final ? [] : [3, 2]);
          context.beginPath();
          context.moveTo(mark.x - mark.half, mark.y);
          context.lineTo(mark.x + mark.half, mark.y);
          context.stroke();
        }
      } finally {
        context.restore();
      }
    });
  }
}

class View implements IPrimitivePaneView {
  private readonly drawer: Renderer;
  constructor(owner: PremarketHighPrimitive) {
    this.drawer = new Renderer(owner);
  }

  zOrder() {
    return "top" as const;
  }

  renderer() {
    return this.drawer;
  }
}

export class PremarketHighPrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApiBase<Time> | null = null;
  private series: ISeriesApi<SeriesType, Time> | null = null;
  private requestUpdate: (() => void) | null = null;
  private readonly views: IPrimitivePaneView[];

  /** `times`: the chart's candle times, to find the marks inside a logical range. */
  constructor(
    private highs: PremarketHigh[],
    private readonly times: () => number[],
  ) {
    this.views = [new View(this)];
  }

  attached(param: SeriesAttachedParameter<Time, SeriesType>): void {
    this.chart = param.chart;
    this.series = param.series;
    this.requestUpdate = param.requestUpdate;
  }

  detached(): void {
    this.chart = null;
    this.series = null;
    this.requestUpdate = null;
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this.views;
  }

  /** Widen the price scale so a pre-market high above the day's high stays visible. */
  autoscaleInfo(start: Logical, end: Logical): AutoscaleInfo | null {
    const times = this.times();
    const first = times[Math.max(0, Math.floor(start))];
    const last = times[Math.min(times.length - 1, Math.ceil(end))];
    if (first === undefined || last === undefined) return null;
    const prices = this.highs
      .filter((item) => item.time >= first && item.time <= last)
      .map((item) => item.high);
    if (prices.length === 0) return null;
    return {
      priceRange: {
        minValue: Math.min(...prices),
        maxValue: Math.max(...prices),
      },
    };
  }

  update(highs: PremarketHigh[]): void {
    this.highs = highs;
    this.requestUpdate?.();
  }

  /** Pixel positions of the marks in view. */
  marks(): { x: number; y: number; half: number; final: boolean }[] {
    const chart = this.chart;
    const series = this.series;
    if (!chart || !series) return [];
    const scale = chart.timeScale();
    // Across most of the candle's slot, at least a few pixels wide.
    const half = Math.max(3, scale.options().barSpacing * 0.45);
    return this.highs.flatMap((item) => {
      const x = scale.timeToCoordinate(item.time as UTCTimestamp);
      const y = series.priceToCoordinate(item.high);
      return x === null || y === null
        ? []
        : [{ x, y, half, final: item.final }];
    });
  }
}
