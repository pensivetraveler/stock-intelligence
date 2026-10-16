import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { compareDecimal, validationErrors } from "../src/validator";

const fixtureRoot = resolve(
  import.meta.dirname,
  "../../../tests/fixtures/contracts",
);

function fixtures(group: "valid" | "invalid"): Array<[string, unknown]> {
  const directory = resolve(fixtureRoot, group);
  return readdirSync(directory)
    .filter((name) => name.endsWith(".json"))
    .sort()
    .map((name) => [
      name,
      JSON.parse(readFileSync(resolve(directory, name), "utf8")),
    ]);
}

describe("canonical JSON Schema and semantic validation", () => {
  it.each(fixtures("valid"))("accepts %s", (_name, document) => {
    expect(validationErrors(document)).toEqual([]);
  });

  it.each(fixtures("invalid"))("rejects %s", (_name, document) => {
    expect(validationErrors(document).length).toBeGreaterThan(0);
  });
});

describe("compareDecimal", () => {
  it.each([
    ["250.10000000000000001", "250.1", 1],
    ["250.1", "250.10", 0],
    ["0", "-0.0", 0],
    ["-1.5", "-1.25", -1],
    ["-2", "1", -1],
    ["10", "9.999", 1],
    ["0.001", "0.01", -1],
  ])("compares %s with %s exactly", (left, right, expected) => {
    expect(compareDecimal(left, right)).toBe(expected);
    expect(compareDecimal(right, left)).toBe(0 - expected);
  });
});
