import { exchangeTime } from "./chart-data";
import { useI18n } from "./i18n";
import type { MarketZone } from "./market-zone";
import type { StrategyReport } from "./pine-strategy";

/** Trades listed at most; the totals still count every trade. */
const MAX_LISTED = 200;

/**
 * A Pine strategy's backtest on this chart (ADR 0040): totals and its trades. These are
 * backtest fills only; nothing here is an order or goes into a ledger.
 */
export function PineStrategyReport({
  name,
  report,
  zone,
}: {
  name: string;
  report: StrategyReport;
  zone: MarketZone;
}) {
  const { t } = useI18n();
  const money = (value: string) =>
    report.currency ? `${value} ${report.currency}` : value;
  const none = t("pine.strategy.none");
  const listed = report.trades.slice(-MAX_LISTED).reverse();
  return (
    <section
      className="pine-strategy"
      aria-label={t("pine.strategy.title", { name })}
    >
      <header>
        <strong>{t("pine.strategy.title", { name })}</strong>
        <span className="workspace-note">{t("pine.strategy.note")}</span>
      </header>
      <dl className="chart-meta pine-strategy-stats">
        <div>
          <dt>{t("pine.strategy.netProfit")}</dt>
          <dd className={report.netProfit.startsWith("-") ? "down" : "up"}>
            {money(report.netProfit)} ({report.netProfitPercent}%)
          </dd>
        </div>
        <div>
          <dt>{t("pine.strategy.closed")}</dt>
          <dd>
            {report.closedTrades} ({report.winTrades} / {report.lossTrades})
          </dd>
        </div>
        <div>
          <dt>{t("pine.strategy.winRate")}</dt>
          <dd>{report.winRate === null ? none : `${report.winRate}%`}</dd>
        </div>
        <div>
          <dt>{t("pine.strategy.profitFactor")}</dt>
          <dd>{report.profitFactor ?? none}</dd>
        </div>
        <div>
          <dt>{t("pine.strategy.maxDrawdown")}</dt>
          <dd>{money(report.maxDrawdown)}</dd>
        </div>
        <div>
          <dt>{t("pine.strategy.commission")}</dt>
          <dd>{money(report.commission)}</dd>
        </div>
        <div>
          <dt>{t("pine.strategy.open")}</dt>
          <dd>
            {report.openPosition
              ? t("pine.strategy.openValue", {
                  size: report.openPosition.size,
                  price: report.openPosition.avgPrice,
                  profit: money(report.openPosition.openProfit),
                })
              : none}
          </dd>
        </div>
      </dl>
      {report.assumedFills > 0 ? (
        <p className="workspace-note" role="note">
          {t("pine.strategy.assumed", { n: report.assumedFills })}
        </p>
      ) : null}
      {report.skippedOrders > 0 ? (
        <p className="workspace-note" role="note">
          {t("pine.strategy.skipped", { n: report.skippedOrders })}
        </p>
      ) : null}
      {report.trades.length > 0 ? (
        <details>
          <summary>
            {t("pine.strategy.list", { n: report.trades.length })}
          </summary>
          <div className="pine-strategy-trades">
            <table>
              <thead>
                <tr>
                  <th>{t("pine.strategy.side")}</th>
                  <th>{t("pine.strategy.entry")}</th>
                  <th>{t("pine.strategy.exit")}</th>
                  <th>{t("pine.strategy.qty")}</th>
                  <th>{t("pine.strategy.profit")}</th>
                </tr>
              </thead>
              <tbody>
                {listed.map((trade, k) => (
                  <tr key={`${trade.entryTime}-${k}`}>
                    <td>
                      {t(`pine.strategy.${trade.direction}`)} · {trade.entryId}
                      {trade.assumed ? " *" : ""}
                    </td>
                    <td>
                      {exchangeTime(trade.entryTime, zone)} @ {trade.entryPrice}
                    </td>
                    <td>
                      {trade.exitTime === null
                        ? t("pine.strategy.stillOpen")
                        : `${exchangeTime(trade.exitTime, zone)} @ ${trade.exitPrice}`}
                    </td>
                    <td>{trade.qty}</td>
                    <td
                      className={trade.profit.startsWith("-") ? "down" : "up"}
                    >
                      {trade.profit}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      ) : null}
    </section>
  );
}
