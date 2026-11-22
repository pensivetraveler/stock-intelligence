// Conversion survey (ADR 0058 step 4): how many Pine indicators in a folder convert to
// formulas, and which features keep the rest as Pine. Opt-in:
//   SIW_PINE_SAMPLES=/path/to/folder pnpm vitest run src/pine-convert.survey.test.ts
// Every *.pine file is read. Scripts stay outside the repository (it is public).
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, it } from "vitest";

import { isPineStrategy } from "./pine-assistant";
import {
  compareConversion,
  convertPine,
  pineBlockers,
  sampleBars,
} from "./pine-convert";

const folder = process.env.SIW_PINE_SAMPLES;

describe.skipIf(!folder)("Pine conversion survey", () => {
  it("prints how many convert and what blocks the rest", () => {
    const files = readdirSync(folder!)
      .filter((name) => name.endsWith(".pine"))
      .sort();
    const rows: Record<string, string>[] = [];
    const blockers = new Map<string, number>();
    const firstReasons = new Map<string, number>();
    let indicators = 0;
    let converted = 0;
    let strategies = 0;
    for (const name of files) {
      const source = readFileSync(join(folder!, name), "utf8");
      if (isPineStrategy(source)) {
        strategies += 1;
        continue;
      }
      indicators += 1;
      const conversion = convertPine(source);
      let result: string;
      if (conversion.ok) {
        const check = compareConversion(
          source,
          conversion,
          {},
          [sampleBars()],
          {
            timeZone: "America/New_York",
          },
        );
        result = check.same
          ? `converted (${check.bars} bars agree)`
          : `differs: ${check.reason}`;
        if (check.same) converted += 1;
      } else {
        result = `Pine: ${conversion.reason} (line ${conversion.line})`;
        const first = conversion.reason.replace(/"[^"]*"/g, '"…"');
        firstReasons.set(first, (firstReasons.get(first) ?? 0) + 1);
      }
      const all = pineBlockers(source);
      all.forEach((item) => blockers.set(item, (blockers.get(item) ?? 0) + 1));
      rows.push({ file: name, result, blockers: all.join(", ") });
    }
    console.table(rows);
    console.log(
      JSON.stringify(
        {
          folder,
          indicators,
          converted,
          strategies_skipped: strategies,
          blockers_by_scripts: Object.fromEntries(
            [...blockers].sort((a, b) => b[1] - a[1]),
          ),
          first_reasons: Object.fromEntries(
            [...firstReasons].sort((a, b) => b[1] - a[1]),
          ),
        },
        null,
        2,
      ),
    );
  });
});
