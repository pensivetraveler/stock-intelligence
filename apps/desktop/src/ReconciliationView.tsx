import type {
  ReconciliationRecord,
  TradeReconciliationResponse,
} from "@siw/contracts/types";
import { useCallback, useEffect, useState } from "react";

import { exchangeTime } from "./chart-data";
import { translateDynamic, useI18n } from "./i18n";
import type { MarketApi } from "./market-api";
import { zoneOf } from "./market-zone";

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

/** "attention" hides matched rows (and broker-only rows while nothing is compared). */
type StatusChoice = "attention" | "all" | Status;
type Layout = "instrument" | "day" | "list";

/** Newest records listed; the summary still counts every record. */
const RECONCILIATION_ROWS = 500;

/** True when the manual and statement ledgers are both empty: nothing to compare. */
function nothingToCompare(result: TradeReconciliationResponse): boolean {
  const counts = result.ledger_counts;
  return counts
    ? counts.manual_actual === 0 && counts.statement_imported === 0
    : false;
}

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
  const [statusChoice, setStatusChoice] = useState<StatusChoice>("attention");
  // null until chosen: per instrument while nothing can be compared, else the list.
  const [layout, setLayout] = useState<Layout | null>(null);
  const [instrumentId, setInstrumentId] = useState("");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [instruments, setInstruments] = useState<string[]>([]);

  // "attention" and "all" filter the same answer here; one status asks the API.
  const serverStatus =
    statusChoice === "attention" || statusChoice === "all"
      ? undefined
      : statusChoice;

  const load = useCallback(() => {
    let current = true;
    setState({ phase: "loading" });
    api
      .reconciliation({
        limit: RECONCILIATION_ROWS,
        instrumentId: instrumentId || undefined,
        fromDate: fromDate || undefined,
        toDate: toDate || undefined,
        status: serverStatus,
      })
      .then((result) => {
        if (!current) return;
        setState({ phase: "ready", result });
        // The instrument choice lists what an unfiltered answer holds.
        if (!result.instrument_id && result.breakdown) {
          setInstruments(
            result.breakdown.by_instrument.map((row) => row.instrument_id),
          );
        }
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
  }, [api, instrumentId, fromDate, toDate, serverStatus]);

  // revision only signals that the ledgers changed.
  useEffect(() => load(), [load, revision]);

  const ledgerName = (ledger: string) =>
    translateDynamic(t, `recon.ledger.${ledger}`, ledger);
  const idle = state.phase === "ready" && nothingToCompare(state.result);
  const shownLayout: Layout = layout ?? (idle ? "instrument" : "list");

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
      {idle ? (
        <p className="workspace-note reconciliation-idle" role="note">
          {t("recon.nothingToCompare")}
        </p>
      ) : null}
      <div className="reconciliation-toolbar">
        <label>
          {t("recon.layout")}{" "}
          <select
            value={shownLayout}
            onChange={(event) => setLayout(event.target.value as Layout)}
          >
            <option value="instrument">{t("recon.layout.instrument")}</option>
            <option value="day">{t("recon.layout.day")}</option>
            <option value="list">{t("recon.layout.list")}</option>
          </select>
        </label>
        <label>
          {t("recon.instrument")}{" "}
          <select
            value={instrumentId}
            onChange={(event) => setInstrumentId(event.target.value)}
          >
            <option value="">{t("recon.instrument.all")}</option>
            {[
              ...new Set([
                ...instruments,
                ...(instrumentId ? [instrumentId] : []),
              ]),
            ]
              .sort((a, b) => symbolOf(a).localeCompare(symbolOf(b)))
              .map((id) => (
                <option key={id} value={id}>
                  {symbolOf(id)}
                </option>
              ))}
          </select>
        </label>
        <label>
          {t("recon.from")}{" "}
          <input
            type="date"
            value={fromDate}
            max={toDate || undefined}
            onChange={(event) => setFromDate(event.target.value)}
          />
        </label>
        <label>
          {t("recon.to")}{" "}
          <input
            type="date"
            value={toDate}
            min={fromDate || undefined}
            onChange={(event) => setToDate(event.target.value)}
          />
        </label>
        <label>
          {t("recon.filter")}{" "}
          <select
            value={statusChoice}
            onChange={(event) =>
              setStatusChoice(event.target.value as StatusChoice)
            }
          >
            <option value="attention">{t("recon.filter.attention")}</option>
            <option value="all">{t("recon.filter.all")}</option>
            {STATUSES.map((status) => (
              <option key={status} value={status}>
                {t(`recon.status.${status}`)}
              </option>
            ))}
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
        shownLayout === "list" ? (
          <ReconciliationTable
            result={state.result}
            statusChoice={statusChoice}
            ledgerName={ledgerName}
            symbolOf={symbolOf}
          />
        ) : (
          <ReconciliationCounts
            result={state.result}
            layout={shownLayout}
            symbolOf={symbolOf}
            onPick={(pick) => {
              if (pick.instrumentId) setInstrumentId(pick.instrumentId);
              if (pick.date) {
                setFromDate(pick.date);
                setToDate(pick.date);
              }
              setLayout("list");
            }}
          />
        )
      ) : null}
    </section>
  );
}

