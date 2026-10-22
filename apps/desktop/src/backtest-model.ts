import type {
  BacktestFill,
  BrokerOrderExecution,
  ImportedExecution,
  ManualExecution,
} from "@siw/contracts/types";

import type { StrategyDefinition } from "./backtest-api";
import { isCustomType, latestCustom } from "./custom-indicators";
import type { Language } from "./i18n";
import {
  INDICATOR_BY_ID,
  INDICATORS,
  type IndicatorDefinition,
} from "./indicators";

/**
 * Indicators the backtest engine computes: every built-in chart indicator, ported to
 * Python with a shared parity fixture (ADR 0021).
 */
export const BACKTEST_INDICATOR_IDS = INDICATORS.map((item) => item.id);

/** Chart outputs a backtest must not read: they draw later values in the past. */
const LOOKAHEAD_OUTPUTS: Record<string, string[]> = { ichimoku: ["lagging"] };

/** The outputs a rule may compare, without look-ahead ones. */
export function backtestOutputs(id: string) {
  const hidden = LOOKAHEAD_OUTPUTS[id] ?? [];
  return backtestIndicator(id).outputs.filter(
    (output) => !hidden.includes(output.key),
  );
}

export function backtestIndicator(id: string): IndicatorDefinition {
  const definition = INDICATOR_BY_ID.get(id);
  if (!definition) throw new Error(`unknown indicator ${id}`);
  return definition;
}

/**
 * Indicators a rule operand can use: the built-ins, then the latest version of each
 * saved Python indicator (`py:<id>@<version>`, ADR 0023), plus `current` when a rule is
 * pinned to an older version that is still known.
 */
export function ruleIndicatorIds(current?: string): string[] {
  const ids: string[] = [
    ...BACKTEST_INDICATOR_IDS,
    ...latestCustom().map((item) => item.type),
  ];
  if (current && !ids.includes(current) && INDICATOR_BY_ID.has(current)) {
    ids.push(current);
  }
  return ids;
}

/** Built-ins by their short name; Python indicators by name and version. */
export function indicatorLabel(id: string, language: Language): string {
  const definition = backtestIndicator(id);
  return isCustomType(id) ? definition.name[language] : definition.short;
}

export const COMPARISONS = [
  "cross_above",
  "cross_below",
  "gt",
  "gte",
  "lt",
  "lte",
] as const;
export type Comparison = (typeof COMPARISONS)[number];
export const PRICES = ["open", "high", "low", "close", "volume"] as const;
export type PriceField = (typeof PRICES)[number];
export const SIZING_MODES = [
  "percent_equity",
  "fixed_cash",
  "fixed_shares",
] as const;
export type SizingMode = (typeof SIZING_MODES)[number];

export type OperandDraft =
  | {
      kind: "indicator";
      indicator: string;
      params: Record<string, number>;
      output: string;
    }
  | { kind: "price"; price: PriceField }
  | { kind: "value"; value: string };

export interface ConditionDraft {
  key: string;
  left: OperandDraft;
  op: Comparison;
  right: OperandDraft;
}

export type OrderKind = "market" | "limit" | "stop";
/** How an entry or exit is ordered (ADR 0031): offset from the signal bar's close. */
export interface OrderDraft {
  type: OrderKind;
  offsetPct: string;
  expireBars: string;
}
export const MARKET_ORDER: OrderDraft = {
  type: "market",
  offsetPct: "",
  expireBars: "1",
};

export interface RulesDraft {
  entryOrder?: OrderDraft;
  exitOrder?: OrderDraft;
  entryMode: "all" | "any";
  entry: ConditionDraft[];
  exitMode: "all" | "any";
  exit: ConditionDraft[];
  stopLossPct: string;
  takeProfitPct: string;
  sizingMode: SizingMode;
  sizingValue: string;
  maxPositions: string;
}

export function indicatorOperand(id: string): OperandDraft {
  const definition = backtestIndicator(id);
  return {
    kind: "indicator",
    indicator: id,
    params: Object.fromEntries(
      definition.inputs.map((input) => [input.key, input.default]),
    ),
    output: definition.outputs[0].key,
  };
}

let keys = 0;
export const conditionKey = () => `c${++keys}`;

