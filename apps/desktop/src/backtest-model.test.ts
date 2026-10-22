import type {
  BacktestFill,
  BrokerOrderExecution,
  CustomIndicator,
  ManualExecution,
} from "@siw/contracts/types";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import {
  BACKTEST_INDICATOR_IDS,
  compareFills,
  defaultRulesDraft,
  indicatorLabel,
  indicatorOperand,
  percent,
  ruleIndicatorIds,
  rulesDefinition,
  rulesDraftOf,
  tunablePaths,
  walkForwardSpec,
  DEFAULT_WALK_FORWARD,
  backtestOutputs,
} from "./backtest-model";
import { registerCustom } from "./custom-indicators";

const savedIndicator = JSON.parse(
  readFileSync(
    resolve(
      import.meta.dirname,
      "../../../tests/fixtures/contracts/valid/custom-indicator.json",
    ),
    "utf8",
  ),
) as CustomIndicator;

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

  it("lists the numbers a walk-forward grid can vary and builds its settings", () => {
    const paths = tunablePaths(rulesDefinition(defaultRulesDraft()));
    expect(paths.map((item) => item.path)).toEqual([
      "/entry/left/params/length",
      "/entry/right/params/length",
      "/exit/left/params/length",
      "/exit/right/params/length",
      "/sizing/value",
    ]);
    expect(paths[0]).toMatchObject({
      label: "entry · left · ema length",
      current: "10",
    });
    expect(walkForwardSpec(DEFAULT_WALK_FORWARD)).toEqual({
      error: "bt.wf.incomplete",
    });
    expect(
      walkForwardSpec({
        ...DEFAULT_WALK_FORWARD,
        enabled: true,
        grid: [{ path: "/entry/left/params/length", values: "5, 10,20" }],
      }),
    ).toEqual({
      in_sample_bars: 120,
      out_of_sample_bars: 40,
      objective: "net_return",
      grid: [{ path: "/entry/left/params/length", values: [5, 10, 20] }],
      anchored: false,
    });
    expect(
      tunablePaths({ type: "python", params: { threshold: 3, name: "x" } }),
    ).toEqual([
      { path: "/params/threshold", label: "params · threshold", current: "3" },
    ]);
  });

  it("writes limit and stop orders and reads them back", () => {
    const draft = {
      ...defaultRulesDraft(),
      entryOrder: {
        type: "limit" as const,
        offsetPct: "-1.5",
        expireBars: "3",
      },
      exitOrder: { type: "stop" as const, offsetPct: "-2", expireBars: "1" },
    };
    const definition = rulesDefinition(draft);
    expect(definition.entry_order).toEqual({
      type: "limit",
      offset_pct: "-1.5",
      expire_bars: 3,
    });
    expect(definition.exit_order).toEqual({
      type: "stop",
      offset_pct: "-2",
      expire_bars: 1,
    });
    const back = rulesDraftOf(definition);
    expect(back?.entryOrder).toEqual(draft.entryOrder);
    expect(back?.exitOrder).toEqual(draft.exitOrder);
    // Market orders stay out of the JSON, as before.
    expect("entry_order" in rulesDefinition(defaultRulesDraft())).toBe(false);
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

  it("offers the latest saved Python indicators, pinned by version", () => {
    const v1 = savedIndicator;
    const v2 = {
      ...v1,
      version: 2,
      versions: [1, 2],
      type: v1.type.replace("@1", "@2"),
    };
    registerCustom([v1, v2]);
    expect(ruleIndicatorIds()).toEqual([...BACKTEST_INDICATOR_IDS, v2.type]);
    // A rule still pinned to version 1 keeps it in its own list.
    expect(ruleIndicatorIds(v1.type)).toEqual([
      ...BACKTEST_INDICATOR_IDS,
      v2.type,
      v1.type,
    ]);
    expect(indicatorLabel(v2.type, "en")).toBe("RSI band (Python v2)");
    expect(indicatorLabel("sma", "en")).toBe("SMA");
    const operand = indicatorOperand(v1.type);
    expect(operand).toEqual({
      kind: "indicator",
      indicator: v1.type,
      params: { length: 14, high: 70 },
      output: "rsi",
    });
    const draft = defaultRulesDraft();
    draft.entry[0].right = { ...operand, output: "over" } as typeof operand;
    const definition = rulesDefinition(draft);
    expect((definition.entry as { right: unknown }).right).toEqual({
      indicator: v1.type,
      params: { length: 14, high: 70 },
      output: "over",
    });
    expect(rulesDefinition(rulesDraftOf(definition)!)).toEqual(definition);
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

describe("indicators offered to backtests", () => {
  it("include every chart indicator but never a look-ahead output", () => {
    expect(BACKTEST_INDICATOR_IDS).toHaveLength(34);
    expect(backtestOutputs("ichimoku").map((output) => output.key)).toEqual([
      "conversion",
      "base",
      "spanA",
      "spanB",
    ]);
    expect(backtestOutputs("dmi").map((output) => output.key)).toEqual([
      "adx",
      "plus",
      "minus",
    ]);
  });
});
