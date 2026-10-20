import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { INDICATOR_BY_ID, type OhlcvBar, type Series } from "./indicators";

interface ParityFixture {
  time_zone: string;
  bars: OhlcvBar[];
  cases: {
    id: string;
    params: Record<string, number>;
    outputs: Record<string, Series>;
  }[];
}

const fixture = JSON.parse(
  readFileSync(
    resolve(
      import.meta.dirname,
      "../../../tests/fixtures/indicators/parity.json",
    ),
    "utf8",
  ),
) as ParityFixture;

describe("indicator parity with the backtest engine", () => {
  it.each(fixture.cases.map((item) => [item.id, item] as const))(
    "%s reproduces the shared fixture",
    (_id, item) => {
      const definition = INDICATOR_BY_ID.get(item.id);
      if (!definition) throw new Error(item.id);
      const defaults = Object.fromEntries(
        definition.inputs.map((input) => [input.key, input.default]),
      );
      const outputs = definition.compute(
        fixture.bars,
        { ...defaults, ...item.params },
        { timeZone: fixture.time_zone },
      );
      for (const [key, expected] of Object.entries(item.outputs)) {
        const actual = outputs[key];
        expect(actual, key).toHaveLength(expected.length);
        expected.forEach((value, i) => {
          if (value === null) expect(actual[i], `${key}[${i}]`).toBeNull();
          else expect(actual[i], `${key}[${i}]`).toBeCloseTo(value, 9);
        });
      }
    },
  );
});
