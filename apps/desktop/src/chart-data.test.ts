import type { MarketBarsResponse, PriceBar } from "@siw/contracts/types";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";

import { MarketApiError } from "./market-api";
import {
  chartPriceBars,
  COLORS,
  exchangeTime,
  seeThrough,
  seeThroughCandles,
  summarize,
  toSeries,
} from "./chart-data";

const fixtures = resolve(
  import.meta.dirname,
  "../../../tests/fixtures/contracts/valid",
);

function fixture<T>(name: string): T {
  return JSON.parse(readFileSync(resolve(fixtures, name), "utf8")) as T;
}

describe("chart series", () => {
  it("draws settled bars by direction and uncertain bars grey", () => {
    const bar = fixture<PriceBar>("price-bar.json");
    const provisional: PriceBar = {
      ...bar,
      interval_start_utc: "2026-10-09T13:31:00Z",
      interval_end_utc: "2026-10-09T13:32:00Z",
      is_final: false,
      quality: "provisional",
    };

    const { candles, volume } = toSeries([bar, provisional]);

    expect(candles[0]).toEqual({
      time: Date.parse(bar.interval_start_utc) / 1000,
      open: 250.1,
      high: 250.5,
      low: 249.9,
      close: 250.25,
    });
    expect(volume[0].color).toBe(COLORS.volumeUp);
    expect(candles[1].color).toBe(COLORS.uncertain);
    expect(volume[1].color).toBe(COLORS.volumeUncertain);
  });
});

describe("response summary", () => {
  it("reports coverage, uncertain bars, sessions and lineage", () => {
    const response = fixture<MarketBarsResponse>(
      "market-bars-response-aggregated.json",
    );

    const summary = summarize(response);

    expect(summary.coverage).toBe("complete");
    expect(summary.intervals).toBe("1/1 5m intervals");
    expect(summary.quality).toBe("1 partial, not final");
    expect(summary.sessions).toBe("regular 1");
    expect(summary.lineage).toContain("fake-market-data");
    expect(summary.lineage).toContain(":agg:5m:");
  });

  it("formats exchange-local times across DST", () => {
    expect(exchangeTime("2026-10-09T13:30:00Z")).toBe("09:30 ET");
    expect(exchangeTime("2026-12-01T14:30:00Z")).toBe("09:30 ET");
  });
});

describe("see-through daily bodies over the pre-market (ADR 0041)", () => {
  it("lowers the opacity of hex and rgb colours, never raises it", () => {
    expect(seeThrough("#3fb68b")).toBe("rgba(63, 182, 139, 0.55)");
    expect(seeThrough("rgb(1, 2, 3)")).toBe("rgba(1, 2, 3, 0.55)");
    expect(seeThrough("rgba(1, 2, 3, 0.2)")).toBe("rgba(1, 2, 3, 0.2)");
    expect(seeThrough("red")).toBe("red");
  });

  it("changes only candles with their own colour, and keeps their wick", () => {
    const plain = { time: 1, open: 1, high: 2, low: 0, close: 1.5 };
    const grey = { ...plain, time: 2, color: COLORS.uncertain };
    expect(seeThroughCandles([plain, grey], false)).toEqual([plain, grey]);
    expect(seeThroughCandles([plain, grey], true)).toEqual([
      plain,
      {
        ...grey,
        color: "rgba(138, 148, 166, 0.55)",
        wickColor: COLORS.uncertain,
      },
    ]);
  });
});

describe("chartPriceBars", () => {
  const REQUEST = {
    instrumentId: "us:aapl:xnas:2026",
    startUtc: "2020-01-01T00:00:00Z",
    endUtc: "2021-01-01T00:00:00Z",
  };
  const answer = {} as MarketBarsResponse;

  it("asks for split-adjusted daily and weekly bars", async () => {
    for (const timeframe of ["1d", "1w"] as const) {
      const bars = vi.fn().mockResolvedValue(answer);
      await chartPriceBars({ bars }, { ...REQUEST, timeframe });
      expect(bars).toHaveBeenCalledWith({
        ...REQUEST,
        timeframe,
        adjustment: "split_adjusted",
      });
    }
  });

  it("leaves intraday bars as traded", async () => {
    const bars = vi.fn().mockResolvedValue(answer);
    await chartPriceBars({ bars }, { ...REQUEST, timeframe: "5m" });
    expect(bars).toHaveBeenCalledWith({ ...REQUEST, timeframe: "5m" });
  });

  it("uses prices as traded when the source has no adjusted ones", async () => {
    const bars = vi
      .fn()
      .mockRejectedValueOnce(
        new MarketApiError("nope", "invalid_market_data_query", null),
      )
      .mockResolvedValueOnce(answer);
    await expect(
      chartPriceBars({ bars }, { ...REQUEST, timeframe: "1d" }),
    ).resolves.toBe(answer);
    expect(bars).toHaveBeenLastCalledWith({ ...REQUEST, timeframe: "1d" });
  });

  it("does not hide a provider failure behind unadjusted prices", async () => {
    const failure = new MarketApiError("down", "provider_unavailable", null);
    const bars = vi.fn().mockRejectedValue(failure);
    await expect(
      chartPriceBars({ bars }, { ...REQUEST, timeframe: "1d" }),
    ).rejects.toBe(failure);
    expect(bars).toHaveBeenCalledTimes(1);
  });
});
