import type { CustomIndicator } from "@siw/contracts/types";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import {
  latestCustom,
  registerCustom,
  signature,
  storeResult,
} from "./custom-indicators";
import {
  INDICATOR_BY_ID,
  parseIndicators,
  renderIndicators,
  warmupBars,
  type OhlcvBar,
} from "./indicators";

const saved = JSON.parse(
  readFileSync(
    resolve(
      import.meta.dirname,
      "../../../tests/fixtures/contracts/valid/custom-indicator.json",
    ),
    "utf8",
  ),
) as CustomIndicator;

const bars: OhlcvBar[] = [1, 2, 3].map((i) => ({
  time: 1_760_000_000 + i * 60,
  open: 10 + i,
  high: 11 + i,
  low: 9 + i,
  close: 10 + i,
  volume: 100,
}));

describe("Python indicators on the chart", () => {
  it("keeps saved instances whose definition has not loaded yet", () => {
    const type = "py:ind-later@3";
    expect(
      parseIndicators([{ key: "a", type, params: { length: 9, bad: "x" } }]),
    ).toEqual([{ key: "a", type, params: { length: 9 } }]);
    expect(parseIndicators([{ key: "b", type: "unknown" }])).toEqual([]);
  });

  it("draws nothing until the API's values for these exact bars arrive", () => {
    registerCustom([saved]);
    const definition = INDICATOR_BY_ID.get(saved.type);
    expect(definition?.category).toBe("custom");
    expect(definition?.outputs.map((output) => output.key)).toEqual(
      saved.outputs,
    );
    const params = { length: 14, high: 70 };
    const instance = { key: "k", type: saved.type, params };
    expect(renderIndicators(bars, [instance])[0].lines[0].data).toEqual([]);

    storeResult(signature(saved.type, params, bars), {
      rsi: [null, 50, 60],
      over: [null, 0, 0],
    });
    const [rendered] = renderIndicators(bars, [instance]);
    expect(rendered.lines[0].data.map((point) => point.value)).toEqual([
      50, 60,
    ]);
    // Different bars (e.g. a new candle) are a different computation.
    const more = [...bars, { ...bars[2], time: bars[2].time + 60 }];
    expect(renderIndicators(more, [instance])[0].lines[0].data).toEqual([]);
    // Whole-number settings count as look-backs for warm-up.
    expect(warmupBars([instance])).toBe(210);
  });

  it("lists the latest version of each indicator", () => {
    registerCustom([
      saved,
      { ...saved, version: 2, type: `py:${saved.indicator_id}@2` },
    ]);
    expect(latestCustom().map((item) => item.version)).toEqual([2]);
  });
});
