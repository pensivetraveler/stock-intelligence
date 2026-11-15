import { describe, expect, it } from "vitest";

import {
  evaluateFormula,
  formulaDefinition,
  FormulaError,
  formulaLookback,
  formulaPosition,
  formulaWidths,
  parseFormula,
} from "./formula";
import {
  ema,
  INDICATOR_BY_ID,
  parseIndicators,
  renderIndicators,
  shift,
  sma,
  warmupBars,
  type OhlcvBar,
  type Series,
} from "./indicators";
import { compilePine, runPine } from "./pine";

const bars: OhlcvBar[] = Array.from({ length: 120 }, (_, i) => {
  const close = 100 + 6 * Math.sin(i / 6) + i * 0.1;
  return {
    time: 1_760_000_000 + i * 60,
    open: close - 0.4,
    high: close + 1,
    low: close - 1.2,
    close,
    volume: 1_000 + (i % 7) * 50,
  };
});
const closes = bars.map((bar) => bar.close);
const ZONE = { timeZone: "America/New_York" };
const run = (text: string, data = bars) =>
  evaluateFormula(parseFormula(text), data, {}, ZONE).values.value;

describe("formula language", () => {
  it("matches the built-in indicators it names", () => {
    expect(run("sma(close, 20)")).toEqual(sma(closes, 20));
    expect(run("ema(close, 10) - sma(close, 30)")).toEqual(
      ema(closes, 10).map((value, i) => {
        const slow = sma(closes, 30)[i];
        return value === null || slow === null ? null : value - slow;
      }),
    );
    const rsi = INDICATOR_BY_ID.get("rsi")!.compute(bars, { length: 14 }).value;
    expect(run("rsi(close, 14)")).toEqual(rsi);
    const atr = INDICATOR_BY_ID.get("atr")!.compute(bars, { length: 14 }).value;
    expect(run("ATR(14)")).toEqual(atr);
  });

  it("does arithmetic with precedence and keeps missing values missing", () => {
    expect(run("1 + 2 * 3 - -4 / 2")[0]).toBe(9);
    expect(run("(1 + 2) * 3")[0]).toBe(9);
    expect(run("close / 0")[0]).toBeNull();
    expect(run("log(0 - 1)")[0]).toBeNull();
    expect(run("shift(close, 2)").slice(0, 3)).toEqual([null, null, closes[0]]);
    expect(run("change(close)")[1]).toBeCloseTo(closes[1] - closes[0], 12);
    expect(run("roc(close, 1)")[1]).toBeCloseTo(
      ((closes[1] - closes[0]) / closes[0]) * 100,
      12,
    );
    expect(run("sum(volume, 3)")[2]).toBe(
      bars[0].volume + bars[1].volume + bars[2].volume,
    );
  });

  it("never changes earlier values when later bars arrive", () => {
    const text =
      "(ema(hlc3, 10) - sma(close, 30)) / atr(14) + rsi(change(close), 5) + vwap() / obv()";
    const early = run(text, bars.slice(0, 70));
    const full = run(text, bars);
    expect(full.slice(0, 70)).toEqual(early);
  });

  it.each([
    ["", "write a formula"],
    ["close +", "ends too early"],
    ["sma(close)", "takes 2 arguments"],
    ["sma(close, 2.5)", "whole number"],
    ["sma(close, length)", "unknown name"],
    ["shift(close, -1)", "whole number"],
    ["eval(close)", 'unknown function "eval"'],
    ["close; 1", "write plot(...) for each line"],
    ["close)", 'unexpected ")"'],
    ["(close", 'expected ")"'],
  ])("explains what is wrong with %j", (text, message) => {
    expect(() => parseFormula(text)).toThrow(FormulaError);
    expect(() => parseFormula(text)).toThrow(message);
  });

  it("reports where the problem is", () => {
    try {
      parseFormula("close + foo");
    } catch (error) {
      expect((error as FormulaError).position).toBe(8);
    }
  });

  it("knows how many earlier bars it needs", () => {
    expect(formulaLookback(parseFormula("close"))).toBe(0);
    expect(formulaLookback(parseFormula("sma(ema(close, 10), 20)"))).toBe(30);
    expect(formulaLookback(parseFormula("atr(14) / sma(close, 50)"))).toBe(50);
  });
});

