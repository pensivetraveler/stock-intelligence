import type {
  BacktestFill,
  BrokerOrderExecution,
  ManualExecution,
} from "@siw/contracts/types";
import { describe, expect, it } from "vitest";

import {
  compareFills,
  defaultRulesDraft,
  indicatorOperand,
  percent,
  rulesDefinition,
  rulesDraftOf,
} from "./backtest-model";

describe("rule builder model", () => {
  it("writes the JSON the engine expects", () => {
    const definition = rulesDefinition(defaultRulesDraft());
    expect(definition).toEqual({
      type: "rules",
      entry: {
        op: "cross_above",
        left: { indicator: "ema", params: { length: 10 }, output: "value" },
        right: { indicator: "sma", params: { length: 30 }, output: "value" },
      },
      exit: {
        op: "cross_below",
        left: { indicator: "ema", params: { length: 10 }, output: "value" },
        right: { indicator: "sma", params: { length: 30 }, output: "value" },
      },
      stop_loss_pct: null,
      take_profit_pct: null,
      sizing: { mode: "percent_equity", value: "20" },
      max_positions: 5,
    });
  });

  it("groups several conditions and reads them back", () => {
    const draft = defaultRulesDraft();
    draft.entry.push({
      key: "extra",
      left: indicatorOperand("rsi"),
      op: "lt",
      right: { kind: "value", value: "70" },
    });
    draft.entryMode = "any";
    draft.stopLossPct = "8";
    const definition = rulesDefinition(draft);
    expect(Object.keys(definition.entry as object)).toEqual(["any"]);
    const back = rulesDraftOf(definition);
    expect(back).not.toBeNull();
    expect(rulesDefinition(back!)).toEqual(definition);
  });

  it("gives up on rules nested deeper than the form shows", () => {
    const nested = {
      ...rulesDefinition(defaultRulesDraft()),
      entry: {
        all: [{ any: [{ op: "gt", left: { value: 1 }, right: { value: 0 } }] }],
      },
    };
    expect(rulesDraftOf(nested)).toBeNull();
  });

  it("formats ratios as percentages", () => {
    expect(percent("-0.133158")).toBe("-13.32%");
    expect(percent(null)).toBe("—");
  });
});

describe("comparison with real fills", () => {
  const fill = (side: "buy" | "sell", price: string, at: string) =>
    ({
      fill_id: `${side}${at}`,
      instrument_id: "us:aapl:xnas:2026",
      side,
      quantity: "10",
      price,
      filled_at_utc: at,
    }) as BacktestFill;

  it("summarizes both sides for the run's instruments and dates only", () => {
    const real = {
      manual: [
        {
          instrument_id: "us:aapl:xnas:2026",
          side: "buy",
          quantity: "5",
          price: "100",
          executed_at_utc: "2026-03-02T15:00:00Z",
        },
        {
          instrument_id: "us:aapl:xnas:2026",
          side: "buy",
          quantity: "5",
          price: "90",
          executed_at_utc: null,
        },
      ] as ManualExecution[],
      imported: [],
      broker: [
        {
          instrument_id: "us:aapl:xnas:2026",
          side: "sell",
          quantity: "5",
          price: "120",
          executed_at_utc: "2027-01-01T15:00:00Z",
        },
      ] as BrokerOrderExecution[],
    };
    const [row] = compareFills(
      ["us:aapl:xnas:2026"],
      "2026-01-02",
      "2026-06-30",
      [
        fill("buy", "100", "2026-02-02T14:30:00Z"),
        fill("sell", "110", "2026-02-10T14:30:00Z"),
      ],
      real,
    );
    expect(row.backtest).toMatchObject({
      buys: 1,
      sells: 1,
      averageBuy: 100,
      averageSell: 110,
    });
    // The undated manual fill and the broker fill after the run are left out.
    expect(row.actual).toMatchObject({
      buys: 1,
      sells: 0,
      averageBuy: 100,
      averageSell: null,
    });
  });
});
