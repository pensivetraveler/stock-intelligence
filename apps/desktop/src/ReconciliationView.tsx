import type {
  ReconciliationRecord,
  TradeReconciliationResponse,
} from "@siw/contracts/types";
import { useCallback, useEffect, useState } from "react";

import { exchangeTime } from "./chart-data";
import { translateDynamic, useI18n } from "./i18n";
import type { MarketApi } from "./market-api";

type Status = ReconciliationRecord["status"];
const STATUSES: Status[] = [
  "matched",
  "conflicting",
  "broker_only",
  "manual_only",
  "imported_only",
];

interface ReconciliationViewProps {
  api: MarketApi;
  /** Changes whenever the ledgers were reloaded, so the comparison follows them. */
  revision: unknown;
  symbolOf: (instrumentId: string) => string;
}

type State =
  | { phase: "loading" }
  | { phase: "error"; message: string }
  | { phase: "ready"; result: TradeReconciliationResponse };

/** Newest records listed; the summary still counts every record. */
const RECONCILIATION_ROWS = 500;

/**
 * The broker API ledger compared with manual entries and statement imports. The API
 * computes it on request; nothing here changes or merges a ledger.
 */
export function ReconciliationView({
  api,
  revision,
  symbolOf,
}: ReconciliationViewProps) {
  const { t } = useI18n();
  const [state, setState] = useState<State>({ phase: "loading" });
  const [attentionOnly, setAttentionOnly] = useState(true);

  const load = useCallback(() => {
    let current = true;
    setState({ phase: "loading" });
    api
      .reconciliation(RECONCILIATION_ROWS)
      .then((result) => {
        if (current) setState({ phase: "ready", result });
      })
      .catch((error: unknown) => {
        if (current) {
          setState({
            phase: "error",
            message: error instanceof Error ? error.message : String(error),
          });
        }
      });
    return () => {
      current = false;
    };
  }, [api]);

  // revision only signals that the ledgers changed.
  useEffect(() => load(), [load, revision]);

  const ledgerName = (ledger: string) =>
    translateDynamic(t, `recon.ledger.${ledger}`, ledger);

  return (
    <section className="ledger reconciliation" aria-label={t("recon.title")}>
      <h2>{t("recon.title")}</h2>
      {state.phase === "ready" ? (
        <p className="workspace-note">
          {t("recon.note", {
            bps: state.result.parameters.price_tolerance_bps,
          })}
        </p>
      ) : null}
      <div className="reconciliation-toolbar">
        <label>
          {t("recon.filter")}{" "}
          <select
            value={attentionOnly ? "attention" : "all"}
            onChange={(event) =>
              setAttentionOnly(event.target.value === "attention")
            }
          >
            <option value="attention">{t("recon.filter.attention")}</option>
            <option value="all">{t("recon.filter.all")}</option>
          </select>
        </label>
        <button type="button" onClick={() => load()}>
          {t("recon.refresh")}
        </button>
      </div>
      {state.phase === "loading" ? (
        <p className="workspace-note" role="status">
          {t("recon.loading")}
        </p>
      ) : null}
      {state.phase === "error" ? (
        <p className="workspace-note" role="status">
          {t("recon.failed", { error: state.message })}
        </p>
      ) : null}
      {state.phase === "ready" ? (
        <ReconciliationTable
          result={state.result}
          attentionOnly={attentionOnly}
          ledgerName={ledgerName}
          symbolOf={symbolOf}
        />
      ) : null}
    </section>
  );
}

function ReconciliationTable({
  result,
  attentionOnly,
  ledgerName,
  symbolOf,
}: {
  result: TradeReconciliationResponse;
  attentionOnly: boolean;
  ledgerName: (ledger: string) => string;
  symbolOf: (instrumentId: string) => string;
}) {
  const { t } = useI18n();
  if (result.records.length === 0) {
    return <p className="workspace-note">{t("recon.empty")}</p>;
  }
  const shown = attentionOnly
    ? result.records.filter((record) => record.status !== "matched")
    : result.records;
  return (
    <>
      {result.record_count !== undefined &&
      result.record_count > result.records.length ? (
        <p className="workspace-note">
          {t("journal.newestOnly", {
            shown: result.records.length,
            total: result.record_count,
          })}
        </p>
      ) : null}
      <ul className="ledger-summary reconciliation-summary">
        {STATUSES.map((status) => (
          <li key={status} data-status={status}>
            {t(`recon.summary.${status}`, { n: result.summary[status] })}
          </li>
        ))}
      </ul>
      {shown.length === 0 ? (
        <p className="workspace-note">{t("recon.allClear")}</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>{t("recon.ledger")}</th>
              <th>{t("journal.symbol")}</th>
              <th>{t("journal.side")}</th>
              <th>{t("journal.qty")}</th>
              <th>{t("journal.price")}</th>
              <th>{t("recon.time")}</th>
              <th>{t("recon.status")}</th>
              <th>{t("recon.details")}</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((record) => (
              <tr
                key={`${record.ledger}:${record.execution_id}`}
                data-status={record.status}
              >
                <td>{ledgerName(record.ledger)}</td>
                <td>{symbolOf(record.instrument_id)}</td>
                <td>{record.side}</td>
                <td>{record.quantity}</td>
                <td>{record.price}</td>
                <td>
                  {record.time_utc
                    ? `${exchangeTime(record.time_utc)} (${record.time_basis})`
                    : t("journal.unknownTime")}
                </td>
                <td>{t(`recon.status.${record.status}`)}</td>
                <td className="reconciliation-details">
                  {record.counterparts.map((link) => (
                    <div key={`${link.ledger}:${link.execution_id}`}>
                      {t("recon.link", {
                        relation: t(`recon.status.${link.relation}`),
                        ledger: ledgerName(link.ledger),
                        id: link.execution_id,
                      })}
                      {link.relation === "conflicting"
                        ? ` (${t("recon.diff", {
                            qty: link.quantity_difference,
                            bps: link.price_difference_bps,
                          })})`
                        : ""}
                    </div>
                  ))}
                  {record.reasons.length ? (
                    <div className="workspace-note">
                      {record.reasons
                        .map((reason) =>
                          translateDynamic(t, `recon.reason.${reason}`, reason),
                        )
                        .join(" · ")}
                    </div>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