describe("formula indicators on a chart", () => {
  it("are kept with their formula and drawn like other indicators", () => {
    const [instance] = parseIndicators([
      {
        key: "f1",
        type: "formula",
        formula: "sma(close, 5) - sma(close, 20)",
        label: "  Gap  ",
        pane: "separate",
        params: {},
      },
    ]);
    expect(instance).toMatchObject({
      type: "formula",
      label: "Gap",
      pane: "separate",
    });
    const [rendered] = renderIndicators(bars, [instance]);
    expect(rendered.definition.short).toBe("Gap");
    expect(rendered.lines[0].data).toHaveLength(bars.length - 19);
    expect(warmupBars([instance])).toBe(60);
  });

  it("draw nothing for a formula that does not parse", () => {
    const [instance] = parseIndicators([
      { key: "f2", type: "formula", formula: "sma(", label: "Broken" },
    ]);
    expect(renderIndicators(bars, [instance])[0].lines[0].data).toEqual([]);
  });
});

// Several days of 15-minute bars, with a flat stretch (equal highs and lows) and a gap.
const longBars: OhlcvBar[] = Array.from({ length: 400 }, (_, i) => {
  const flat = i >= 150 && i < 165;
  const close = flat
    ? 104
    : 100 + 8 * Math.sin(i / 11) + 3 * Math.cos(i / 4) + (i > 300 ? 6 : 0);
  return {
    time: 1_760_000_000 + i * 900,
    open: flat ? 104 : close - 0.3 * Math.sin(i),
    high: flat ? 104 : close + 0.6 + 0.4 * Math.abs(Math.sin(i / 3)),
    low: flat ? 104 : close - 0.7 - 0.3 * Math.abs(Math.cos(i / 5)),
    close,
    volume: 900 + ((i * 37) % 400),
  };
});

const all = (text: string, data = longBars, params = {}) =>
  evaluateFormula(parseFormula(text), data, params, ZONE);