/** A moving-average crossover: a working strategy to start from. */
export function defaultRulesDraft(): RulesDraft {
  const fast = indicatorOperand("ema");
  const slow = indicatorOperand("sma");
  if (fast.kind === "indicator") fast.params.length = 10;
  if (slow.kind === "indicator") slow.params.length = 30;
  return {
    entryMode: "all",
    entry: [
      { key: conditionKey(), left: fast, op: "cross_above", right: slow },
    ],
    exitMode: "all",
    exit: [{ key: conditionKey(), left: fast, op: "cross_below", right: slow }],
    stopLossPct: "",
    takeProfitPct: "",
    sizingMode: "percent_equity",
    sizingValue: "20",
    maxPositions: "5",
  };
}

export const PYTHON_TEMPLATE = `# on_bar(ctx) runs at the close of every final bar of every instrument.
# ctx.instrument_id, ctx.time, ctx.bar (open/high/low/close/volume),
# ctx.history (this instrument's bars so far), ctx.closes, ctx.params,
# ctx.position (None or {"quantity", "average_price"}), ctx.cash, ctx.equity,
# ctx.indicator(id, output=None, **params) -> latest value or None.
# Return ctx.buy(percent_equity=…|cash=…|quantity=…), ctx.sell(), a list, or None.
# Orders fill at the next bar's open. Add order_type="limit" or "stop" with limit_price /
# stop_price (or offset_pct from this close) and expire_bars for other orders.
# No network, files or subprocesses.

def on_bar(ctx):
    rsi = ctx.indicator("rsi", length=ctx.params.get("length", 14))
    if rsi is None:
        return None
    if ctx.position is None and rsi < 30:
        return ctx.buy(percent_equity=20)
    if ctx.position is not None and rsi > 70:
        return ctx.sell()
    return None
`;

function operandJson(operand: OperandDraft): Record<string, unknown> {
  if (operand.kind === "indicator") {
    return {
      indicator: operand.indicator,
      params: operand.params,
      output: operand.output,
    };
  }
  if (operand.kind === "price") return { price: operand.price };
  return { value: operand.value };
}

function group(
  mode: "all" | "any",
  conditions: ConditionDraft[],
): Record<string, unknown> | null {
  if (conditions.length === 0) return null;
  const items = conditions.map((condition) => ({
    op: condition.op,
    left: operandJson(condition.left),
    right: operandJson(condition.right),
  }));
  return items.length === 1 ? items[0] : { [mode]: items };
}

const optionalNumber = (text: string) =>
  text.trim() === "" ? null : text.trim();

function orderJson(order: OrderDraft | undefined): Record<string, unknown> {
  if (!order || order.type === "market") return {};
  return {
    type: order.type,
    offset_pct: order.offsetPct.trim(),
    expire_bars: Number(order.expireBars),
  };
}

function orderDraft(value: unknown): OrderDraft {
  if (typeof value !== "object" || value === null) return MARKET_ORDER;
  const item = value as Record<string, unknown>;
  if (item.type !== "limit" && item.type !== "stop") return MARKET_ORDER;
  return {
    type: item.type,
    offsetPct: String(item.offset_pct ?? ""),
    expireBars: String(item.expire_bars ?? 1),
  };
}

/** The JSON the API validates; it, not this screen, is the judge of what is valid. */
export function rulesDefinition(draft: RulesDraft): StrategyDefinition {
  return {
    type: "rules",
    entry: group(draft.entryMode, draft.entry),
    exit: group(draft.exitMode, draft.exit),
    stop_loss_pct: optionalNumber(draft.stopLossPct),
    take_profit_pct: optionalNumber(draft.takeProfitPct),
    sizing: { mode: draft.sizingMode, value: draft.sizingValue.trim() },
    max_positions: Number(draft.maxPositions),
    // Market orders are left out, so saved strategies keep their fingerprints.
    ...(draft.entryOrder?.type && draft.entryOrder.type !== "market"
      ? { entry_order: orderJson(draft.entryOrder) }
      : {}),
    ...(draft.exitOrder?.type && draft.exitOrder.type !== "market"
      ? { exit_order: orderJson(draft.exitOrder) }
      : {}),
  };
}

