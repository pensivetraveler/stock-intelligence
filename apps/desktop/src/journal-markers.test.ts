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
  groupMarkers,
  journalMarkers,
  MARKER_COLORS,
  sumDecimals,
  thinLabels,
  type ChartMarker,
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

describe("grouped markers", () => {
  const broker: BrokerOrderExecution = {
    kind: "broker_order_execution",
    schema_version: "0.1",
    execution_id: "e0",
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
    broker_order_id: "1",
    verification_status: "broker_api_reported",
    synced_at_utc: "2026-10-10T00:00:00Z",
  };

  it("collapses one ledger's fills per bar and side, summing quantity exactly", () => {
    const fills = [
      ...Array.from({ length: 12 }, (_, index) => ({
        ...broker,
        execution_id: `buy-${index}`,
        quantity: index === 0 ? "0.5" : "30",
        executed_at_utc: `2026-10-09T14:0${index % 5}:00Z`,
      })),
      { ...broker, execution_id: "sell-1", side: "sell" as const },
      {
        ...broker,
        execution_id: "sell-2",
        side: "sell" as const,
        quantity: "2",
        // Earlier than sell-1, so it is listed first despite its id.
        executed_at_utc: "2026-10-09T14:00:10Z",
      },
      {
        ...broker,
        execution_id: "next-bar",
        executed_at_utc: "2026-10-09T14:06:00Z",
      },
    ];
    const markers = groupMarkers(
      journalMarkers(bars, "us:aapl:xnas:2026", [], [], [], fills),
    );
    expect(markers.map((marker) => [marker.time, marker.text])).toEqual([
      [Date.UTC(2026, 9, 9, 14, 0) / 1000, "B buy ×12 (330.5)"],
      [Date.UTC(2026, 9, 9, 14, 0) / 1000, "B sell ×2 (12)"],
      [Date.UTC(2026, 9, 9, 14, 5) / 1000, "B buy 10"],
    ]);
    expect(markers[0].details).toHaveLength(12);
    expect(markers[0].details?.[0]).toMatch(/^B buy 0\.5 @ 250\.10 · /);
    expect(markers[1].details?.map((line) => line.split(" @")[0])).toEqual([
      "B sell 2",
      "B sell 10",
    ]);
    expect(markers[2].details).toHaveLength(1);
    expect(new Set(markers.map((marker) => marker.id)).size).toBe(3);
  });

  it("keeps ledgers, colours, shapes and unknown-time notes apart", () => {
    const manual = fixture<ManualExecution>("manual-execution.json");
    const onBar = {
      ...manual,
      side: "buy" as const,
      executed_at_utc: "2026-10-09T14:01:00Z",
    };
    const unknown = {
      ...onBar,
      execution_id: "unknown",
      time_precision: "unknown" as const,
      executed_at_utc: null,
      selected_interval_start_utc: "2026-10-09T14:00:00Z",
    };
    const fill = fixture<BacktestFill>("backtest-fill.json");
    const markers = groupMarkers([
      ...journalMarkers(
        bars,
        "us:aapl:xnas:2026",
        [
          onBar,
          { ...onBar, execution_id: "m2" },
          unknown,
          { ...unknown, execution_id: "u2" },
        ],
        [],
        [],
        [broker, { ...broker, execution_id: "e1" }],
      ),
      ...backtestMarkers(bars, "us:aapl:xnas:2026", [
        {
          ...fill,
          instrument_id: "us:aapl:xnas:2026",
          side: "buy",
          quantity: "3",
          filled_at_utc: "2026-10-09T14:02:00Z",
        },
        {
          ...fill,
          fill_id: "f2",
          instrument_id: "us:aapl:xnas:2026",
          side: "buy",
          quantity: "4",
          filled_at_utc: "2026-10-09T14:03:00Z",
        },
      ]),
    ]);
    expect(
      markers.map((marker) => [marker.text, marker.color, marker.shape]).sort(),
    ).toEqual(
      [
        ["B buy ×2 (20)", MARKER_COLORS.broker, "arrowUp"],
        ["BT buy ×2 (7)", MARKER_COLORS.backtest, "arrowUp"],
        [
          `M buy ×2 (${sumDecimals([onBar.quantity, onBar.quantity])})`,
          MARKER_COLORS.manual,
          "arrowUp",
        ],
        [
          `M buy ×2 (${sumDecimals([onBar.quantity, onBar.quantity])}) (time unknown)`,
          MARKER_COLORS.manual,
          "arrowUp",
        ],
      ].sort(),
    );
  });

  it("counts hypothetical legs without a quantity total", () => {
    const scenario = fixture<ScenarioDefinition>("scenario-definition.json");
    const markers = groupMarkers(
      journalMarkers(
        bars,
        "us:aapl:xnas:2026",
        [],
        [scenario, { ...scenario, scenario_id: "s2" }],
      ),
    );
    const entry = markers.find((marker) => marker.shape === "circle");
    expect(entry?.text).toBe(`H ${scenario.direction} entry ×2`);
    expect(entry?.details).toHaveLength(2);
  });

  it("sums decimal strings without float error", () => {
    expect(sumDecimals(["0.1", "0.2"])).toBe("0.3");
    expect(sumDecimals(["10", "2.50", "-1"])).toBe("11.5");
    expect(sumDecimals(["0.000001", "0.000002"])).toBe("0.000003");
    expect(sumDecimals(["1e3"])).toBeNull();
  });
});

describe("thinned labels", () => {
  const marker = (
    id: string,
    minute: number,
    count: number,
    position: ChartMarker["position"] = "belowBar",
  ): ChartMarker => ({
    id,
    time: minute * 60,
    position,
    shape: position === "belowBar" ? "arrowUp" : "arrowDown",
    color: MARKER_COLORS.broker,
    text: `label ${id}`,
    details: Array.from({ length: count }, (_, index) => `${id}-${index}`),
  });

  it("keeps every marker but labels only the biggest of crowded neighbours", () => {
    const drawn = thinLabels(
      [
        marker("a", 0, 1),
        marker("b", 5, 12),
        marker("c", 10, 2),
        marker("d", 30, 1),
        marker("e", 5, 1, "aboveBar"),
      ],
      300,
      3,
    );
    expect(drawn.map((item) => [item.id, item.text])).toEqual([
      ["a", ""],
      ["b", "label b"],
      ["c", ""],
      ["d", "label d"],
      ["e", "label e"],
    ]);
    expect(drawn[0].details).toHaveLength(1);
  });
});
