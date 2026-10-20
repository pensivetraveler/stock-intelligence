import { describe, expect, it } from "vitest";

import {
  customTimeframe,
  DEFAULT_FAVORITES,
  FAVORITES_STORAGE_KEY,
  loadFavorites,
  normalizeFavorites,
  sessionsForCandles,
  timeframeLabel,
  timeframeMinutes,
} from "./timeframes";

describe("timeframes", () => {
  it("reads whole-minute timeframes up to a full extended session", () => {
    expect(timeframeMinutes("1m")).toBe(1);
    expect(timeframeMinutes("240m")).toBe(240);
    expect(timeframeMinutes("960m")).toBe(960);
    expect(timeframeMinutes("961m")).toBeNull();
    expect(timeframeMinutes("0m")).toBeNull();
    expect(timeframeMinutes("1h")).toBeNull();
    expect(timeframeMinutes("05m")).toBeNull();
  });

  it("shows hours for whole-hour timeframes", () => {
    expect(timeframeLabel("45m")).toBe("45m");
    expect(timeframeLabel("60m")).toBe("1h");
    expect(timeframeLabel("240m")).toBe("4h");
    expect(timeframeLabel("90m")).toBe("1h 30m");
  });

  it("builds custom timeframes from minutes or hours", () => {
    expect(customTimeframe(7, "minutes")).toBe("7m");
    expect(customTimeframe(3, "hours")).toBe("180m");
    expect(customTimeframe(17, "hours")).toBeNull();
    expect(customTimeframe(0, "minutes")).toBeNull();
    expect(customTimeframe(2.5, "minutes")).toBeNull();
  });

  it("keeps favorites valid, unique and ordered", () => {
    expect(normalizeFavorites(["60m", "5m", "5m", "bad", 3, "1m"])).toEqual([
      "1m",
      "5m",
      "60m",
    ]);
    expect(normalizeFavorites(null)).toEqual(DEFAULT_FAVORITES);
    const storage = new Map<string, string>([
      [FAVORITES_STORAGE_KEY, JSON.stringify(["240m", "3m"])],
    ]);
    expect(
      loadFavorites({
        getItem: (key: string) => storage.get(key) ?? null,
      } as Storage),
    ).toEqual(["3m", "240m"]);
  });

  it("loads enough sessions for about 1,200 candles, at most 60", () => {
    expect(sessionsForCandles("1m", "regular")).toBe(4); // 390 a day
    expect(sessionsForCandles("1m", "extended")).toBe(2); // 960 a day
    expect(sessionsForCandles("5m", "regular")).toBe(16); // 78 a day
    expect(sessionsForCandles("60m", "regular")).toBe(60); // 7 a day: capped
  });
});