describe("formula statements (ADR 0058 §2)", () => {
  it("names values, declares settings and draws several lines", () => {
    const text = `// MACD with settings
fast = input(12, "Fast length")
slow = input(26, "Slow length", minval = 2)
line = ema(close, fast) - ema(close, slow)
plot(line, "MACD", line > 0 ? "#26a69a" : "#ef5350", 2)
plot(ema(line, 9), title = "Signal", color = "#ff9800")
plot(line - ema(line, 9), "Hist", style = "histogram")
hline(0); hline(-1.5, "Low")`;
    const program = parseFormula(text);
    expect(program.inputs).toMatchObject([
      {
        key: "fast",
        label: "Fast length",
        default: 12,
        min: 1,
        max: 500,
        length: true,
      },
      {
        key: "slow",
        label: "Slow length",
        default: 26,
        min: 2,
        max: 500,
        length: true,
      },
    ]);
    expect(
      program.plots.map((plot) => [
        plot.key,
        plot.title,
        plot.width,
        plot.kind,
      ]),
    ).toEqual([
      ["plot0", "MACD", 2, "line"],
      ["plot1", "Signal", 1, "line"],
      ["plot2", "Hist", 1, "histogram"],
    ]);
    expect(program.levels).toEqual([0, -1.5]);
    const { values, colors } = all(text);
    const closes = longBars.map((bar) => bar.close);
    const line = ema(closes, 12).map((x, i) => {
      const y = ema(closes, 26)[i];
      return x === null || y === null ? null : x - y;
    });
    expect(values.plot0).toEqual(line);
    expect(Object.keys(colors)).toEqual(["plot0"]);
    expect(colors.plot0[399]).toBe(line[399]! > 0 ? "#26a69a" : "#ef5350");
    // A changed setting changes the lines; the warm-up follows it.
    expect(all(text, longBars, { fast: 5 }).values.plot0).not.toEqual(line);
    expect(formulaLookback(program)).toBe(26 + 9);
    expect(formulaLookback(program, { slow: 40 })).toBe(40 + 9);
  });

  it("reads true/false and price settings", () => {
    const text = `src = input(hl2, "Source")
smooth = input(true, "Smooth")
plot(smooth ? sma(src, 3) : src)`;
    const program = parseFormula(text);
    expect(
      program.inputs.map((input) => [input.key, input.kind, input.default]),
    ).toEqual([
      ["src", "source", 5],
      ["smooth", "bool", 1],
    ]);
    const hl2 = longBars.map((bar) => (bar.high + bar.low) / 2);
    expect(all(text).values.plot0).toEqual(sma(hl2, 3));
    expect(all(text, longBars, { smooth: 0, src: 3 }).values.plot0).toEqual(
      longBars.map((bar) => bar.close),
    );
  });

  it("compares, combines and picks, with missing values comparing false", () => {
    const { values } = all(`up = close > close[1] and not (close < open)
plot(up ? 1 : 0)
plot(sma(close, 3) > 0 ? 1 : 0)
plot(close[1] == close[1] ? 1 : 0)
plot(7 % 3)`);
    expect(values.plot0[0]).toBe(0); // close[1] is missing on the first bar
    expect(values.plot1.slice(0, 3)).toEqual([0, 0, 1]);
    expect(values.plot2.slice(0, 2)).toEqual([0, 1]);
    expect(values.plot3[0]).toBe(1);
    const closes = longBars.map((bar) => bar.close);
    expect(all("close[3]").values.value).toEqual(shift(closes, 3));
  });

  it.each([
    ["plot(close > 1)", "plot's value must be a number"],
    ["close + (close > 1)", "must be a number"],
    ["close > 1 ? 1 : true", "same kind of value"],
    ["close and true", "must be true/false"],
    ['plot(close, "C", 3)', "must be a colour"],
    ['plot(close, "C", "red")', "#rrggbb"],
    ['plot(close, "C", linewidth = 9)', "linewidth"],
    ['plot(close, "C", style = "area")', "histogram"],
    ["n = input(2.5)\nplot(sma(close, n))", "whole number"],
    ["n = input(10, maxval = 600)\nplot(sma(close, close))", "number setting"],
    ["sma = 3\nplot(close)", "already a word"],
    ["a = 1\na = 2\nplot(a)", "already set above"],
    ["plot(b)\nb = 1", 'unknown name "b"'],
    ["close\nplot(close)", "write plot(...) for each line"],
    ["hline(close)", "must be a number"],
    ["x = 1", "add the value to draw"],
    ["close[-1]", "whole number"],
    ["close[n]", "whole number of bars"],
    ['plot(close, "C", "#fff', "not closed"],
  ])("explains what is wrong with %j", (text, message) => {
    expect(() => parseFormula(text)).toThrow(message);
  });

  it("reports the line and column", () => {
    const text = "a = close\nplot(a + foo)";
    try {
      parseFormula(text);
      throw new Error("no error");
    } catch (error) {
      expect(formulaPosition(text, (error as FormulaError).position)).toEqual({
        line: 2,
        column: 10,
      });
    }
  });

  it("never changes earlier values when later bars arrive, for every function", () => {
    const text = `a = macd(close, 5, 13, 4) + macd_signal(close, 5, 13, 4) + macd_hist(hlc3, 5, 13, 4)
b = bb_upper(close, 20, 2) - bb_lower(close, 20, 2) + stoch(close, high, low, 14) + cci(hlc3, 20)
c = mfi(hlc3, 14) + cum(volume) / 1000 + tr() + nz(close[2], 0) + (na(close[5]) ? 1 : 0)
d = crossover(ema(close, 5), sma(close, 20)) or crossunder(close, open) ? 1 : 0
plot(a); plot(b); plot(c); plot(d); plot(vwap()); plot(obv() % 7)`;
    const early = all(text, longBars.slice(0, 220)).values;
    const full = all(text).values;
    for (const key of Object.keys(full)) {
      expect(full[key].slice(0, 220)).toEqual(early[key]);
    }
  });

  it("still reads a single formula as before", () => {
    const definition = formulaDefinition(
      "ema(close, 10) - sma(close, 30)",
      "Gap",
      "price",
    );
    expect(definition.outputs).toEqual([
      { key: "value", label: "Gap", color: "#e6c547" },
    ]);
    expect(definition.inputs).toEqual([]);
  });

  it("gives a chart its settings, lines, levels and per-bar colours", () => {
    const formula = `n = input(14, "Length")
r = rsi(close, n)
plot(r, "RSI", r > 70 ? "#ef5350" : na)
hline(70); hline(30)`;
    const [instance] = parseIndicators([
      {
        key: "f3",
        type: "formula",
        formula,
        label: "RSI",
        pane: "separate",
        params: { n: 900 },
      },
    ]);
    expect(instance.params).toEqual({ n: 14 }); // out of range: back to the default
    const [rendered] = renderIndicators(longBars, [instance]);
    expect(rendered.definition.inputs[0]).toMatchObject({
      key: "n",
      min: 1,
      max: 500,
    });
    expect(rendered.definition.levels).toEqual([70, 30]);
    const colored = rendered.lines[0].data.filter(
      (point) => point.color === "#ef5350",
    );
    expect(colored.length).toBeGreaterThan(0);
    expect(
      rendered.lines[0].data.some(
        (point) => point.color === "rgba(0, 0, 0, 0)",
      ),
    ).toBe(true);
    expect(
      formulaWidths('plot(close, "C", linewidth = 3)\nplot(open)'),
    ).toEqual({ plot0: 3 });
  });
});

