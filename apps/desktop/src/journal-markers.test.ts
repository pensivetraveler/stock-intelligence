import type {
  BacktestFill,
  BrokerOrderExecution,
  ManualExecution,
  PriceBar,
  ScenarioDefinition,
} from "@siw/contracts/types";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import {
  backtestMarkers,
  journalMarkers,
  MARKER_COLORS,
} from "./journal-markers";

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
  it("draws broker API fills at their reported time and skips unknown times", () => {
    const base: BrokerOrderExecution = {
      kind: "broker_order_execution",
      schema_version: "0.1",
      execution_id: "kiwoom-exec-1",
      instrument_id: "us:aapl:xnas:2026",
      symbol: "AAPL",
      side: "buy",
      quantity: "10",
      price: "250.10",
      fees: null,
      commission: null,
      tax: null,
      currency: "USD",
      executed_at_utc: "2026-10-09T14:01:00Z",
      time_meaning: "reported_fill_time",
      granularity: "order_aggregate",
      order_type: "지정가",
      order_status: "체결완료",
      ordered_at_utc: "2026-10-09T14:00:00Z",
      settlement_date: null,
      broker_id: "kiwoom",
      broker_account_ref: "kiwoom-abc",
      broker_order_id: "20261009-000000001",
      verification_status: "broker_api_reported",
      synced_at_utc: "2026-10-10T00:00:00Z",
    };
    const markers = journalMarkers(
      bars,
      "us:aapl:xnas:2026",
      [],
      [],
      [],
      [
        {
          ...base,
          instrument_id: "us:aapl:xnas:2026",
          side: "sell",
          executed_at_utc: "2026-10-09T14:11:00Z",
        },
        { ...base, instrument_id: "us:aapl:xnas:2026", executed_at_utc: null },
        {
          ...base,
          instrument_id: "us:msft:xnas:2026",
          executed_at_utc: "2026-10-09T14:01:00Z",
        },
      ],
    );
    expect(markers).toHaveLength(1);
    expect(markers[0]).toMatchObject({
      time: Date.UTC(2026, 9, 9, 14, 10) / 1000,
      position: "aboveBar",
      color: MARKER_COLORS.broker,
    });
    expect(markers[0].text).toMatch(/^B sell/);
  });

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

describe("backtest markers", () => {
  it("draws fills of the chosen run on their bar, in their own colour", () => {
    const fill = fixture<BacktestFill>("backtest-fill.json");
    const markers = backtestMarkers(bars, "us:aapl:xnas:2026", [
      {
        ...fill,
        instrument_id: "us:aapl:xnas:2026",
        filled_at_utc: "2026-10-09T14:06:00Z",
      },
      { ...fill, fill_id: "other", instrument_id: "us:msft:xnas:2026" },
      {
        ...fill,
        fill_id: "outside",
        instrument_id: "us:aapl:xnas:2026",
        filled_at_utc: "2026-10-09T18:00:00Z",
      },
    ]);
    expect(markers).toHaveLength(1);
    expect(markers[0]).toMatchObject({
      time: Date.UTC(2026, 9, 9, 14, 5) / 1000,
      color: MARKER_COLORS.backtest,
      text: `BT ${fill.side} ${fill.quantity}`,
    });
  });
});
