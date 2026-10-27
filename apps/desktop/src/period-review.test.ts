import { describe, expect, it } from "vitest";

import {
  curveTimes,
  daysBetween,
  presetRange,
  weekdayOf,
} from "./period-review";

describe("period dates", () => {
  it("knows this and last week and month", () => {
    // Wednesday 11 March 2026.
    expect(presetRange("thisWeek", "2026-03-11")).toEqual([
      "2026-03-09",
      "2026-03-11",
    ]);
    expect(presetRange("lastWeek", "2026-03-11")).toEqual([
      "2026-03-02",
      "2026-03-08",
    ]);
    expect(presetRange("thisMonth", "2026-03-11")).toEqual([
      "2026-03-01",
      "2026-03-11",
    ]);
    expect(presetRange("lastMonth", "2026-03-11")).toEqual([
      "2026-02-01",
      "2026-02-28",
    ]);
    expect(presetRange("lastMonth", "2026-01-15")).toEqual([
      "2025-12-01",
      "2025-12-31",
    ]);
  });

  it("lists days and weekdays", () => {
    expect(daysBetween("2026-02-27", "2026-03-02")).toEqual([
      "2026-02-27",
      "2026-02-28",
      "2026-03-01",
      "2026-03-02",
    ]);
    expect(weekdayOf("2026-03-02")).toBe(0);
    expect(weekdayOf("2026-03-08")).toBe(6);
  });

  it("keeps curve times rising", () => {
    expect(
      curveTimes([
        { closed_on: "2026-03-02", closed_at_utc: "2026-03-02T16:00:00Z" },
        { closed_on: "2026-03-02", closed_at_utc: "2026-03-02T16:00:00Z" },
        { closed_on: "2026-03-03", closed_at_utc: null },
      ]),
    ).toEqual([1772467200, 1772467201, 1772496000]);
  });
});
