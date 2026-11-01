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
import type { StrategyReport } from "./pine-strategy";
import type { StrategyResults } from "./strategy-research-api";

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
