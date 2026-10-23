import type { PriceBar } from "@siw/contracts/types";
import { describe, expect, it } from "vitest";

import {
  contiguousFromNewest,
  olderDailyRange,
  prependOlder,
} from "./older-bars";
import { dailyRange } from "./timeframes";

function bar(start: string): PriceBar {
  return { interval_start_utc: start } as PriceBar;
}

describe("older bars", () => {
  it("asks for the daily range just before the shown one, without overlap", () => {
    const shown = dailyRange("1d", "2026-10-22");
    const older = olderDailyRange("1d", shown.startUtc.slice(0, 10));
    expect(older.endUtc).toBe(shown.startUtc);
    expect(older.startUtc < older.endUtc).toBe(true);
    const weekly = olderDailyRange("1w", "2003-01-01");
    expect(weekly.endUtc).toBe("2003-01-01T00:00:00Z");
  });

  it("puts only earlier bars in front", () => {
    const older = [
      bar("2026-10-20T19:59:00Z"),
      bar("2026-10-21T13:30:00Z"), // already on the chart
    ];
    const first = prependOlder([], older, "2026-10-21T13:30:00Z");
    expect(first.map((b) => b.interval_start_utc)).toEqual([
      "2026-10-20T19:59:00Z",
    ]);
    // A second batch is measured against the first batch's earliest bar.
    const second = prependOlder(
      first,
      [bar("2026-10-19T19:59:00Z"), bar("2026-10-20T19:59:00Z")],
      "2026-10-21T13:30:00Z",
    );
    expect(second.map((b) => b.interval_start_utc)).toEqual([
      "2026-10-19T19:59:00Z",
      "2026-10-20T19:59:00Z",
    ]);
    expect(prependOlder([], older, null)).toEqual(older);
  });

  it("keeps sessions only while they run back unbroken from the newest", () => {
    expect(contiguousFromNewest([[1], [2, 3], [4]])).toEqual({
      bars: [1, 2, 3, 4],
      sessions: 3,
    });
    // The second-oldest session failed: the oldest one is not drawn over the gap.
    expect(contiguousFromNewest([[1], null, [4]])).toEqual({
      bars: [4],
      sessions: 1,
    });
    expect(contiguousFromNewest([[1], [4], null])).toEqual({
      bars: [],
      sessions: 0,
    });
  });
});
