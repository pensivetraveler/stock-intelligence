import type { PriceBar } from "@siw/contracts/types";
import { describe, expect, it } from "vitest";

import {
  alignToChart,
  higherBarsOf,
  higherChoices,
  higherRange,
} from "./higher-timeframe";
import {
  parseIndicators,
  renderIndicators,
  warmupBars,
  type OhlcvBar,
} from "./indicators";

const daily = (day: string, close: number, final = true) =>
  ({
    interval_start_utc: `${day}T13:30:00Z`,
    interval_end_utc: `${day}T20:00:00Z`,
    available_at_utc: `${day}T20:15:00Z`,
    open: String(close),
    high: String(close + 1),
    low: String(close - 1),
    close: String(close),
    volume: "1000",
    is_final: final,
  }) as PriceBar;

const at = (utc: string) => Math.floor(Date.parse(utc) / 1000);

describe("larger-timeframe indicators", () => {
  it("offer only timeframes larger than the chart's", () => {
    expect(higherChoices("5m")).toEqual(["1d", "1w"]);
    expect(higherChoices("1d")).toEqual(["1w"]);
    expect(higherChoices("1w")).toEqual([]);
  });

  it("use final bars only and show a value once it was published", () => {
    const source = higherBarsOf([
      daily("2026-10-08", 10),
      daily("2026-10-07", 9),
      daily("2026-10-09", 11, false), // still forming: never used
    ]);
    expect(source.bars.map((bar) => bar.close)).toEqual([9, 10]);
    const values = { value: [9, 10] };
    const ends = [
      at("2026-10-08T15:00:00Z"), // during the 8th: only the 7th is known
      at("2026-10-08T20:05:00Z"), // after the close but before publication
      at("2026-10-08T20:30:00Z"), // after publication
      at("2026-10-09T19:00:00Z"), // the 9th is not final
    ];
    expect(alignToChart(values, source.availableAt, ends).value).toEqual([
      9, 9, 10, 10,
    ]);
    expect(
      alignToChart(values, source.availableAt, [at("2026-10-07T15:00:00Z")])
        .value,
    ).toEqual([null]);
  });

  it("are drawn from the larger bars, aligned to the chart, and need no chart warm-up", () => {
    const [instance] = parseIndicators([
      { key: "s", type: "sma", params: { length: 2 }, timeframe: "1d" },
    ]);
    expect(instance.timeframe).toBe("1d");
    expect(warmupBars([instance])).toBe(0);
    const chart: OhlcvBar[] = [0, 1, 2].map((i) => ({
      time: at("2026-10-09T14:00:00Z") + i * 300,
      open: 1,
      high: 1,
      low: 1,
      close: 1,
      volume: 1,
    }));
    const source = higherBarsOf([
      daily("2026-10-07", 9),
      daily("2026-10-08", 11),
    ]);
    const [rendered] = renderIndicators(chart, [instance], 0, undefined, {
      bars: { "1d": source },
      ends: chart.map((bar) => bar.time + 300),
    });
    expect(rendered.values.value).toEqual([10, 10, 10]);
    // Without the larger bars yet, nothing is drawn.
    expect(renderIndicators(chart, [instance])[0].lines[0].data).toEqual([]);
  });

  it("keep Python indicators on the chart's own bars", () => {
    expect(
      parseIndicators([
        { key: "p", type: "py:ind-x@1", params: {}, timeframe: "1d" },
      ])[0].timeframe,
    ).toBeUndefined();
  });

  it("fetch enough larger bars to warm up", () => {
    const range = higherRange(
      at("2026-10-09T13:30:00Z"),
      at("2026-10-09T20:00:00Z"),
      20,
      "1d",
    );
    expect(range.endUtc).toBe("2026-10-09T20:00:00Z");
    expect(Date.parse(range.startUtc)).toBeLessThan(
      Date.parse("2026-07-01T00:00:00Z"),
    );
  });
});
