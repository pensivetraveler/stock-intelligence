import { describe, expect, it } from "vitest";

import {
  defaultParams,
  ema,
  INDICATOR_BY_ID,
  INDICATORS,
  parseIndicators,
  renderIndicators,
  rma,
  sma,
  stdev,
  warmupBars,
  wma,
  type OhlcvBar,
} from "./indicators";

function bar(
  close: number,
  extra: Partial<OhlcvBar> = {},
  index = 0,
): OhlcvBar {
  return {
    time: 1_760_000_000 + index * 60,
    open: close,
    high: close,
    low: close,
    close,
    volume: 100,
    ...extra,
  };
}

/** A deterministic, wavy price path with real ranges and volume. */
function path(count: number): OhlcvBar[] {
  return Array.from({ length: count }, (_, i) => {
    const close = 100 + 5 * Math.sin(i / 7) + i * 0.05;
    const open = close - Math.cos(i / 3);
    return {
      time: 1_760_000_000 + i * 60,
      open,
      high: Math.max(open, close) + 0.6 + (i % 4) * 0.1,
      low: Math.min(open, close) - 0.5 - (i % 3) * 0.1,
      close,
      volume: 1_000 + ((i * 37) % 500),
    };
  });
}

const run = (id: string, bars: OhlcvBar[], params = {}) => {
  const definition = INDICATOR_BY_ID.get(id);
  if (!definition) throw new Error(id);
  return definition.compute(bars, { ...defaultParams(definition), ...params });
};

describe("building blocks", () => {
  it("averages by their textbook definitions", () => {
    expect(sma([1, 2, 3, 4, 5], 3)).toEqual([null, null, 2, 3, 4]);
    // EMA seeded with the SMA of the first 3 values, alpha = 2 / 4.
    expect(ema([1, 2, 3, 4, 5], 3)).toEqual([null, null, 2, 3, 4]);
    // Wilder: seed (1+2)/2, then alpha = 1/2.
    expect(rma([1, 2, 3, 4], 2)).toEqual([null, 1.5, 2.25, 3.125]);
    expect(wma([1, 2, 3], 3)[2]).toBeCloseTo(14 / 6);
    expect(stdev([2, 4, 4, 4, 5, 5, 7, 9], 8)[7]).toBeCloseTo(2);
  });
});

describe("indicators", () => {
  it("RSI is 100 on only gains and 0 on only losses", () => {
    const rising = Array.from({ length: 20 }, (_, i) => bar(10 + i, {}, i));
    const falling = Array.from({ length: 20 }, (_, i) => bar(30 - i, {}, i));
    expect(run("rsi", rising).value.at(-1)).toBe(100);
    expect(run("rsi", falling).value.at(-1)).toBe(0);
    expect(
      run("rsi", rising)
        .value.slice(0, 14)
        .every((v) => v === null),
    ).toBe(true);
  });

  it("Bollinger bands collapse on a flat price", () => {
    const flat = Array.from({ length: 25 }, (_, i) => bar(50, {}, i));
    const result = run("bb", flat);
    expect(result.basis.at(-1)).toBe(50);
    expect(result.upper.at(-1)).toBe(50);
    expect(result.lower.at(-1)).toBe(50);
  });

  it("MACD is zero on a flat price once warmed up", () => {
    const flat = Array.from({ length: 60 }, (_, i) => bar(42, {}, i));
    const result = run("macd", flat);
    expect(result.macd.at(-1)).toBe(0);
    expect(result.signal.at(-1)).toBe(0);
    expect(result.macd[24]).toBeNull();
  });

  it("OBV adds volume on up closes and subtracts on down closes", () => {
    const bars = [10, 11, 10, 10].map((close, i) =>
      bar(close, { volume: (i + 1) * 100 }, i),
    );
    expect(run("obv", bars).value).toEqual([0, 200, -100, -100]);
  });

  it("VWAP weights the typical price by volume and restarts each ET day", () => {
    const bars = [
      bar(10, { volume: 1 }, 0),
      bar(20, { volume: 3 }, 1),
      // Next ET day: the average starts over.
      { ...bar(30, { volume: 2 }), time: 1_760_000_000 + 86_400 },
    ];
    expect(run("vwap", bars).value).toEqual([10, 17.5, 30]);
  });

  it("Stochastic reaches 100 at the top of its range", () => {
    const bars = Array.from({ length: 20 }, (_, i) =>
      bar(10 + i, { high: 10.5 + i, low: 9.5 + i }, i),
    );
    const k = run("stoch", bars).k.at(-1) as number;
    expect(k).toBeCloseTo((100 * (29 - 15.5)) / (29.5 - 15.5));
  });

  it("every indicator returns one finite-or-empty value per bar", () => {
    const bars = path(300);
    for (const definition of INDICATORS) {
      const result = definition.compute(bars, defaultParams(definition));
      for (const output of definition.outputs) {
        const series = result[output.key];
        expect(series, `${definition.id}.${output.key}`).toHaveLength(300);
        expect(
          series.every((value) => value === null || Number.isFinite(value)),
          `${definition.id}.${output.key}`,
        ).toBe(true);
        expect(
          series.some((value) => value !== null),
          `${definition.id}.${output.key} never warms up`,
        ).toBe(true);
      }
    }
  });

  it("never uses later bars: values do not change when history is cut", () => {
    const bars = path(240);
    const cut = 170;
    for (const definition of INDICATORS) {
      const params = defaultParams(definition);
      const full = definition.compute(bars, params);
      const partial = definition.compute(bars.slice(0, cut), params);
      for (const output of definition.outputs) {
        // The Ichimoku lagging span plots today's close in the past by design.
        if (definition.id === "ichimoku" && output.key === "lagging") continue;
        const a = full[output.key].slice(0, cut);
        const b = partial[output.key];
        b.forEach((value, i) => {
          if (value === null) {
            expect(a[i], `${definition.id}.${output.key}[${i}]`).toBeNull();
          } else {
            expect(a[i], `${definition.id}.${output.key}[${i}]`).toBeCloseTo(
              value,
              9,
            );
          }
        });
      }
    }
  });
});

