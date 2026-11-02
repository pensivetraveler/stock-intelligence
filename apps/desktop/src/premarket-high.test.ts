import type { PriceBar, SessionWindow } from "@siw/contracts/types";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { premarketBars, premarketRange, splitFactors } from "./premarket-high";

const fixtures = resolve(
  import.meta.dirname,
  "../../../tests/fixtures/contracts/valid",
);

function fixture<T>(name: string): T {
  return JSON.parse(readFileSync(resolve(fixtures, name), "utf8")) as T;
}

const window: SessionWindow = {
  kind: "session_window",
  schema_version: "0.1",
  calendar_id: "XNAS",
  session_date: "2026-10-09",
  is_trading_day: true,
  is_early_close: false,
  pre_open_utc: "2026-10-09T08:00:00Z",
  regular_open_utc: "2026-10-09T13:30:00Z",
  regular_close_utc: "2026-10-09T20:00:00Z",
  post_close_utc: "2026-10-10T00:00:00Z",
  calendar_version: "v",
  extended_hours_policy_version: "p",
};

const seconds = (utc: string) => Date.parse(utc) / 1000;
// Open, low and close of the fixture bar; each test sets its own high.
const ohlc = { open: 250.1, low: 249.9, close: 250.25 };

function preBar(
  start: string,
  end: string,
  high: string,
  final = true,
): PriceBar {
  return {
    ...fixture<PriceBar>("price-bar.json"),
    timeframe: "960m",
    session: "pre",
    interval_start_utc: start,
    interval_end_utc: end,
    high,
    is_final: final,
    quality: final ? "complete" : "partial",
  };
}

describe("pre-market range", () => {
  it("is the session's pre-market window", () => {
    expect(premarketRange(window)).toEqual({
      startUtc: "2026-10-09T08:00:00Z",
      endUtc: "2026-10-09T13:30:00Z",
    });
  });

  it("is null on a closed day", () => {
    expect(premarketRange(fixture("session-window-holiday.json"))).toBeNull();
  });
});

describe("pre-market bars", () => {
  const day1 = preBar("2026-10-08T08:00:00Z", "2026-10-08T13:30:00Z", "251.10");
  const day2 = preBar(
    "2026-10-09T08:00:00Z",
    "2026-10-09T13:30:00Z",
    "249.75",
    false,
  );

  it("places each pre-market candle on the daily bar that starts at its end", () => {
    const daily = [
      seconds("2026-10-08T13:30:00Z"),
      seconds("2026-10-09T13:30:00Z"),
    ];
    expect(premarketBars([day2, day1], daily)).toEqual([
      { time: daily[0], ...ohlc, high: 251.1, final: true },
      { time: daily[1], ...ohlc, high: 249.75, final: false },
    ]);
  });

  it("drops bars of other sessions and days not on the chart", () => {
    const regular = { ...day1, session: "regular" as const };
    expect(
      premarketBars([regular, day1, day2], [seconds("2026-10-09T13:30:00Z")]),
    ).toEqual([
      {
        time: seconds("2026-10-09T13:30:00Z"),
        ...ohlc,
        high: 249.75,
        final: false,
      },
    ]);
  });
});

describe("pre-market candles over split-adjusted daily candles", () => {
  const daily = (date: string, close: string): PriceBar => ({
    ...fixture<PriceBar>("price-bar.json"),
    timeframe: "1d",
    interval_start_utc: `${date}T13:30:00Z`,
    interval_end_utc: `${date}T20:00:00Z`,
    close,
  });
  const before = seconds("2020-08-28T13:30:00Z");
  const after = seconds("2020-08-31T13:30:00Z");
  const factors = splitFactors(
    [daily("2020-08-28", "124.8075"), daily("2020-08-31", "129.04")],
    [daily("2020-08-28", "499.23"), daily("2020-08-31", "129.04")],
  );
  const pre = (date: string, high: string) =>
    preBar(`${date}T08:00:00Z`, `${date}T13:30:00Z`, high);

  it("takes each session's ratio from its own daily bars", () => {
    expect(factors.get(before)).toBeCloseTo(0.25, 10);
    expect(factors.get(after)).toBe(1);
  });

  it("treats the rounding of adjusted prices as no split", () => {
    const rounded = splitFactors(
      [daily("2020-08-28", "0.51")],
      [daily("2020-08-28", "0.5123")],
    );
    expect(rounded.get(before)).toBe(1);
  });

  it("scales a candle before a split and leaves one after it as traded", () => {
    const bars = premarketBars(
      [pre("2020-08-28", "500"), pre("2020-08-31", "130")],
      [before, after],
      factors,
    );
    expect(bars[0].high).toBeCloseTo(125, 8);
    expect(bars[0].adjusted).toBe(true);
    expect(bars[1].high).toBe(130);
    expect(bars[1].adjusted).toBeUndefined();
  });

  it("draws no candle for a session whose ratio is not known", () => {
    expect(
      premarketBars([pre("2020-08-28", "500")], [before], new Map()),
    ).toEqual([]);
  });

  it("leaves candles as traded when the daily candles are", () => {
    const [bar] = premarketBars([pre("2020-08-28", "500")], [before], null);
    expect(bar.high).toBe(500);
    expect(bar.adjusted).toBeUndefined();
  });
});
