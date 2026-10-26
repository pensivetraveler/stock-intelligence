import type { PriceBar, ReviewTradeListResponse } from "@siw/contracts/types";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { executionRefs, manualGroupOf, type ReviewTrade } from "./review-api";
import { joinBars, tradeChartRequests, tradeMarkers } from "./trade-review";

function fixture<T>(name: string): T {
  return JSON.parse(
    readFileSync(
      resolve(
        import.meta.dirname,
        `../../../tests/fixtures/contracts/valid/${name}`,
      ),
      "utf8",
    ),
  ) as T;
}

const list = fixture<ReviewTradeListResponse>("review-trade-list.json");
const bar = fixture<PriceBar>("price-bar.json");
// Scale-ins on 2 March, exits on 3 March (all with a time).
const swing = list.trades[0] as ReviewTrade;

function barAt(start: string, end: string): PriceBar {
  return { ...bar, interval_start_utc: start, interval_end_utc: end };
}

describe("trade chart", () => {
  it("uses 5-minute bars, one UTC day per request, for a short timed trade", () => {
    const requests = tradeChartRequests(swing, "2026-10-26");
    expect(requests.map((r) => [r.timeframe, r.startUtc, r.endUtc])).toEqual([
      ["5m", "2026-03-02T00:00:00Z", "2026-03-03T00:00:00Z"],
      ["5m", "2026-03-03T00:00:00Z", "2026-03-04T00:00:00Z"],
      ["5m", "2026-03-04T00:00:00Z", "2026-03-05T00:00:00Z"],
    ]);
  });

  it("joins the days' bars in order without repeats", () => {
    const a = barAt("2026-03-02T15:00:00Z", "2026-03-02T15:05:00Z");
    const b = barAt("2026-03-03T15:00:00Z", "2026-03-03T15:05:00Z");
    expect(joinBars([[b], [a, b]])).toEqual([a, b]);
  });

  it("uses daily bars around a longer trade or one without times", () => {
    const dayOnly: ReviewTrade = {
      ...swing,
      executions: swing.executions.map((item) => ({
        ...item,
        executed_at_utc: null,
      })) as ReviewTrade["executions"],
    };
    expect(tradeChartRequests(dayOnly, "2026-03-20")).toEqual([
      {
        instrumentId: "us:aapl:xnas:2026",
        timeframe: "1d",
        startUtc: "2025-12-02T00:00:00Z",
        // 30 days after the exit, but never past today.
        endUtc: "2026-03-21T00:00:00Z",
      },
    ]);
  });

  it("marks each execution on the bar that holds it", () => {
    const intraday = [
      barAt("2026-03-02T15:00:00Z", "2026-03-02T15:05:00Z"),
      barAt("2026-03-03T16:00:00Z", "2026-03-03T16:05:00Z"),
    ];
    const markers = tradeMarkers(swing, intraday, false);
    expect(markers.map((marker) => marker.text)).toEqual([
      "B 10@100",
      "S 15@130",
    ]);
    expect(markers[0]).toMatchObject({
      position: "belowBar",
      shape: "arrowUp",
    });

    // Daily bars: by the session date in New York.
    const daily = [
      barAt("2026-03-02T05:00:00Z", "2026-03-03T05:00:00Z"),
      barAt("2026-03-03T05:00:00Z", "2026-03-04T05:00:00Z"),
    ];
    expect(tradeMarkers(swing, daily, true).map((m) => m.text)).toEqual([
      "B 10@100",
      "B 10@110",
      "S 5@120",
      "S 15@130",
    ]);
  });
});

describe("review api helpers", () => {
  it("lists a trade's executions once and finds its manual group", () => {
    expect(executionRefs(swing)).toHaveLength(4);
    expect(manualGroupOf(swing)).toBeNull();
    expect(manualGroupOf({ ...swing, trade_id: "manual:group-abc123" })).toBe(
      "abc123",
    );
  });
});