function operandDraft(value: unknown): OperandDraft | null {
  if (typeof value !== "object" || value === null) return null;
  const item = value as Record<string, unknown>;
  if (typeof item.indicator === "string") {
    if (!INDICATOR_BY_ID.has(item.indicator)) return null;
    const base = indicatorOperand(item.indicator);
    if (base.kind !== "indicator") return null;
    return {
      ...base,
      params: { ...base.params, ...(item.params as Record<string, number>) },
      output: typeof item.output === "string" ? item.output : base.output,
    };
  }
  if (typeof item.price === "string") {
    return { kind: "price", price: item.price as PriceField };
  }
  if ("value" in item) return { kind: "value", value: String(item.value) };
  return null;
}

function conditionsOf(
  value: unknown,
): { mode: "all" | "any"; items: ConditionDraft[] } | null {
  if (value === null || value === undefined) return { mode: "all", items: [] };
  if (typeof value !== "object") return null;
  const item = value as Record<string, unknown>;
  const mode = "any" in item ? "any" : "all";
  const list = Array.isArray(item[mode]) ? (item[mode] as unknown[]) : [item];
  const items: ConditionDraft[] = [];
  for (const entry of list) {
    if (typeof entry !== "object" || entry === null) return null;
    const comparison = entry as Record<string, unknown>;
    // Nested groups are valid for the engine but too deep for this form.
    if (!COMPARISONS.includes(comparison.op as Comparison)) return null;
    const left = operandDraft(comparison.left);
    const right = operandDraft(comparison.right);
    if (!left || !right) return null;
    items.push({
      key: conditionKey(),
      left,
      op: comparison.op as Comparison,
      right,
    });
  }
  return { mode, items };
}

/** The form for a saved rules definition, or null when it is too deep to show as one. */
export function rulesDraftOf(
  definition: Record<string, unknown>,
): RulesDraft | null {
  const entry = conditionsOf(definition.entry);
  const exit = conditionsOf(definition.exit);
  const sizing = definition.sizing as { mode: SizingMode; value: string };
  if (!entry || !exit || !sizing) return null;
  return {
    entryMode: entry.mode,
    entry: entry.items,
    exitMode: exit.mode,
    exit: exit.items,
    stopLossPct: String(definition.stop_loss_pct ?? ""),
    takeProfitPct: String(definition.take_profit_pct ?? ""),
    sizingMode: sizing.mode,
    sizingValue: String(sizing.value),
    maxPositions: String(definition.max_positions ?? 10),
    entryOrder: orderDraft(definition.entry_order),
    exitOrder: orderDraft(definition.exit_order),
  };
}

export interface FillSummary {
  buys: number;
  sells: number;
  boughtShares: number;
  averageBuy: number | null;
  averageSell: number | null;
}

export interface FillComparison {
  instrumentId: string;
  backtest: FillSummary;
  actual: FillSummary;
}

interface SimpleFill {
  instrumentId: string;
  side: "buy" | "sell";
  quantity: number;
  price: number;
  time: string | null;
}

function summarize(fills: SimpleFill[]): FillSummary {
  const average = (side: "buy" | "sell") => {
    const items = fills.filter((fill) => fill.side === side);
    const shares = items.reduce((total, fill) => total + fill.quantity, 0);
    return shares > 0
      ? items.reduce((total, fill) => total + fill.quantity * fill.price, 0) /
          shares
      : null;
  };
  return {
    buys: fills.filter((fill) => fill.side === "buy").length,
    sells: fills.filter((fill) => fill.side === "sell").length,
    boughtShares: fills
      .filter((fill) => fill.side === "buy")
      .reduce((total, fill) => total + fill.quantity, 0),
    averageBuy: average("buy"),
    averageSell: average("sell"),
  };
}

/**
 * Backtest fills next to the user's real ones (manual, imported and broker API
 * ledgers) for the same instruments and dates. Real executions with no known time
 * are left out. The two are different things: this is a reference, not a match.
 */
