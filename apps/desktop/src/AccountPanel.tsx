import type {
  BrokerAccountSnapshot,
  BrokerSyncResult,
} from "@siw/contracts/types";
import { useCallback, useEffect, useState } from "react";

import { exchangeTime } from "./chart-data";
import { useI18n } from "./i18n";
import { MarketApiError, type MarketApi } from "./market-api";

interface AccountPanelProps {
  api: MarketApi;
  /** Called after a sync so the journal shows the new broker executions. */
  onSynced: () => void;
}

const dash = (value: string | null | undefined) => value ?? "—";

/**
 * Read-only view of every Toss account (holdings, buying power, commission rates, open
 * orders and conditional orders) and on-demand syncs of filled Toss and Kiwoom orders into
 * the broker ledger. Nothing here can place, change or cancel an order.
 */
export function AccountPanel({ api, onSynced }: AccountPanelProps) {
  const { t } = useI18n();
  const [snapshot, setSnapshot] = useState<BrokerAccountSnapshot | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [sync, setSync] = useState<
    | { state: "idle" }
    | { state: "busy" }
    | { state: "done"; result: BrokerSyncResult }
    | { state: "failed"; error: unknown }
  >({ state: "idle" });
  const [sellable, setSellable] = useState<Record<string, string>>({});
  const [kiwoomSince, setKiwoomSince] = useState("");

  const load = useCallback(() => {
    let current = true;
    setError(null);
    api
      .brokerAccount()
      .then((result) => current && setSnapshot(result))
      .catch((reason: unknown) => current && setError(reason));
    return () => {
      current = false;
    };
  }, [api]);

  useEffect(() => load(), [load]);

  const runSync = (broker: "toss" | "kiwoom" | "kiwoom-kr") => {
    setSync({ state: "busy" });
    (broker === "kiwoom"
      ? api.syncKiwoom(kiwoomSince || undefined)
      : broker === "kiwoom-kr"
        ? api.syncKiwoomDomestic(kiwoomSince || undefined)
        : api.syncBroker()
    )
      .then((result) => {
        setSync({ state: "done", result });
        onSynced();
      })
      .catch((reason: unknown) => setSync({ state: "failed", error: reason }));
  };

  const describe = (reason: unknown) =>
    reason instanceof MarketApiError &&
    reason.code === "quote_source_unavailable"
      ? t("account.noKey")
      : t("account.failed", {
          error: reason instanceof Error ? reason.message : String(reason),
        });

  return (
    <section className="quote-panel" aria-label={t("account.title")}>
      <h2>{t("account.title")}</h2>
      <p className="workspace-note">{t("account.readOnly")}</p>
      <p>
        <button
          type="button"
          disabled={sync.state === "busy"}
          onClick={() => runSync("toss")}
        >
          {sync.state === "busy" ? t("account.syncing") : t("account.sync")}
        </button>{" "}
        <button
          type="button"
          disabled={sync.state === "busy"}
          onClick={() => runSync("kiwoom")}
        >
          {t("account.syncKiwoom")}
        </button>{" "}
        <button
          type="button"
          disabled={sync.state === "busy"}
          onClick={() => runSync("kiwoom-kr")}
        >
          {t("account.syncKiwoomDomestic")}
        </button>{" "}
        <label>
          {t("account.kiwoomSince")}{" "}
          <input
            type="date"
            value={kiwoomSince}
            onChange={(event) => setKiwoomSince(event.target.value)}
          />
        </label>{" "}
        <button type="button" className="secondary" onClick={() => load()}>
          {t("account.refresh")}
        </button>
      </p>
      {sync.state === "done" ? (
        <p className="workspace-note" role="status">
          {t("account.synced", {
            new: sync.result.stored_new,
            same: sync.result.already_stored,
            notFilled: sync.result.skipped.not_filled,
            currency: sync.result.skipped.unsupported_currency,
            unknown: sync.result.skipped.unknown_instrument,
            conflicts: sync.result.conflicts.length
              ? t("account.conflicts", { n: sync.result.conflicts.length })
              : "",
          })}
          {sync.result.broker_id === "kiwoom"
            ? t("account.kiwoomNote", {
                n: sync.result.skipped.time_unresolved ?? 0,
              })
            : null}
        </p>
      ) : null}
      {sync.state === "failed" ? (
        <p className="chart-error" role="alert">
          {describe(sync.error)}
        </p>
      ) : null}
      {error ? (
        <p className="chart-error" role="alert">
          {describe(error)}
        </p>
      ) : null}
      {snapshot === null && !error ? (
        <p className="workspace-note">{t("account.loading")}</p>
      ) : null}
      {snapshot && snapshot.accounts.length === 0 ? (
        <p className="workspace-note">{t("account.none")}</p>
      ) : null}
      {snapshot?.accounts.map((account) => (
        <div className="ledger" key={account.account_ref}>
          <h3>
            {t("account.heading", {
              suffix: account.account_suffix ?? "????",
              type: account.account_type,
            })}
          </h3>
          {account.summary ? (
            <p>
              {t("account.summary", {
                usd: dash(account.summary.market_value.usd),
                krw: dash(account.summary.market_value.krw),
                pnlUsd: dash(account.summary.profit_loss.usd),
                pnlKrw: dash(account.summary.profit_loss.krw),
                rate: dash(account.summary.profit_loss_rate),
              })}
            </p>
          ) : null}
          <p>
            {t("account.buyingPower", {
              usd: dash(account.buying_power.usd),
              krw: dash(account.buying_power.krw),
            })}
          </p>
          <h3>{t("account.holdings")}</h3>
          {account.holdings.length === 0 ? (
            <p className="workspace-note">{t("account.noHoldings")}</p>
          ) : (
            <table className="quote-trades">
              <thead>
                <tr>
                  <th>{t("journal.symbol")}</th>
                  <th>{t("journal.qty")}</th>
                  <th>{t("journal.avgPrice")}</th>
                  <th>{t("quote.price")}</th>
                  <th>P&L</th>
                  <th>{t("account.sellable")}</th>
                </tr>
              </thead>
              <tbody>
                {account.holdings.map((holding) => {
                  const key = `${account.account_seq}:${holding.symbol}`;
                  return (
                    <tr key={holding.symbol}>
                      <td title={holding.name ?? undefined}>
                        {holding.symbol}
                      </td>
                      <td>{dash(holding.quantity)}</td>
                      <td>{dash(holding.average_purchase_price)}</td>
                      <td>{dash(holding.last_price)}</td>
                      <td>
                        {dash(holding.profit_loss)} {holding.currency ?? ""}
                      </td>
                      <td>
                        {sellable[key] ?? (
                          <button
                            type="button"
                            className="secondary"
                            onClick={() =>
                              void api
                                .sellableQuantity(
                                  account.account_seq,
                                  holding.symbol,
                                )
                                .then((result) =>
                                  setSellable((previous) => ({
                                    ...previous,
                                    [key]: result.sellable_quantity,
                                  })),
                                )
                                .catch(() =>
                                  setSellable((previous) => ({
                                    ...previous,
                                    [key]: "—",
                                  })),
                                )
                            }
                          >
                            {t("account.check")}
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
          <h3>{t("account.openOrders")}</h3>
          {account.open_orders.length === 0 ? (
            <p className="workspace-note">{t("account.noOpenOrders")}</p>
          ) : (
            <table className="quote-trades">
              <tbody>
                {account.open_orders.map((order) => (
                  <tr key={order.broker_order_id}>
                    <td>{order.symbol}</td>
                    <td>{order.side}</td>
                    <td>{order.status}</td>
                    <td>
                      {dash(order.filled_quantity)} / {dash(order.quantity)}
                    </td>
                    <td>{dash(order.price)}</td>
                    <td>
                      {order.ordered_at_utc
                        ? exchangeTime(order.ordered_at_utc)
                        : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <h3>{t("account.conditional")}</h3>
          {account.open_conditional_orders.length === 0 ? (
            <p className="workspace-note">{t("account.noConditional")}</p>
          ) : (
            <table className="quote-trades">
              <tbody>
                {account.open_conditional_orders.map((order) => (
                  <tr key={order.conditional_order_id}>
                    <td>{order.symbol}</td>
                    <td>{order.type}</td>
                    <td>{order.status}</td>
                    <td>{dash(order.quantity)}</td>
                    <td>{dash(order.expire_date)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <h3>{t("account.commissions")}</h3>
          <p>
            {account.commission_rates
              .map(
                (rate) =>
                  `${rate.market_country} ${dash(rate.rate)}${rate.end_date ? ` (~${rate.end_date})` : ""}`,
              )
              .join(" · ") || "—"}
          </p>
          {account.errors.length ? (
            <p className="chart-error">
              {t("account.partErrors", { errors: account.errors.join(" · ") })}
            </p>
          ) : null}
        </div>
      ))}
    </section>
  );
}
