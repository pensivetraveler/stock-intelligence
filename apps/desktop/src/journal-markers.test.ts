import type {
  ManualExecution,
  PriceBar,
  ScenarioDefinition,
} from "@siw/contracts/types";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { journalMarkers, MARKER_COLORS } from "./journal-markers";

const fixtures = resolve(
  import.meta.dirname,
  "../../../tests/fixtures/contracts/valid",
);
const fixture = <T>(name: string): T =>
  JSON.parse(readFileSync(resolve(fixtures, name), "utf8")) as T;

const bars: PriceBar[] = [0, 5, 10].map((offset) => ({
  ...fixture<PriceBar>("aggregated-price-bar.json"),
  instrument_id: "us:aapl:xnas:2026",
  interval_start_utc: new Date(Date.UTC(2026, 9, 9, 14, offset))
    .toISOString()
    .replace(".000", ""),
  interval_end_utc: new Date(Date.UTC(2026, 9, 9, 14, offset + 5))
    .toISOString()
    .replace(".000", ""),
}));

describe("journal markers", () => {
  it("places each ledger on the bar containing its time with distinct shapes", () => {
    const manual: ManualExecution = {
      ...fixture<ManualExecution>("manual-execution.json"),
      executed_at_utc: "2026-10-09T14:06:30Z",
    };
    const unknownTime: ManualExecution = {
      ...manual,
      execution_id: "unknown",
      side: "buy",
      time_precision: "unknown",
      executed_at_utc: null,
      selected_interval_start_utc: "2026-10-09T14:00:00Z",
    };
    const scenario = fixture<ScenarioDefinition>("scenario-definition.json");

    const markers = journalMarkers(
      bars,
      "us:aapl:xnas:2026",
      [manual, unknownTime],
      [scenario],
    );

    expect(
      markers.map((marker) => [marker.text, marker.shape, marker.color]),
    ).toEqual([
      ["H long entry", "circle", MARKER_COLORS.hypothetical],
      ["M buy 5 (time unknown)", "arrowUp", MARKER_COLORS.manual],
      ["M sell 5", "arrowDown", MARKER_COLORS.manual],
    ]);
    expect(markers[2].time).toBe(Date.UTC(2026, 9, 9, 14, 5) / 1000);
  });

  it("skips other instruments and times outside the drawn bars", () => {
    const manual = fixture<ManualExecution>("manual-execution.json");

    expect(journalMarkers(bars, "us:other:xnas:2026", [manual], [])).toEqual(
      [],
    );
    expect(
      journalMarkers(
        bars,
        "us:aapl:xnas:2026",
        [{ ...manual, executed_at_utc: "2026-10-09T18:00:00Z" }],
        [],
      ),
    ).toEqual([]);
  });
});
