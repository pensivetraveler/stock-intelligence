import type { PriceBar, SessionWindow } from "@siw/contracts/types";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { premarketHighs, premarketRange } from "./premarket-high";

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

describe("pre-market highs", () => {
  const day1 = preBar("2026-10-08T08:00:00Z", "2026-10-08T13:30:00Z", "251.10");
  const day2 = preBar(
    "2026-10-09T08:00:00Z",
    "2026-10-09T13:30:00Z",
    "249.75",
    false,
  );

  it("places each pre-market high on the daily bar that starts at its end", () => {
    const daily = [
      seconds("2026-10-08T13:30:00Z"),
      seconds("2026-10-09T13:30:00Z"),
    ];
    expect(premarketHighs([day2, day1], daily)).toEqual([
      { time: daily[0], high: 251.1, final: true },
      { time: daily[1], high: 249.75, final: false },
    ]);
  });

  it("drops bars of other sessions and days not on the chart", () => {
    const regular = { ...day1, session: "regular" as const };
    expect(
      premarketHighs([regular, day1, day2], [seconds("2026-10-09T13:30:00Z")]),
    ).toEqual([
      { time: seconds("2026-10-09T13:30:00Z"), high: 249.75, final: false },
    ]);
  });
});