export function compareFills(
  instrumentIds: string[],
  startDate: string,
  endDate: string,
  backtest: BacktestFill[],
  real: {
    manual: ManualExecution[];
    imported: ImportedExecution[];
    broker: BrokerOrderExecution[];
  },
): FillComparison[] {
  const from = Date.parse(`${startDate}T00:00:00Z`) - 86_400_000;
  const to = Date.parse(`${endDate}T00:00:00Z`) + 2 * 86_400_000;
  const actual: SimpleFill[] = [
    ...real.manual,
    ...real.imported,
    ...real.broker,
  ].map((execution) => ({
    instrumentId: execution.instrument_id,
    side: execution.side,
    quantity: Number(execution.quantity),
    price: Number(execution.price),
    time: execution.executed_at_utc ?? null,
  }));
  return instrumentIds.map((instrumentId) => ({
    instrumentId,
    backtest: summarize(
      backtest
        .filter((fill) => fill.instrument_id === instrumentId)
        .map((fill) => ({
          instrumentId,
          side: fill.side,
          quantity: Number(fill.quantity),
          price: Number(fill.price),
          time: fill.filled_at_utc,
        })),
    ),
    actual: summarize(
      actual.filter((fill) => {
        if (fill.instrumentId !== instrumentId || fill.time === null)
          return false;
        const at = Date.parse(fill.time);
        return at >= from && at < to;
      }),
    ),
  }));
}

/** "-0.133158" -> "-13.32%". */
export function percent(value: string | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${(Number(value) * 100).toFixed(2)}%`;
}

/** A number in a rules definition that a walk-forward grid may vary (ADR 0032). */
export interface TunablePath {
  path: string;
  label: string;
  current: string;
}

/**
 * Numeric settings of a rules definition as JSON pointers: indicator parameters,
 * comparison values, sizing, stop loss / take profit and order offsets.
 */
export function tunablePaths(definition: unknown): TunablePath[] {
  const found: TunablePath[] = [];
  const isNumber = (value: unknown) =>
    (typeof value === "number" && Number.isFinite(value)) ||
    (typeof value === "string" &&
      value.trim() !== "" &&
      !Number.isNaN(Number(value)));
  const walk = (node: unknown, path: string, label: string[]) => {
    if (Array.isArray(node)) {
      node.forEach((item, index) =>
        walk(item, `${path}/${index}`, [...label, `#${index + 1}`]),
      );
      return;
    }
    if (typeof node !== "object" || node === null) return;
    const record = node as Record<string, unknown>;
    if (typeof record.indicator === "string" && record.params) {
      for (const [name, value] of Object.entries(
        record.params as Record<string, unknown>,
      )) {
        if (isNumber(value)) {
          found.push({
            path: `${path}/params/${name}`,
            label: [...label, `${record.indicator} ${name}`].join(" · "),
            current: String(value),
          });
        }
      }
      return;
    }
    for (const [key, value] of Object.entries(record)) {
      if (key === "type" || key === "op" || key === "mode") continue;
      // A Python strategy's own settings: /params/<name> (ADR 0032).
      const pythonSetting = path === "/params" && isNumber(value);
      if (
        pythonSetting ||
        (isNumber(value) &&
          ["value", "stop_loss_pct", "take_profit_pct", "offset_pct"].includes(
            key,
          ))
      ) {
        found.push({
          path: `${path}/${key}`,
          label: [...label, key].join(" · "),
          current: String(value),
        });
      } else {
        walk(value, `${path}/${key}`, [...label, key]);
      }
    }
  };
  walk(definition, "", []);
  return found;
}

export interface WalkForwardDraft {
  enabled: boolean;
  inSampleBars: string;
  outOfSampleBars: string;
  objective:
    "net_return" | "return_over_drawdown" | "profit_factor" | "win_rate";
  grid: { path: string; values: string }[];
  /** In-sample stretches start at the first bar and grow (instead of rolling). */
  anchored?: boolean;
}

export const DEFAULT_WALK_FORWARD: WalkForwardDraft = {
  enabled: false,
  inSampleBars: "120",
  outOfSampleBars: "40",
  objective: "net_return",
  grid: [{ path: "", values: "" }],
};

/** The run config's walk_forward, or an error message key when the draft is incomplete. */
export function walkForwardSpec(
  draft: WalkForwardDraft,
): Record<string, unknown> | { error: "bt.wf.incomplete" } {
  const grid = draft.grid
    .filter((row) => row.path)
    .map((row) => ({
      path: row.path,
      values: row.values
        .split(",")
        .map((text) => text.trim())
        .filter(Boolean)
        .map(Number),
    }));
  if (
    grid.length === 0 ||
    grid.some((row) => row.values.length === 0 || row.values.some(Number.isNaN))
  ) {
    return { error: "bt.wf.incomplete" };
  }
  return {
    in_sample_bars: Number(draft.inSampleBars),
    out_of_sample_bars: Number(draft.outOfSampleBars),
    objective: draft.objective,
    grid,
    anchored: draft.anchored ?? false,
  };
}
