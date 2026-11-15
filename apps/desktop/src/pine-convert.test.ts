import { describe, expect, it } from "vitest";

import { parseIndicators, renderIndicators, type OhlcvBar } from "./indicators";
import { PINE_EXAMPLE, PINE_STRATEGY_EXAMPLE } from "./pine";
import {
  compareConversion,
  convertInstance,
  convertPine,
  CONVERTER_VERSION,
  pineParamsFor,
  revertInstance,
  sampleBars,
} from "./pine-convert";
import { sha256 } from "./sha256";

const ZONE = { timeZone: "America/New_York" };
const chartBars: OhlcvBar[] = Array.from({ length: 300 }, (_, i) => {
  const close = 20 + 2 * Math.sin(i / 9) + 0.5 * Math.cos(i / 2.1);
  return {
    time: 1_761_000_000 + i * 300,
    open: close - 0.1,
    high: close + 0.25,
    low: close - 0.3,
    close,
    volume: 1_000 + ((i * 17) % 300),
  };
});

const converted = (source: string) => {
  const result = convertPine(source);
  if (!result.ok) throw new Error(`${result.reason} (line ${result.line})`);
  return result;
};
const sameOnBars = (source: string, params = {}) =>
  compareConversion(
    source,
    converted(source),
    params,
    [chartBars, sampleBars()],
    ZONE,
  );

describe("SHA-256", () => {
  it("matches known digests", () => {
    expect(sha256("")).toBe(
      "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    );
    expect(sha256("abc")).toBe(
      "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    );
    expect(sha256("가".repeat(100))).toHaveLength(64);
  });
});

describe("Pine → formula (ADR 0058 §3)", () => {
  it("converts the example and draws the same", () => {
    const result = converted(PINE_EXAMPLE);
    expect(result).toMatchObject({ label: "EMA Cross", pane: "price" });
    expect(result.formula).toBe(
      [
        'fastlength = input(9, "Fast length", minval = 1, maxval = 5000)',
        'slowlength = input(21, "Slow length", minval = 1, maxval = 5000)',
        'src = input(close, "Source")',
        "fast = ema(src, fastlength)",
        "slow = ema(src, slowlength)",
        'plot(fast, "Fast", "#ff9800ff", linewidth = 1)',
        'plot(slow, "Slow", ((fast > slow) ? "#089981ff" : "#f23645ff"), linewidth = 2)',
      ].join("\n"),
    );
    expect(result.keys).toEqual({
      fast_length: "fastlength",
      slow_length: "slowlength",
      source: "src",
    });
    expect(sameOnBars(PINE_EXAMPLE)).toEqual({ same: true, bars: 900 });
    // Changed settings, including another price, still agree.
    expect(
      sameOnBars(PINE_EXAMPLE, { fast_length: 5, source: 4 }),
    ).toMatchObject({ same: true });
  });

  it("converts tuples, colours, inputs inside calls and the other functions", () => {
    const source = `//@version=5
indicator("Mix", overlay=false)
[m, s, h] = ta.macd(close, 12, 26, 9)
[mid, up, lo] = ta.bb(close, input.int(20, "BB length"), 2)
up_col = color.new(color.green, 40)
plot(h, "Hist", h >= 0 ? up_col : color.red, style=plot.style_histogram)
plot(m, "MACD", color=color.blue)
plot((close - lo) / (up - lo), "%B")
plot(ta.stoch(close, high, low, 14) + ta.cci(hlc3, 20) + ta.mfi(hlc3, 14))
plot(ta.crossover(m, s) ? 1 : 0, "Cross")
plot(nz(close[1], open) + math.max(high, low) - ta.highest(10) + ta.tr + ta.cum(volume) / 1e6)
hline(0)
hline(1, "Top")`;
    const result = converted(source);
    expect(result.formula).toContain("m = macd(close, 12, 26, 9)");
    expect(result.formula).toContain('input_1 = input(20, "BB length"');
    expect(result.formula).toContain("highest(high, 10)");
    expect(result.formula).toContain('style = "histogram"');
    expect(sameOnBars(source)).toMatchObject({ same: true });
  });

  it.each([
    [
      "var",
      'indicator("X")\nvar x = 0.0\nx := x + 1\nplot(x)',
      "var is not converted",
    ],
    [
      "reassign",
      'indicator("X")\nx = close\nx := open\nplot(x)',
      ":= (changing a value)",
    ],
    [
      "if",
      'indicator("X")\nx = 0.0\nif close > open\n    x := 1\nplot(x)',
      "if blocks",
    ],
    [
      "loop",
      'indicator("X")\nfor i = 0 to 3\n    log.info("x")\nplot(close)',
      "loops",
    ],
    [
      "function",
      'indicator("X")\nf(x) => x * 2\nplot(f(close))',
      "functions you write",
    ],
    ["strategy", PINE_STRATEGY_EXAMPLE, "strategies stay Pine"],
    [
      "plotshape",
      'indicator("X")\nplot(close)\nplotshape(close > open)',
      "plotshape() is not converted",
    ],
    [
      "branch state",
      'indicator("X")\nplot(close > open ? ta.sma(close, 5) : close)',
      "only some bars",
    ],
    [
      "v6 and",
      '//@version=6\nindicator("X")\nplot(close > open and ta.rsi(close, 14) > 50 ? 1 : 0)',
      "version 6",
    ],
    [
      "text setting",
      'indicator("X")\nt = input.string("EMA", "Type", options=["EMA", "SMA"])\nplot(close)',
      "text, colour and time settings",
    ],
    ["offset", 'indicator("X")\nplot(close, offset=2)', "offset"],
    [
      "unknown",
      'indicator("X")\nplot(ta.alma(close, 9, 0.85, 6))',
      "ta.alma() is not converted",
    ],
    ["no plot", 'indicator("X")\nx = close', "draws nothing"],
    ["bad Pine", 'indicator("X")\nplot(close +', ""],
  ])("keeps %s as Pine, with the reason", (_, source, reason) => {
    const result = convertPine(source);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.reason).toContain(reason);
      expect(result.line).toBeGreaterThanOrEqual(1);
    }
  });

  it("v5 and/or evaluates both sides, so it converts", () => {
    const source =
      '//@version=5\nindicator("X")\nplot(close > open and ta.rsi(close, 14) > 50 ? 1 : 0)';
    expect(sameOnBars(source)).toMatchObject({ same: true });
  });

  it("notices a formula that does not draw the same", () => {
    const source = 'indicator("X")\nplot(ta.sma(close, 10))';
    const wrong = {
      ...converted(source),
      formula: 'plot(sma(close, 11), "Plot 1")',
    };
    const check = compareConversion(source, wrong, {}, [chartBars], ZONE);
    expect(check).toMatchObject({ same: false });
    if (!check.same) expect(check.reason).toContain("plot0 differs at bar");
  });
});

