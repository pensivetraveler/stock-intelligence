import { describe, expect, it } from "vitest";

import {
  DEFAULT_WORKSPACE,
  loadWorkspace,
  parseWorkspace,
  saveWorkspace,
  sessionRange,
  WORKSPACE_STORAGE_KEY,
} from "./workspace-state";

function memoryStorage(initial: Record<string, string> = {}): Storage {
  const values = new Map(Object.entries(initial));
  return {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => [...values.keys()][index] ?? null,
    removeItem: (key) => void values.delete(key),
    setItem: (key, value) => void values.set(key, value),
  };
}

const window = {
  kind: "session_window" as const,
  schema_version: "0.1" as const,
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

describe("workspace persistence", () => {
  it("round-trips a saved layout with independent chart settings", () => {
    const storage = memoryStorage();
    const state = {
      ...DEFAULT_WORKSPACE,
      syncTimeRange: false,
      layoutMode: "4" as const,
      charts: [
        { ...DEFAULT_WORKSPACE.charts[0], timeframe: "60m" as const },
        { ...DEFAULT_WORKSPACE.charts[1], instrumentId: "us:aapl:xnas:2026" },
        DEFAULT_WORKSPACE.charts[2],
        { ...DEFAULT_WORKSPACE.charts[3], sessionScope: "extended" as const },
      ],
    };

    saveWorkspace(storage, state);

    expect(loadWorkspace(storage)).toEqual(state);
  });

  it("keeps the pre-market high switch only when it is on", () => {
    const chart = (premarketHigh: unknown) => ({
      ...DEFAULT_WORKSPACE.charts[0],
      timeframe: "1d",
      premarketHigh,
    });
    const parsed = parseWorkspace({
      ...DEFAULT_WORKSPACE,
      charts: [chart(true), chart(false), chart("yes")],
    });

    expect(parsed?.charts[0].premarketHigh).toBe(true);
    expect(parsed?.charts[1]).not.toHaveProperty("premarketHigh");
    expect(parsed?.charts[2]).not.toHaveProperty("premarketHigh");
  });

  it("fills the third and fourth chart of a two-chart layout from the first", () => {
    const storage = memoryStorage();
    const first = {
      ...DEFAULT_WORKSPACE.charts[0],
      instrumentId: "us:aapl:xnas:2026",
      sessionDate: "2026-10-16",
      timeframe: "60m" as const,
    };
    storage.setItem(
      WORKSPACE_STORAGE_KEY,
      JSON.stringify({
        version: 1,
        syncTimeRange: true,
        charts: [first, DEFAULT_WORKSPACE.charts[1]],
      }),
    );

    const loaded = loadWorkspace(storage);
    const follow = {
      instrumentId: first.instrumentId,
      sessionDate: "2026-10-16",
    };
    expect(loaded.charts).toEqual([
      first,
      DEFAULT_WORKSPACE.charts[1],
      { ...DEFAULT_WORKSPACE.charts[2], ...follow },
      { ...DEFAULT_WORKSPACE.charts[3], ...follow },
    ]);
    expect(loaded.layoutMode).toBeUndefined();
  });

  it.each([
    ["corrupt JSON", "{"],
    ["unknown version", JSON.stringify({ ...DEFAULT_WORKSPACE, version: 2 })],
    [
      "one chart",
      JSON.stringify({
        ...DEFAULT_WORKSPACE,
        charts: [DEFAULT_WORKSPACE.charts[0]],
      }),
    ],
    [
      "unsupported timeframe",
      JSON.stringify({
        ...DEFAULT_WORKSPACE,
        charts: [
          { ...DEFAULT_WORKSPACE.charts[0], timeframe: "2d" },
          DEFAULT_WORKSPACE.charts[1],
        ],
      }),
    ],
  ])("falls back to the default layout for %s", (_name, raw) => {
    expect(
      loadWorkspace(memoryStorage({ [WORKSPACE_STORAGE_KEY]: raw })),
    ).toEqual(DEFAULT_WORKSPACE);
  });

  it("survives unavailable storage", () => {
    const broken = memoryStorage();
    broken.getItem = () => {
      throw new Error("denied");
    };
    broken.setItem = () => {
      throw new Error("quota");
    };

    expect(loadWorkspace(broken)).toEqual(DEFAULT_WORKSPACE);
    expect(() => saveWorkspace(broken, DEFAULT_WORKSPACE)).not.toThrow();
    expect(parseWorkspace(null)).toBeNull();
  });
});

describe("session range", () => {
  it("uses the calendar's regular or extended bounds", () => {
    expect(sessionRange(window, "regular")).toEqual({
      startUtc: "2026-10-09T13:30:00Z",
      endUtc: "2026-10-09T20:00:00Z",
    });
    expect(sessionRange(window, "extended")).toEqual({
      startUtc: "2026-10-09T08:00:00Z",
      endUtc: "2026-10-10T00:00:00Z",
    });
  });

  it("has no range on a closed day", () => {
    expect(
      sessionRange(
        {
          ...window,
          is_trading_day: false,
          pre_open_utc: null,
          regular_open_utc: null,
          regular_close_utc: null,
          post_close_utc: null,
        },
        "regular",
      ),
    ).toBeNull();
  });
});
