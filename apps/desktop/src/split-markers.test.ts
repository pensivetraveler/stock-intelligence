import { describe, expect, it } from "vitest";

import { translator } from "./i18n";
import { SPLIT_COLORS, splitMarkers, splitRatio } from "./split-markers";

const t = translator("en");

function day(date: string) {
  return {
    interval_start_utc: `${date}T13:30:00Z`,
    interval_end_utc: `${date}T20:00:00Z`,
  } as never;
}

describe("split markers", () => {
  it("writes ratios as new shares to old", () => {
    expect(splitRatio(10, 1)).toBe("10:1");
    expect(splitRatio(1, 15)).toBe("1:15");
    expect(splitRatio(4, 25)).toBe("1:6.25");
  });

  it("puts a split on the bar of its date and says where it came from", () => {
    const [marker] = splitMarkers(
      [day("2024-06-07"), day("2024-06-10")],
      [
        {
          kind: "not_compared",
          filing: null,
          provider: { on: "2024-06-10", numerator: 10, denominator: 1 },
        },
      ],
      "America/New_York",
      t,
    );
    expect(marker.time).toBe(Date.parse("2024-06-10T13:30:00Z") / 1000);
    expect(marker.text).toBe("Split 10:1");
    expect(marker.color).toBe(SPLIT_COLORS.agreed);
    expect(marker.details?.[0]).toContain("did not read filings");
  });

  it("marks a reading Alpaca does not name in another colour", () => {
    const [marker] = splitMarkers(
      [day("2025-05-19")],
      [
        {
          kind: "filing_only",
          filing: {
            effective: "2025-05-19",
            numerator: 1,
            denominator: 15,
            status: "confirmed",
          },
          provider: null,
        },
      ],
      "America/New_York",
      t,
    );
    expect(marker.text).toBe("Reverse split 1:15");
    expect(marker.color).toBe(SPLIT_COLORS.differs);
  });

  it("draws nothing for a split outside the bars", () => {
    expect(
      splitMarkers(
        [day("2024-06-07")],
        [
          {
            kind: "same",
            filing: null,
            provider: { on: "2020-08-31", numerator: 4, denominator: 1 },
          },
        ],
        "America/New_York",
        t,
      ),
    ).toEqual([]);
  });
});
