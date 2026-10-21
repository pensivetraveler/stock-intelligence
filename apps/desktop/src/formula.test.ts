import { describe, expect, it } from "vitest";

import {
  evaluateFormula,
  FormulaError,
  formulaLookback,
  parseFormula,
} from "./formula";
import {
  ema,
  INDICATOR_BY_ID,
  parseIndicators,
  renderIndicators,
  sma,
  warmupBars,
  type OhlcvBar,
} from "./indicators";

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
const run = (text: string, data = bars) =>
  evaluateFormula(parseFormula(text), data, { timeZone: "America/New_York" });

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
    ["close; 1", 'unexpected ";"'],
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
