import type {
  BacktestResults,
  BacktestRun,
  BrokerOrderExecution,
  ImportedExecution,
  ManualExecution,
} from "@siw/contracts/types";
import { useMemo } from "react";

import { BacktestChart } from "./BacktestChart";
import { compareFills, percent } from "./backtest-model";
import { exchangeTime } from "./chart-data";
import { translateDynamic, useI18n, type MessageKey } from "./i18n";
import { zoneOf } from "./market-zone";

const TABLE_LIMIT = 300;

const money = (value: string | null | undefined, currency: string) =>
  value === null || value === undefined
    ? "—"
    : `${Number(value).toLocaleString("en-US", {
        maximumFractionDigits: currency === "KRW" ? 0 : 2,
        minimumFractionDigits: currency === "KRW" ? 0 : 2,
      })} ${currency}`;

/** Fill prices carry slippage to 6 decimals; 4 are enough to read (whole won for KRW). */
const price = (value: string | null | undefined, currency: string) =>
  value === null || value === undefined
    ? "—"
    : Number(value).toLocaleString("en-US", {
        maximumFractionDigits: currency === "KRW" ? 0 : 4,
      });

const average = (value: number | null) =>
  value === null
    ? "—"
    : value.toLocaleString("en-US", { maximumFractionDigits: 2 });

export interface RealFills {
  manual: ManualExecution[];
  imported: ImportedExecution[];
  broker: BrokerOrderExecution[];
}

