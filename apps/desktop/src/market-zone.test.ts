import { describe, expect, it } from "vitest";

import { dayMarketThrough } from "./market-zone";

describe("dayMarketThrough", () => {
  it("adds tonight's day market to today's chart from 20:00 New York", () => {
    // 2026-11-03 22:35 in New York (EST).
    const evening = new Date("2026-11-04T03:35:00Z");
    expect(dayMarketThrough("2026-11-03", evening)).toBe("2026-11-04");
  });

  it("keeps today's own date before 20:00 New York", () => {
    const afternoon = new Date("2026-11-03T21:00:00Z"); // 16:00 in New York
    expect(dayMarketThrough("2026-11-03", afternoon)).toBe("2026-11-03");
  });

  it("keeps any other date as it is", () => {
    const evening = new Date("2026-11-04T03:35:00Z");
    expect(dayMarketThrough("2026-10-30", evening)).toBe("2026-10-30");
  });

  it("uses New York's clock across daylight saving time", () => {
    // 2026-07-01 20:30 in New York (EDT) is 00:30 UTC the next day.
    expect(
      dayMarketThrough("2026-07-01", new Date("2026-07-02T00:30:00Z")),
    ).toBe("2026-07-02");
  });
});
