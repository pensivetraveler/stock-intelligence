import { describe, expect, it } from "vitest";

import {
  defaultFibSettings,
  durationText,
  fibExtensionLevels,
  fibLevels,
  fibSettingsOf,
  measure,
  parseDrawings,
  snapPrice,
} from "./drawings";

describe("drawings", () => {
  it("places retracement levels from the end of the move back to its start", () => {
    const levels = fibLevels({ time: 0, price: 100 }, { time: 60, price: 200 });
    expect(levels.map((level) => level.ratio)).toEqual([
      0, 0.236, 0.382, 0.5, 0.618, 0.786, 1,
    ]);
    expect(levels[0].price).toBe(200);
    expect(levels[3].price).toBe(150);
    expect(levels[4].price).toBeCloseTo(138.2);
    expect(levels[6].price).toBe(100);
  });

  it("measures price change, percent and bars", () => {
    expect(
      measure({ time: 0, price: 200 }, { time: 900, price: 190 }, 300),
    ).toEqual({ change: -10, percent: -5, bars: 3, seconds: 900 });
    expect(
      measure({ time: 0, price: 0 }, { time: 60, price: 1 }, 60).percent,
    ).toBe(null);
  });

  it("snaps to the nearest open, high, low or close only when a bar is given", () => {
    const bar = { open: 10, high: 12, low: 9, close: 11 };
    expect(snapPrice(11.8, bar)).toBe(12);
    expect(snapPrice(9.4, bar)).toBe(9);
    expect(snapPrice(9.4, undefined)).toBe(9.4);
  });

  it("writes durations compactly", () => {
    expect(durationText(300)).toBe("5m");
    expect(durationText(5_400)).toBe("1h 30m");
    expect(durationText(90_000)).toBe("1d 1h");
    expect(durationText(0)).toBe("0m");
  });

  it("keeps only well-formed saved drawings", () => {
    expect(
      parseDrawings([
        {
          id: "a",
          kind: "trend",
          instrumentId: "us:aapl:xnas:2026",
          points: [
            { time: 1, price: 2 },
            { time: 3, price: 4 },
          ],
        },
        { id: "b", kind: "hline", instrumentId: "x", points: [] },
        {
          id: "c",
          kind: "text",
          instrumentId: "x",
          points: [{ time: 1, price: 1 }],
        },
        {
          id: "d",
          kind: "text",
          instrumentId: "x",
          points: [{ time: 1, price: 1 }],
          text: "breakout",
        },
        { id: "e", kind: "measure", instrumentId: "x", points: [] },
        "junk",
      ]).map((item) => item.id),
    ).toEqual(["a", "d"]);
    expect(parseDrawings(null)).toEqual([]);
  });
  it("adds TradingView's extension levels beyond the move, and reverses on request", () => {
    const a = { time: 0, price: 100 };
    const b = { time: 60, price: 200 };
    const settings = defaultFibSettings();
    const levels = fibLevels(a, b, settings);
    expect(levels.map((level) => level.ratio)).toEqual([
      0, 0.236, 0.382, 0.5, 0.618, 0.786, 1, 1.618, 2.618, 3.618, 4.236,
    ]);
    // 1.618 of a 100-point move, measured back from the end at 200.
    expect(levels[7].price).toBeCloseTo(38.2);
    const reversed = fibLevels(a, b, { ...settings, reverse: true });
    expect(reversed[0].price).toBe(100);
    expect(reversed[6].price).toBe(200);
  });

  it("projects a trend-based extension from the third point", () => {
    const levels = fibExtensionLevels(
      { time: 0, price: 100 },
      { time: 60, price: 150 },
      { time: 120, price: 130 },
      { ...defaultFibSettings(), levels: [0, 0.618, 1, 1.618] },
    );
    expect(
      levels.map((level) => [level.ratio, Number(level.price.toFixed(2))]),
    ).toEqual([
      [0, 130],
      [0.618, 160.9],
      [1, 180],
      [1.618, 210.9],
    ]);
  });

  it("keeps the levels of retracements saved before the options existed, now shaded", () => {
    const legacy = fibSettingsOf({ kind: "fib" });
    expect(legacy.levels).toEqual([0, 0.236, 0.382, 0.5, 0.618, 0.786, 1]);
    expect(legacy).toMatchObject({ extendRight: true, background: true });
    expect(fibSettingsOf({ kind: "fibext" })).toEqual(defaultFibSettings());
  });

  it("reads extensions with three points and cleans their options", () => {
    const [extension] = parseDrawings([
      {
        id: "e",
        kind: "fibext",
        instrumentId: "us:aapl:xnas:2026",
        points: [
          { time: 1, price: 1 },
          { time: 2, price: 2 },
          { time: 3, price: 1.5 },
        ],
        fib: {
          levels: [1.618, 0, "x", 99, 0.5, 0.5],
          reverse: true,
          extendLeft: "yes",
        },
      },
    ]);
    expect(extension.fib).toEqual({
      ...defaultFibSettings(),
      levels: [0, 0.5, 1.618],
      reverse: true,
    });
    expect(
      parseDrawings([
        {
          id: "x",
          kind: "fibext",
          instrumentId: "i",
          points: [{ time: 1, price: 1 }],
        },
      ]),
    ).toEqual([]);
  });
});
