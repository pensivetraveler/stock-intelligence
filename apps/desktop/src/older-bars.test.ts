import type { PriceBar } from "@siw/contracts/types";
import { describe, expect, it } from "vitest";

import {
  contiguousFromNewest,
  MAX_EMPTY_SESSIONS,
  olderDailyRange,
  olderSessionBars,
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

  it("skips batches of sessions without trades (a halt) instead of stopping", async () => {
    // Trading days numbered backwards: day 30 is the newest; days 10-29 had no trades.
    const day = (n: number) => `2026-01-${String(n).padStart(2, "0")}`;
    const asked: string[] = [];
    const sessionsBefore = async (endDate: string, count: number) => {
      asked.push(endDate);
      const last = Number(endDate.slice(8));
      const first = Math.max(1, last - count + 1);
      return Array.from({ length: last - first + 1 }, (_, i) => ({
        session_date: day(first + i),
      }));
    };
    const barsOf = async (session: { session_date: string }) =>
      Number(session.session_date.slice(8)) < 10
        ? [bar(`${session.session_date}T14:30:00Z`)]
        : [];
    const found = await olderSessionBars(day(30), 7, sessionsBefore, barsOf);
    expect(found.failed).toBe(false);
    // The batch of days 9-15 holds day 9, the first day with trades before the halt.
    expect(found.bars.map((b) => b.interval_start_utc.slice(0, 10))).toEqual([
      day(9),
    ]);
    expect(found.from).toBe(day(9));
    expect(asked).toEqual([day(29), day(22), day(15)]);
  });

  it("gives up after MAX_EMPTY_SESSIONS empty sessions and stops at a failure", async () => {
    let asked = 0;
    const empty = await olderSessionBars(
      "2026-06-30",
      5,
      async () => {
        asked += 1;
        return [
          { session_date: "2026-01-02" },
          { session_date: "2026-01-05" },
          { session_date: "2026-01-06" },
          { session_date: "2026-01-07" },
          { session_date: "2026-01-08" },
        ];
      },
      async () => [],
    );
    expect(empty.bars).toEqual([]);
    expect(asked).toBe(Math.ceil(MAX_EMPTY_SESSIONS / 5));
    const failed = await olderSessionBars(
      "2026-06-30",
      2,
      async () => [
        { session_date: "2026-06-26" },
        { session_date: "2026-06-29" },
      ],
      async (s) =>
        s.session_date === "2026-06-29" ? null : [bar("2026-06-26T14:30:00Z")],
    );
    expect(failed).toEqual({ bars: [], from: null, failed: true });
  });

  it("jumps over a long halt to the last trading day before it", async () => {
    const asked: string[] = [];
    const sessionsBefore = async (endDate: string) => {
      asked.push(endDate);
      // Two sessions ending with endDate; trades only up to 2026-04-13.
      return [
        { session_date: endDate.replace(/\d\d$/, "01") },
        { session_date: endDate },
      ];
    };
    const barsOf = async (session: { session_date: string }) =>
      session.session_date <= "2026-04-13"
        ? [bar(`${session.session_date}T13:30:00Z`)]
        : [];
    const lastTrade = async (date: string) =>
      date > "2026-04-13" ? "2026-04-13" : null;
    const found = await olderSessionBars(
      "2026-10-07",
      2,
      sessionsBefore,
      barsOf,
      lastTrade,
    );
    // One empty batch, then straight to the batch ending 2026-04-13.
    expect(asked).toEqual(["2026-10-06", "2026-04-13"]);
    expect(found.bars.map((b) => b.interval_start_utc.slice(0, 10))).toEqual([
      "2026-04-01",
      "2026-04-13",
    ]);
    const none = await olderSessionBars(
      "2016-01-05",
      2,
      sessionsBefore,
      async () => [],
      async () => null,
    );
    expect(none).toEqual({ bars: [], from: "2016-01-01", failed: false });
  });
});
