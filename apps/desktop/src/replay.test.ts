import type { PriceBar } from "@siw/contracts/types";
import { describe, expect, it } from "vitest";

import { barsAsOf, stepBack, stepForward } from "./replay";

function bar(start: string, end: string): PriceBar {
  return {
    interval_start_utc: start,
    interval_end_utc: end,
  } as PriceBar;
}

const bars = [
  bar("2026-10-09T13:30:00Z", "2026-10-09T13:35:00Z"),
  bar("2026-10-09T13:35:00Z", "2026-10-09T13:40:00Z"),
  bar("2026-10-09T13:40:00Z", "2026-10-09T13:45:00Z"),
];
const at = (utc: string) => Date.parse(utc) / 1000;

describe("replay", () => {
  it("shows only bars that had closed at the replay time", () => {
    expect(barsAsOf(bars, null)).toHaveLength(3);
    expect(barsAsOf(bars, at("2026-10-09T13:35:00Z"))).toEqual([bars[0]]);
    // A bar still in progress at the replay time stays hidden.
    expect(barsAsOf(bars, at("2026-10-09T13:39:00Z"))).toEqual([bars[0]]);
    expect(barsAsOf(bars, at("2026-10-09T13:30:00Z"))).toEqual([]);
  });

  it("steps by the chart timeframe and stops at the last loaded bar", () => {
    const last = at("2026-10-09T13:45:00Z");
    expect(stepForward(at("2026-10-09T13:35:00Z"), 300, last)).toBe(
      at("2026-10-09T13:40:00Z"),
    );
    expect(stepForward(at("2026-10-09T13:43:00Z"), 300, last)).toBe(last);
    expect(stepForward(last, 300, last)).toBeNull();
    expect(
      stepBack(at("2026-10-09T13:40:00Z"), 300, at("2026-10-09T13:35:00Z")),
    ).toBe(at("2026-10-09T13:35:00Z"));
    expect(
      stepBack(at("2026-10-09T13:36:00Z"), 300, at("2026-10-09T13:35:00Z")),
    ).toBe(at("2026-10-09T13:35:00Z"));
  });
});
