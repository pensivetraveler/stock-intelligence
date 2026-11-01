/**
 * How strategy research (ADR 0042) runs Claude's scripts on the chart. A run tells Claude
 * whether the script worked and how many trades it made, never its profit: that goes only
 * in the results the investor chooses to send.
 */

import type { IndicatorInstance, OhlcvBar } from "./indicators";
import { defaultParams } from "./indicators";
import {
  compilePine,
  PineError,
  pineDefinition,
  type PineContext,
  runPine,
} from "./pine";
import { isPineStrategy, type SavedScript } from "./pine-assistant";
import { type Expr, parsePine, type Stmt } from "./pine-parser";
import type { StrategyReport, StrategySettings } from "./pine-strategy";
import type {
  ComparisonRequest,
  StrategyResults,
} from "./strategy-research-api";

/** Trades sent with results, newest kept; the totals still count every trade. */
export const MAX_SENT_TRADES = 50;

export type StrategyRun =
  | { ok: true; summary: string; report: StrategyReport }
  | { ok: false; error: string };

/** Runs a strategy on the chart's bars with its default inputs, as the preview will. */
export function runStrategy(
  source: string,
  bars: OhlcvBar[],
  context: PineContext,
): StrategyRun {
  try {
    const program = compilePine(source);
    if (!program.strategy) {
      return {
        ok: false,
        error: "line 1, column 1: write a strategy() script, not an indicator",
      };
    }
    const params = defaultParams(pineDefinition(source));
    const result = runPine(program, bars, params, context);
    const report = result.strategy;
    if (!report) {
      return { ok: false, error: "the strategy produced no report" };
    }
    const inputs = program.inputs.map(
      (input) =>
        `${input.label.en}=${input.options?.find((o) => o.value === input.default)?.label ?? input.default}`,
    );
    const lines = [
      `Title: ${program.title}; ${bars.length} bars.`,
      ...(inputs.length > 0 ? [`Inputs: ${inputs.join(", ")}.`] : []),
      report.closedTrades === 0 && !report.openPosition
        ? "It made no trades on this chart."
        : `Closed trades: ${report.closedTrades}; open position: ${report.openPosition ? "yes" : "no"}.`,
      ...(report.skippedOrders > 0
        ? [`Orders skipped: ${report.skippedOrders}.`]
        : []),
      ...(result.missing.length > 0
        ? [
            `No data in this test run for request.security: ${result.missing.join(", ")}.`,
          ]
        : []),
    ];
    return { ok: true, summary: lines.join("\n"), report };
  } catch (error) {
    if (error instanceof PineError) {
      return {
        ok: false,
        error: `line ${error.line}, column ${error.column}: ${error.message}`,
      };
    }
    return {
      ok: false,
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

const utc = (time: number) =>
  new Date(time * 1000).toISOString().replace(".000Z", "Z");

/**
 * How the trades crowd into days, in the exchange's time, over every trade rather than only
 * the ones sent. Claude does not count this reliably from a list (pilot A, #SIW-2).
 */
export function concentrationOf(
  trades: { entryTime: number }[],
  timeZone: string | undefined,
): StrategyResults["concentration"] {
  const day = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  const counts = new Map<string, number>();
  for (const trade of trades) {
    const date = day.format(new Date(trade.entryTime * 1000));
    counts.set(date, (counts.get(date) ?? 0) + 1);
  }
  const top = [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, 3);
  const inTop = top.reduce((sum, [, count]) => sum + count, 0);
  return {
    days_with_trades: counts.size,
    top_days: top.map(([date, count]) => ({
      date,
      trades: count,
    })) as StrategyResults["concentration"]["top_days"],
    top3_share_percent:
      trades.length === 0 ? "0.00" : ((inTop / trades.length) * 100).toFixed(2),
  };
}

/** A backtest as it is kept with its version and sent to Claude. */
export function resultsOf(
  report: StrategyReport,
  chart: {
    symbol: string;
    timeframe: string;
    bars: OhlcvBar[];
    context: PineContext;
  },
  version: number | null,
): StrategyResults {
  const { bars } = chart;
  return {
    symbol: chart.symbol,
    timeframe: chart.timeframe,
    bars: bars.length,
    from_utc: bars.length > 0 ? utc(bars[0].time) : "",
    to_utc: bars.length > 0 ? utc(bars[bars.length - 1].time) : "",
    version,
    currency: report.currency,
    initial_capital: report.initialCapital,
    net_profit: report.netProfit,
    net_profit_percent: report.netProfitPercent,
    gross_profit: report.grossProfit,
    gross_loss: report.grossLoss,
    profit_factor: report.profitFactor,
    closed_trades: report.closedTrades,
    win_trades: report.winTrades,
    loss_trades: report.lossTrades,
    win_rate: report.winRate,
    max_drawdown: report.maxDrawdown,
    commission: report.commission,
    assumed_fills: report.assumedFills,
    skipped_orders: report.skippedOrders,
    open_position: report.openPosition
      ? {
          size: report.openPosition.size,
          average_price: report.openPosition.avgPrice,
          open_profit: report.openPosition.openProfit,
        }
      : null,
    concentration: concentrationOf(report.trades, chart.context.timeZone),
    trades: report.trades.slice(-MAX_SENT_TRADES).map((trade) => ({
      direction: trade.direction,
      qty: trade.qty,
      entry_time_utc: utc(trade.entryTime),
      entry_price: trade.entryPrice,
      exit_time_utc: trade.exitTime === null ? null : utc(trade.exitTime),
      exit_price: trade.exitPrice,
      profit: trade.profit,
      assumed: trade.assumed,
    })),
  };
}

/** The Pine strategies on the workspace's charts, this chart's first. */
export function pineStrategiesOn(
  charts: {
    instrumentId: string;
    timeframe: string;
    indicators?: IndicatorInstance[];
  }[],
  chart: number,
  symbolOf: (instrumentId: string) => string,
): SavedScript[] {
  return charts
    .flatMap((config, at) =>
      (config.indicators ?? []).flatMap((item) =>
        item.type === "pine" && item.pine && isPineStrategy(item.pine)
          ? [
              {
                key: item.key,
                title: `${pineDefinition(item.pine).name.en} · ${symbolOf(config.instrumentId)} ${config.timeframe}`,
                code: item.pine,
                sameChart: at === chart,
              },
            ]
          : [],
      ),
    )
    .sort((a, b) => Number(b.sameChart) - Number(a.sameChart));
}

// --- conversion to rules (ADR 0042, step 3) ---------------------------------------------

/** A sub-expression, in parentheses when dropping them could change what it means. */
const grouped = (expr: Expr): string =>
  expr.kind === "binary" || expr.kind === "ternary"
    ? `(${exprText(expr)})`
    : exprText(expr);

function exprText(expr: Expr): string {
  switch (expr.kind) {
    case "number":
      return String(expr.value);
    case "string":
      return JSON.stringify(expr.value);
    case "bool":
      return String(expr.value);
    case "na":
      return "na";
    case "name":
      return expr.name;
    case "call":
      return `${expr.name}(${[
        ...expr.args.map(exprText),
        ...Object.entries(expr.named).map(
          ([name, value]) => `${name}=${exprText(value)}`,
        ),
      ].join(", ")})`;
    case "index":
      return `${exprText(expr.target)}[${exprText(expr.offset)}]`;
    case "unary":
      return `${expr.op === "not" ? "not " : expr.op}${grouped(expr.operand)}`;
    case "binary":
      return `${grouped(expr.left)} ${expr.op} ${grouped(expr.right)}`;
    case "ternary":
      return `${grouped(expr.test)} ? ${grouped(expr.then)} : ${grouped(expr.otherwise)}`;
    case "tuple":
      return `[${expr.items.map(exprText).join(", ")}]`;
    case "block":
      return "<block>";
  }
}

const childStatements = (statement: Stmt): Stmt[] => {
  switch (statement.kind) {
    case "if":
      return [...statement.body, ...statement.otherwise];
    case "for":
    case "forin":
    case "while":
    case "function":
      return statement.body;
    case "switch":
      return statement.cases.flatMap((item) => item.body);
    default:
      return [];
  }
};

function placesOrders(statement: Stmt): boolean {
  if (statement.kind === "expr" && statement.value.kind === "call") {
    if (statement.value.name.startsWith("strategy.")) return true;
  }
  return childStatements(statement).some(placesOrders);
}

/**
 * The conditions of a Pine strategy, from the parser and not from Claude: each order call with
 * its levels, and each test guarding an order, split at `and`/`or` and through the boolean
 * variables it names. Claude must account for every one of them when converting.
 */
export function pineConditions(source: string): string[] {
  const body = parsePine(source);
  const found: string[] = [];
  const add = (text: string) => {
    if (!found.includes(text)) found.push(text);
  };
  const declared = new Map<string, Expr>();
  const declare = (statements: Stmt[]) => {
    for (const statement of statements) {
      if (
        statement.kind === "declare" &&
        statement.names.length === 1 &&
        !declared.has(statement.names[0])
      ) {
        declared.set(statement.names[0], statement.value);
      }
    }
  };
  declare(body);
  const isLogical = (expr: Expr) =>
    (expr.kind === "binary" &&
      ["and", "or", "<", ">", "<=", ">=", "==", "!="].includes(expr.op)) ||
    (expr.kind === "unary" && expr.op === "not") ||
    (expr.kind === "call" && /^ta\.cross/.test(expr.name));
  const test = (expr: Expr, seen: Set<string>) => {
    if (expr.kind === "binary" && (expr.op === "and" || expr.op === "or")) {
      test(expr.left, seen);
      test(expr.right, seen);
      return;
    }
    if (expr.kind === "name" && !seen.has(expr.name)) {
      const value = declared.get(expr.name);
      if (value && isLogical(value)) {
        test(value, new Set(seen).add(expr.name));
        return;
      }
    }
    add(exprText(expr));
  };
  const order = (call: Extract<Expr, { kind: "call" }>) => {
    const levels = Object.keys(call.named).filter((name) =>
      [
        "profit",
        "limit",
        "loss",
        "stop",
        "trail_points",
        "trail_price",
        "trail_offset",
        "qty",
        "qty_percent",
      ].includes(name),
    );
    if (call.name === "strategy.exit" && levels.length > 0) {
      for (const name of levels) {
        add(`strategy.exit ${name}=${exprText(call.named[name])}`);
      }
      return;
    }
    add(exprText(call));
  };
  const settings = (call: Extract<Expr, { kind: "call" }>) => {
    const named = (name: string) =>
      call.named[name] ? exprText(call.named[name]) : null;
    const type = named("default_qty_type");
    const value = named("default_qty_value");
    add(
      type || value
        ? `quantity: ${type ?? "strategy.fixed"} ${value ?? "1"}`
        : "quantity: the default of 1 share",
    );
    for (const name of ["pyramiding", "process_orders_on_close"]) {
      const setting = named(name);
      if (setting !== null && setting !== "0" && setting !== "false") {
        add(`strategy ${name}=${setting}`);
      }
    }
  };
  const walk = (statements: Stmt[]) => {
    declare(statements);
    for (const statement of statements) {
      if (statement.kind === "if" && placesOrders(statement)) {
        test(statement.test, new Set());
      }
      if (statement.kind === "expr" && statement.value.kind === "call") {
        if (statement.value.name === "strategy") settings(statement.value);
        else if (statement.value.name.startsWith("strategy.")) {
          order(statement.value);
        }
      }
      walk(childStatements(statement));
    }
  };
  walk(body);
  return found;
}

/** What the API needs to run the rules beside this Pine backtest, on the same bars. */
export function comparisonRequest(
  code: string,
  report: StrategyReport,
  settings: StrategySettings,
  chart: { bars: OhlcvBar[]; context: PineContext },
): ComparisonRequest {
  const { bars } = chart;
  const tick = 10 ** -(chart.context.priceDecimals ?? 2);
  const price = bars.length > 0 ? bars[bars.length - 1].close : 0;
  return {
    code,
    // Closed trades on both sides: open ones are valued differently.
    pine: {
      closed_trades: report.closedTrades,
      net_profit: Number(report.netProfit),
      entry_times: report.trades
        .filter((trade) => trade.exitTime !== null)
        .map((trade) => trade.entryTime),
    },
    bars: bars.map(({ time, open, high, low, close, volume }) => ({
      time,
      open,
      high,
      low,
      close,
      volume,
    })),
    time_zone: chart.context.timeZone ?? "UTC",
    currency: settings.currency || "USD",
    initial_capital: settings.initialCapital,
    fee_rate:
      settings.commissionType === "percent"
        ? settings.commissionValue / 100
        : 0,
    fee_per_order:
      settings.commissionType === "cash_per_order"
        ? settings.commissionValue
        : 0,
    // Pine slips in ticks and the engine in basis points, at the latest price.
    slippage_bps: price > 0 ? (settings.slippage * tick * 10_000) / price : 0,
  };
}
