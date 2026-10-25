import { describe, expect, it } from "vitest";

import { ema, rsi, sma, type OhlcvBar } from "./indicators";
import {
  compilePine,
  PINE_COLORS,
  PINE_EXAMPLE,
  pineDefinition,
  PineError,
  runPine,
} from "./pine";

const closes = (data: OhlcvBar[]) => data.map((bar) => bar.close);

function bars(count: number): OhlcvBar[] {
  return Array.from({ length: count }, (_, i) => {
    const close = 100 + 10 * Math.sin(i / 5) + i * 0.1;
    return {
      time: 1_700_000_000 + i * 60,
      open: close - 0.5,
      high: close + 1,
      low: close - 1,
      close,
      volume: 1000 + i,
    };
  });
}

const run = (source: string, data = bars(60), params = {}) =>
  runPine(compilePine(source), data, params).values;

const round = (series: (number | null)[]) =>
  series.map((value) => (value === null ? null : Number(value.toFixed(9))));

describe("Pine Script", () => {
  it("reads the example's title, inputs and plots", () => {
    const program = compilePine(PINE_EXAMPLE);
    expect(program).toMatchObject({ title: "EMA Cross", overlay: true });
    expect(program.inputs.map((input) => [input.key, input.default])).toEqual([
      ["fast_length", 9],
      ["slow_length", 21],
      ["source", 0],
    ]);
    expect(
      program.plots.map((plot) => [plot.key, plot.title, plot.width]),
    ).toEqual([
      ["plot0", "Fast", 1],
      ["plot1", "Slow", 2],
    ]);
  });

  it("computes ta.ema, ta.sma and ta.rsi like the built-in indicators", () => {
    const data = bars(80);
    const values = run(
      `indicator("T")
plot(ta.ema(close, 9))
plot(ta.sma(close, 14))
plot(ta.rsi(close, 14))`,
      data,
    );
    expect(round(values.plot0)).toEqual(round(ema(closes(data), 9)));
    expect(round(values.plot1)).toEqual(round(sma(closes(data), 14)));
    expect(round(values.plot2)).toEqual(round(rsi(closes(data), 14)));
  });

  it("uses inputs, sources and per-bar colours", () => {
    const data = bars(40);
    const definition = pineDefinition(PINE_EXAMPLE);
    expect(definition.pane).toBe("price");
    // Source 1 = open.
    const values = definition.compute(data, {
      fast_length: 3,
      slow_length: 5,
      source: 1,
    });
    const opens = data.map((bar) => bar.open);
    expect(round(values.plot0)).toEqual(round(ema(opens, 3)));
    const colours = PINE_COLORS.get(values)!;
    const i = 20;
    expect(colours.plot1[i]).toBe(
      values.plot0[i]! > values.plot1[i]! ? "#089981" : "#f23645",
    );
  });

  it("keeps var across bars, reads history and runs if, for and functions", () => {
    const values = run(
      `indicator("State")
var count = 0
count += 1
previous = close[1]
float total = 0.0
for i = 0 to 2
    total := total + close[i]
twice(x) => x * 2
direction = if close > previous
    1
else
    -1
up = close > previous ? 1 : 0
plot(count)
plot(previous)
plot(total / 3)
plot(twice(count))
plot(up)
plot(direction)`,
      bars(10),
    );
    expect(values.plot0).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
    const data = bars(10);
    expect(values.plot1[0]).toBeNull();
    expect(values.plot1[3]).toBeCloseTo(data[2].close);
    expect(values.plot2[1]).toBeNull(); // close[2] does not exist yet
    expect(values.plot2[5]).toBeCloseTo(
      (data[5].close + data[4].close + data[3].close) / 3,
    );
    expect(values.plot3[4]).toBe(10);
    const up = data[4].close > data[3].close;
    expect(values.plot4[4]).toBe(up ? 1 : 0);
    expect(values.plot5[4]).toBe(up ? 1 : -1);
  });

  it("returns tuples from ta.macd and ta.bb", () => {
    const values = run(
      `indicator("M")
[m, s, h] = ta.macd(close, 12, 26, 9)
[mid, upper, lower] = ta.bb(close, 20, 2)
plot(h, style=plot.style_histogram)
plot(upper - lower)`,
      bars(80),
    );
    const definition = pineDefinition(`indicator("M")
[m, s, h] = ta.macd(close, 12, 26, 9)
plot(h, style=plot.style_histogram)`);
    expect(definition.outputs[0].kind).toBe("histogram");
    expect(values.plot0[40]).not.toBeNull();
    expect(values.plot1[19]).toBeGreaterThan(0);
    expect(values.plot1[18]).toBeNull();
  });

  it("refuses unsupported functions, unknown names and runaway loops with a line", () => {
    expect(() =>
      compilePine(
        `indicator("S")\nx = request.financial("AAPL", "EPS", "FQ")\nplot(x)`,
      ),
    ).toThrow(/request.financial/);
    expect(() =>
      compilePine(`indicator("S")\nplotcandle(open, high, low, close)`),
    ).toThrow(/plotcandle/);
    expect(() => compilePine("plot(close)")).toThrow(/indicator/);
    try {
      run(`indicator("X")\nplot(foo)`);
      throw new Error("no error");
    } catch (error) {
      expect(error).toBeInstanceOf(PineError);
      expect((error as PineError).line).toBe(2);
    }
    expect(() =>
      run(
        `indicator("L")\nvar x = 0\nwhile true\n    x += 1\nplot(x)`,
        bars(2),
      ),
    ).toThrow(/loops ran more than/);
    try {
      compilePine(`indicator("E")\nx = (close +\nplot(x`);
      throw new Error("no error");
    } catch (error) {
      expect(error).toBeInstanceOf(PineError);
    }
  });

  it("never reads a later bar", () => {
    const data = bars(30);
    const full = run(
      `indicator("F")\nplot(ta.ema(close, 5) + ta.highest(high, 4))`,
      data,
    );
    const cut = run(
      `indicator("F")\nplot(ta.ema(close, 5) + ta.highest(high, 4))`,
      data.slice(0, 20),
    );
    expect(cut.plot0).toEqual(full.plot0.slice(0, 20));
  });
});