describe("converted indicators", () => {
  const pine = {
    key: "p1",
    type: "pine",
    params: { fast_length: 7, slow_length: 21, source: 1 },
    pine: PINE_EXAMPLE,
    style: { outputs: { plot1: { width: 3 } } },
  };

  it("keep their key, settings, style and where they came from", () => {
    const result = convertInstance(pine, chartBars, ZONE);
    if (!("instance" in result)) throw new Error(result.reason);
    expect(result.instance).toMatchObject({
      key: "p1",
      type: "formula",
      label: "EMA Cross",
      pane: "price",
      // source 1 is open in Pine's list and index 0 in the formula's
      params: { fastlength: 7, slowlength: 21, src: 0 },
      style: { outputs: { plot1: { width: 3 } } },
      converted: {
        pine: PINE_EXAMPLE,
        sha256: sha256(PINE_EXAMPLE),
        converter: CONVERTER_VERSION,
        bars: 900,
      },
    });
    // Saved and read back, and drawn.
    const [saved] = parseIndicators([
      JSON.parse(JSON.stringify(result.instance)),
    ]);
    expect(saved.converted).toEqual(result.instance.converted);
    expect(renderIndicators(chartBars, [saved])[0].lines).toHaveLength(2);
    // And back to the script, with its settings.
    expect(revertInstance(saved, pineParamsFor(saved))).toEqual(pine);
  });

  it("stay Pine when the script cannot be converted", () => {
    const result = convertInstance(
      { ...pine, pine: 'indicator("X")\nvar x = 0.0\nplot(x)' },
      chartBars,
      ZONE,
    );
    expect(result).toEqual({ reason: "var is not converted yet (line 2)" });
  });

  it("stay Pine when the formula would not hold the script's settings", () => {
    const failing = convertInstance(
      {
        ...pine,
        params: {},
        pine: 'indicator("X")\nn = input.int(600, "N", maxval=700)\nplot(ta.sma(close, n))',
      },
      chartBars,
      ZONE,
    );
    expect(failing).toMatchObject({
      reason: expect.stringContaining("not be valid"),
    });
  });

  it("drop an origin that is not sound", () => {
    const [saved] = parseIndicators([
      {
        key: "f",
        type: "formula",
        formula: "close",
        label: "F",
        converted: { pine: "x", sha256: "nothex", converter: "v", bars: 1 },
      },
    ]);
    expect(saved.converted).toBeUndefined();
  });
});