describe("saved indicator settings", () => {
  it("drops unknown types and repairs out-of-range params", () => {
    expect(
      parseIndicators([
        { key: "a", type: "rsi", params: { length: 5 } },
        { key: "b", type: "nope", params: {} },
        {
          key: "c",
          type: "bb",
          params: { length: -3, mult: "2" },
          hidden: true,
        },
        "junk",
      ]),
    ).toEqual([
      { key: "a", type: "rsi", params: { length: 5 } },
      { key: "c", type: "bb", params: { length: 20, mult: 2 }, hidden: true },
    ]);
    expect(parseIndicators(undefined)).toEqual([]);
  });

  it("has a unique id, a Korean name and defaults within range for each entry", () => {
    const ids = new Set(INDICATORS.map((item) => item.id));
    expect(ids.size).toBe(INDICATORS.length);
    for (const definition of INDICATORS) {
      expect(definition.name.ko.length).toBeGreaterThan(0);
      for (const item of definition.inputs) {
        expect(item.default).toBeGreaterThanOrEqual(item.min);
        expect(item.default).toBeLessThanOrEqual(item.max);
      }
    }
  });
});

describe("warm-up", () => {
  const instance = (type: string, params: Record<string, number> = {}) => {
    const definition = INDICATOR_BY_ID.get(type);
    if (!definition) throw new Error(type);
    return {
      key: type,
      type,
      params: { ...defaultParams(definition), ...params },
    };
  };

  it("asks for three times the longest look-back, capped", () => {
    expect(warmupBars([])).toBe(0);
    expect(warmupBars([instance("rsi")])).toBe(42);
    expect(warmupBars([instance("rsi"), instance("macd")])).toBe(78);
    // Ichimoku needs span B's length plus the displacement.
    expect(warmupBars([instance("ichimoku")])).toBe(3 * (9 + 26 + 52 + 26));
    // "Bars per year" is a scale, not a look-back.
    expect(warmupBars([instance("hv")])).toBe(30);
    expect(warmupBars([instance("vwap")])).toBe(0);
    expect(warmupBars([instance("sma", { length: 500 })])).toBe(1_000);
  });

  it("draws only from the first shown bar but keeps warmed-up values", () => {
    const bars = path(40);
    const [rendered] = renderIndicators(
      bars,
      [instance("sma", { length: 5 })],
      30,
    );
    const line = rendered.lines[0];
    expect(line.data[0].time).toBe(bars[30].time);
    expect(line.data).toHaveLength(10);
    // The first drawn value already averages five earlier bars.
    expect(rendered.values.value[30]).toBeCloseTo(
      bars.slice(26, 31).reduce((total, bar) => total + bar.close, 0) / 5,
    );
  });
});