/** Same values (within 1e-9 of the size) and the same missing bars. */
function expectSame(actual: Series, expected: Series, what: string) {
  expect(actual.length, what).toBe(expected.length);
  actual.forEach((value, i) => {
    const other = expected[i];
    if (value === null || other === null) {
      expect([what, i, value], what).toEqual([what, i, other]);
    } else {
      expect(
        Math.abs(value - other),
        `${what} at bar ${i}`,
      ).toBeLessThanOrEqual(1e-9 * Math.max(1, Math.abs(other)));
    }
  });
}

describe("formula functions match the Pine Script interpreter", () => {
  // Each pair: the Pine expression and the formula written for it.
  const PAIRS: [string, string][] = [
    ["ta.sma(close, 20)", "sma(close, 20)"],
    ["ta.ema(close, 9)", "ema(close, 9)"],
    ["ta.wma(hl2, 10)", "wma(hl2, 10)"],
    ["ta.rma(close, 14)", "rma(close, 14)"],
    ["ta.stdev(close, 20)", "stdev(close, 20)"],
    ["ta.highest(high, 10)", "highest(high, 10)"],
    ["ta.lowest(low, 10)", "lowest(low, 10)"],
    ["math.sum(volume, 5)", "sum(volume, 5)"],
    ["ta.change(close)", "change(close)"],
    ["ta.change(close, 3)", "change(close, 3)"],
    ["ta.roc(close, 9)", "roc(close, 9)"],
    ["ta.rsi(close, 14)", "rsi(close, 14)"],
    ["ta.atr(14)", "atr(14)"],
    ["ta.tr", "tr()"],
    ["ta.cum(volume)", "cum(volume)"],
    ["ta.vwap(hlc3)", "vwap()"],
    ["ta.obv", "obv()"],
    ["ta.stoch(close, high, low, 14)", "stoch(close, high, low, 14)"],
    ["ta.cci(hlc3, 20)", "cci(hlc3, 20)"],
    ["ta.mfi(hlc3, 14)", "mfi(hlc3, 14)"],
    ["close[3]", "close[3]"],
    ["nz(close[2], open)", "nz(close[2], open)"],
    ["na(close[2]) ? 1 : 0", "na(close[2]) ? 1 : 0"],
    ["math.abs(close - open)", "abs(close - open)"],
    ["math.sqrt(volume)", "sqrt(volume)"],
    ["math.log(close)", "log(close)"],
    ["math.max(open, close)", "max(open, close)"],
    ["math.min(open, close)", "min(open, close)"],
    ["close % 3", "close % 3"],
    [
      "ta.crossover(ta.ema(close, 5), ta.sma(close, 20)) ? 1 : 0",
      "crossover(ema(close, 5), sma(close, 20)) ? 1 : 0",
    ],
    [
      "ta.crossunder(close, ta.ema(close, 8)) ? 1 : 0",
      "crossunder(close, ema(close, 8)) ? 1 : 0",
    ],
    [
      "close > open and not (high - low > 1) or close == open ? 1 : 0",
      "close > open and not (high - low > 1) or close == open ? 1 : 0",
    ],
    ["close[1] > close ? close : na", "close[1] > close ? close : na"],
  ];

  it.each(PAIRS)("%s", (pine, formula) => {
    const pineValues = runPine(
      compilePine(`//@version=5\nindicator("P")\nplot(${pine})`),
      longBars,
      {},
      { timeZone: ZONE.timeZone },
    ).values.plot0;
    expectSame(all(formula).values.value, pineValues, pine);
  });

  it("ta.macd and ta.bb", () => {
    const pine = runPine(
      compilePine(`//@version=5
indicator("P")
[m, s, h] = ta.macd(close, 12, 26, 9)
[mid, up, lo] = ta.bb(close, 20, 2)
plot(m)
plot(s)
plot(h)
plot(up)
plot(lo)`),
      longBars,
      {},
      { timeZone: ZONE.timeZone },
    ).values;
    const ours = all(`plot(macd(close, 12, 26, 9))
plot(macd_signal(close, 12, 26, 9))
plot(macd_hist(close, 12, 26, 9))
plot(bb_upper(close, 20, 2))
plot(bb_lower(close, 20, 2))`).values;
    for (const key of Object.keys(pine)) expectSame(ours[key], pine[key], key);
  });
});
