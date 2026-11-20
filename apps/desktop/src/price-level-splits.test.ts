import type { FilingSplitsResponse } from "@siw/contracts/types";
import { describe, expect, it } from "vitest";

import { translator } from "./i18n";
import { levelsOnChart } from "./price-level-splits";
import type { ChartPriceLevel } from "./PriceChart";

const t = translator("en");

function splits(
  events: { on: string; numerator: number; denominator: number }[],
  comparedWith: string | null = "alpaca-corporate-actions",
): FilingSplitsResponse {
  return {
    kind: "filing_splits_response",
    schema_version: "0.1",
    instrument_id: "us:xxii:xnas:2026",
    status: "found",
    filings_from: null,
    as_of: null,
    rows: [],
    compared_with: comparedWith,
    differences: [],
    events: events.map((provider) => ({
      kind: "provider_only",
      filing: null,
      provider,
    })),
  };
}

// XXII: warrants at 71.40 in the 10-Q of 2026-08-14; Alpaca has reverse splits on
// 2026-06-12 (1:20, before the filing) and 2026-11-05 (1:58, after it).
const XXII = splits([
  { on: "2026-06-12", numerator: 1, denominator: 20 },
  { on: "2026-11-05", numerator: 1, denominator: 58 },
]);
const warrant: ChartPriceLevel = {
  price: 71.4,
  title: "S1",
  color: "#c00",
  asOf: "2026-08-14",
};

describe("levelsOnChart", () => {
  it("scales a filing price by the Alpaca splits after the filing date", () => {
    const [level] = levelsOnChart([warrant], "split_adjusted", XXII, t)!;
    expect(level.price).toBeCloseTo(4141.2, 6);
    expect(level.title).toBe("S1 (×58, Alpaca splits after the filing)");
  });

  it("scales a forward split down", () => {
    const [level] = levelsOnChart(
      [warrant],
      "split_adjusted",
      splits([{ on: "2026-09-01", numerator: 4, denominator: 1 }]),
      t,
    )!;
    expect(level.price).toBeCloseTo(17.85, 6);
  });

  it("leaves a price alone when no split came after its date", () => {
    const later = { ...warrant, asOf: "2026-11-16" };
    expect(levelsOnChart([later], "split_adjusted", XXII, t)).toEqual([later]);
  });

  it("says so when Alpaca's splits could not be read", () => {
    const [level] = levelsOnChart(
      [warrant],
      "split_adjusted",
      splits([], null),
      t,
    )!;
    expect(level.price).toBe(71.4);
    expect(level.title).toBe("S1 (splits after the filing unknown)");
    expect(
      levelsOnChart([warrant], "split_adjusted", null, t)![0].title,
    ).toContain("unknown");
  });

  it("leaves as-traded charts and undated levels alone", () => {
    expect(levelsOnChart([warrant], "raw", XXII, t)).toEqual([warrant]);
    const undated = { price: 1.1, title: "Z1", color: "#888" };
    expect(levelsOnChart([undated], "split_adjusted", XXII, t)).toEqual([
      undated,
    ]);
    expect(levelsOnChart(undefined, "split_adjusted", XXII, t)).toBeUndefined();
  });
});