export function BacktestResultsView({
  run,
  results,
  symbolOf,
  real,
  shown,
  onToggleShown,
}: {
  run: BacktestRun;
  results: BacktestResults;
  symbolOf: (instrumentId: string) => string;
  real: RealFills;
  shown: boolean;
  onToggleShown: () => void;
}) {
  const { t } = useI18n();
  const metrics = results.metrics;
  const definitions = results.metric_definitions;
  const currency = results.currency;
  const comparison = useMemo(
    () =>
      compareFills(
        run.config.instrument_ids,
        run.config.start_date,
        run.config.end_date,
        results.fills,
        real,
      ),
    [run, results, real],
  );
  const when = (utc: string | null, instrumentId: string) =>
    utc === null
      ? "—"
      : run.config.timeframe === "1d" || run.config.timeframe === "1w"
        ? utc.slice(0, 10)
        : exchangeTime(utc, zoneOf(instrumentId));
  const reason = (code: string | null) =>
    code === null ? "" : translateDynamic(t, `bt.reason.${code}`, code);
  const notable = results.orders.filter((order) => order.status !== "filled");

  const cards: [string, string, string | undefined][] = [
    [t("bt.m.net"), percent(metrics.net_return), definitions.net_return],
    [
      t("bt.m.benchmark"),
      percent(metrics.benchmark_return),
      definitions.benchmark_return,
    ],
    [t("bt.m.gross"), percent(metrics.gross_return), definitions.gross_return],
    [t("bt.m.mdd"), percent(metrics.max_drawdown), definitions.max_drawdown],
    [t("bt.m.trades"), String(metrics.trade_count), definitions.trade_count],
    [t("bt.m.winRate"), percent(metrics.win_rate), definitions.win_rate],
    [
      t("bt.m.profitFactor"),
      metrics.profit_factor === null
        ? "—"
        : Number(metrics.profit_factor).toFixed(2),
      definitions.profit_factor,
    ],
    [
      t("bt.m.avgWin"),
      money(metrics.average_win, currency),
      definitions.average_win,
    ],
    [
      t("bt.m.avgLoss"),
      money(metrics.average_loss, currency),
      definitions.average_loss,
    ],
    [t("bt.m.exposure"), percent(metrics.exposure), definitions.exposure],
    [
      t("bt.m.turnover"),
      metrics.turnover === null ? "—" : Number(metrics.turnover).toFixed(2),
      definitions.turnover,
    ],
    [t("bt.m.fees"), money(metrics.fees, currency), undefined],
    [t("bt.m.slippage"), money(metrics.slippage_cost, currency), undefined],
    [t("bt.m.final"), money(results.final_equity, currency), undefined],
  ];

  return (
    <div className="bt-results">
      <div className="bt-results-head">
        <button type="button" className="tb-button" onClick={onToggleShown}>
          {shown ? t("bt.hideOnChart") : t("bt.showOnChart")}
        </button>
        <span className="workspace-note">
          {t("bt.engineNote", {
            engine: results.engine_version,
            fill: results.fill_model_version,
          })}
        </span>
      </div>

      {results.warnings.length > 0 ? (
        <ul className="bt-warnings" aria-label={t("bt.warnings")}>
          {results.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      ) : null}

      <dl className="bt-metrics">
        {cards.map(([label, value, help]) => (
          <div key={label} title={help}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>

      <BacktestChart
        curve={results.equity_curve}
        labels={{
          equity: t("bt.curve.equity"),
          benchmark: t("bt.curve.benchmark"),
          drawdown: t("bt.curve.drawdown"),
        }}
      />

      <h3>{t("bt.perSymbol")}</h3>
      <table className="bt-table">
        <thead>
          <tr>
            <th>{t("bt.col.symbol")}</th>
            <th>{t("bt.col.trades")}</th>
            <th>{t("bt.col.winRate")}</th>
            <th>{t("bt.col.realized")}</th>
            <th>{t("bt.col.unrealized")}</th>
          </tr>
        </thead>
        <tbody>
          {results.per_symbol.map((row) => (
            <tr key={row.instrument_id}>
              <td>{symbolOf(row.instrument_id)}</td>
              <td>{row.trade_count}</td>
              <td>{percent(row.win_rate)}</td>
              <td>{money(row.realized_pnl, currency)}</td>
              <td>{money(row.unrealized_pnl, currency)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3>{t("bt.trades", { n: results.trades.length })}</h3>
      <table className="bt-table" aria-label={t("bt.tradesTable")}>
        <thead>
          <tr>
            <th>{t("bt.col.symbol")}</th>
            <th>{t("bt.col.entry")}</th>
            <th>{t("bt.col.entryPrice")}</th>
            <th>{t("bt.col.quantity")}</th>
            <th>{t("bt.col.exit")}</th>
            <th>{t("bt.col.exitPrice")}</th>
            <th>{t("bt.col.exitReason")}</th>
            <th>{t("bt.col.pnl")}</th>
            <th>{t("bt.col.return")}</th>
          </tr>
        </thead>
        <tbody>
          {results.trades.slice(0, TABLE_LIMIT).map((trade) => (
            <tr key={trade.entry_order_id}>
              <td>{symbolOf(trade.instrument_id)}</td>
              <td>{when(trade.entry_utc, trade.instrument_id)}</td>
              <td>{price(trade.entry_price, currency)}</td>
              <td>{trade.quantity}</td>
              <td>{when(trade.exit_utc, trade.instrument_id)}</td>
              <td>{price(trade.exit_price, currency)}</td>
              <td>{reason(trade.exit_reason)}</td>
              <td className={Number(trade.net_pnl) < 0 ? "down" : "up"}>
                {money(trade.net_pnl, currency)}
              </td>
              <td>{percent(trade.return)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {results.trades.length > TABLE_LIMIT ? (
        <p className="workspace-note">
          {t("bt.truncated", { n: TABLE_LIMIT })}
        </p>
      ) : null}

      {notable.length > 0 ? (
        <>
          <h3>{t("bt.notFilled", { n: notable.length })}</h3>
          <table className="bt-table">
            <thead>
              <tr>
                <th>{t("bt.col.symbol")}</th>
                <th>{t("bt.col.side")}</th>
                <th>{t("bt.col.signal")}</th>
                <th>{t("bt.col.status")}</th>
                <th>{t("bt.col.reason")}</th>
              </tr>
            </thead>
            <tbody>
              {notable.slice(0, TABLE_LIMIT).map((order) => (
                <tr key={order.order_id}>
                  <td>{symbolOf(order.instrument_id)}</td>
                  <td>{order.side}</td>
                  <td>{when(order.signal_utc, order.instrument_id)}</td>
                  <td>{reason(order.status)}</td>
                  <td>{reason(order.reason)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}

      <h3>{t("bt.compare")}</h3>
      <p className="workspace-note">{t("bt.compareNote")}</p>
      <table className="bt-table" aria-label={t("bt.compare")}>
        <thead>
          <tr>
            <th>{t("bt.col.symbol")}</th>
            <th>{t("bt.col.source")}</th>
            <th>{t("bt.col.buys")}</th>
            <th>{t("bt.col.sells")}</th>
            <th>{t("bt.col.avgBuy")}</th>
            <th>{t("bt.col.avgSell")}</th>
          </tr>
        </thead>
        <tbody>
          {comparison.flatMap((row) =>
            (["backtest", "actual"] as const).map((source) => (
              <tr key={`${row.instrumentId}:${source}`}>
                <td>{symbolOf(row.instrumentId)}</td>
                <td>{t(`bt.source.${source}` as MessageKey)}</td>
                <td>{row[source].buys}</td>
                <td>{row[source].sells}</td>
                <td>{average(row[source].averageBuy)}</td>
                <td>{average(row[source].averageSell)}</td>
              </tr>
            )),
          )}
        </tbody>
      </table>

      <details>
        <summary>{t("bt.inputs")}</summary>
        <table className="bt-table">
          <thead>
            <tr>
              <th>{t("bt.col.symbol")}</th>
              <th>{t("bt.col.sources")}</th>
              <th>{t("bt.col.adjustment")}</th>
              <th>{t("bt.col.revision")}</th>
              <th>{t("bt.col.bars")}</th>
              <th>{t("bt.col.missing")}</th>
              <th>{t("bt.col.nonFinal")}</th>
              <th>SHA-256</th>
            </tr>
          </thead>
          <tbody>
            {results.inputs.map((pin) => (
              <tr key={pin.instrument_id}>
                <td>{symbolOf(pin.instrument_id)}</td>
                <td>{pin.source_ids.join(", ")}</td>
                <td>{pin.adjustments.join(", ")}</td>
                <td>{pin.dataset_revisions.join(", ")}</td>
                <td>{pin.bar_count}</td>
                <td>{pin.missing_interval_count}</td>
                <td>{pin.non_final_bar_count}</td>
                <td title={pin.bars_sha256}>{pin.bars_sha256.slice(0, 12)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>

      {results.logs.length > 0 ? (
        <details>
          <summary>{t("bt.logs", { n: results.logs.length })}</summary>
          <pre className="bt-logs">{results.logs.join("")}</pre>
        </details>
      ) : null}
    </div>
  );
}