function StatusSummary({ result }: { result: TradeReconciliationResponse }) {
  const { t } = useI18n();
  return (
    <ul className="ledger-summary reconciliation-summary">
      {STATUSES.map((status) => (
        <li key={status} data-status={status}>
          {t(`recon.summary.${status}`, { n: result.summary[status] })}
        </li>
      ))}
    </ul>
  );
}

function total(counts: Record<Status, number>): number {
  return STATUSES.reduce((sum, status) => sum + counts[status], 0);
}

/** Status counts per instrument or per exchange-local day; a row opens its list. */
function ReconciliationCounts({
  result,
  layout,
  symbolOf,
  onPick,
}: {
  result: TradeReconciliationResponse;
  layout: "instrument" | "day";
  symbolOf: (instrumentId: string) => string;
  onPick: (pick: { instrumentId?: string; date?: string }) => void;
}) {
  const { t } = useI18n();
  const rows: {
    key: string;
    label: string;
    counts: Record<Status, number>;
    pick: { instrumentId?: string; date?: string };
  }[] =
    layout === "instrument"
      ? [...(result.breakdown?.by_instrument ?? [])]
          // The busiest instruments first.
          .sort((a, b) => total(b.counts) - total(a.counts))
          .map((row) => ({
            key: row.instrument_id,
            label: symbolOf(row.instrument_id),
            counts: row.counts,
            pick: { instrumentId: row.instrument_id },
          }))
      : (result.breakdown?.by_day ?? []).map((row) => ({
          key: row.date ?? "unknown",
          label: row.date ?? t("journal.unknownTime"),
          counts: row.counts,
          pick: row.date ? { date: row.date } : {},
        }));
  // Only statuses that occur get a column; the summary above still lists all.
  const columns = STATUSES.filter((status) => result.summary[status] > 0);
  if (rows.length === 0) {
    return <p className="workspace-note">{t("recon.empty")}</p>;
  }
  return (
    <>
      <StatusSummary result={result} />
      <table className="reconciliation-counts">
        <thead>
          <tr>
            <th>
              {layout === "instrument" ? t("journal.symbol") : t("recon.day")}
            </th>
            <th>{t("recon.total")}</th>
            {columns.map((status) => (
              <th key={status}>{t(`recon.status.${status}`)}</th>
            ))}
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key}>
              <td>{row.label}</td>
              <td>{total(row.counts)}</td>
              {columns.map((status) => (
                <td key={status} data-status={status}>
                  {row.counts[status] || ""}
                </td>
              ))}
              <td>
                {row.pick.instrumentId || row.pick.date ? (
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => onPick(row.pick)}
                  >
                    {t("recon.openList")}
                  </button>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function ReconciliationTable({
  result,
  statusChoice,
  ledgerName,
  symbolOf,
}: {
  result: TradeReconciliationResponse;
  statusChoice: StatusChoice;
  ledgerName: (ledger: string) => string;
  symbolOf: (instrumentId: string) => string;
}) {
  const { t } = useI18n();
  if (result.records.length === 0) {
    return <p className="workspace-note">{t("recon.empty")}</p>;
  }
  const idle = nothingToCompare(result);
  const shown =
    statusChoice === "attention"
      ? result.records.filter(
          (record) =>
            record.status !== "matched" &&
            !(idle && record.status === "broker_only"),
        )
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
      <StatusSummary result={result} />
      {shown.length === 0 ? (
        <p className="workspace-note">
          {idle ? t("recon.brokerOnlyHidden") : t("recon.allClear")}
        </p>
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
                    ? `${exchangeTime(record.time_utc, zoneOf(record.instrument_id))} (${record.time_basis})`
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
