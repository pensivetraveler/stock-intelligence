import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { validationErrors } from "../src/validator";

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
