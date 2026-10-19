import type {
  ImportedLedgerResponse,
  ManualLedgerResponse,
  ScenarioLedgerResponse,
} from "@siw/contracts/types";

import { exchangeTime } from "./chart-data";
import { useI18n } from "./i18n";
import { MARKER_COLORS } from "./journal-markers";

interface JournalPanelProps {
  manual: ManualLedgerResponse | null;
  imported: ImportedLedgerResponse | null;
  scenarios: ScenarioLedgerResponse | null;
  error: string | null;
  symbolOf: (instrumentId: string) => string;
}

/**
 * Three ledgers side by side and never summed: manual entries, statement imports (both
 * actual fills, possibly of the same trades) and hypothetical scenarios.
 */
export function JournalPanel({
  manual,
  imported,
  scenarios,
  error,
  symbolOf,
}: JournalPanelProps) {
  const { t } = useI18n();
  if (error) {
    return (
      <section className="journal" aria-label={t("journal.label")}>
        <p className="workspace-note" role="status">
          {error}
        </p>
      </section>
    );
  }
  return (
    <section className="journal" aria-label={t("journal.label")}>
      <div className="ledger">
        <h2>
          <span
            className="swatch"
            style={{ background: MARKER_COLORS.manual }}
          />{" "}
          {t("journal.manualTitle")}
        </h2>
        {manual && manual.positions.length > 0 ? (
          <ul className="ledger-summary">
            {manual.positions.map((position) => (
              <li key={position.instrument_id}>
                {t("journal.position", {
                  symbol: symbolOf(position.instrument_id),
                  pnl: position.realized_pnl,
                  fees: position.fees,
                  open: position.open_quantity,
                })}
                {position.average_cost ? ` @ ${position.average_cost}` : ""}
                {position.unordered_execution_ids.length
                  ? t("journal.unordered", {
                      n: position.unordered_execution_ids.length,
                    })
                  : ""}
              </li>
            ))}
          </ul>
        ) : null}
        <table>
          <thead>
            <tr>
              <th>{t("journal.symbol")}</th>
              <th>{t("journal.side")}</th>
              <th>{t("journal.qty")}</th>
              <th>{t("journal.price")}</th>
              <th>{t("journal.fillTime")}</th>
              <th>{t("journal.evidence")}</th>
            </tr>
          </thead>
          <tbody>
            {manual?.executions.length ? (
              manual.executions.map((item) => (
                <tr key={item.execution_id}>
                  <td>{symbolOf(item.instrument_id)}</td>
                  <td>{item.side}</td>
                  <td>{item.quantity}</td>
                  <td>{item.price}</td>
                  <td>
                    {item.executed_at_utc
                      ? `${exchangeTime(item.executed_at_utc)} (${item.time_precision})`
                      : t("journal.unknownTime")}
                  </td>
                  <td>{item.evidence_status}</td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={6}>{t("journal.noManual")}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="ledger">
        <h2>
          <span
            className="swatch"
            style={{ background: MARKER_COLORS.imported }}
          />{" "}
          {t("journal.importedTitle")}
        </h2>
        {imported && imported.positions.length > 0 ? (
          <ul className="ledger-summary">
            {imported.positions.map((position) => (
              <li key={position.instrument_id}>
                {t("journal.position", {
                  symbol: symbolOf(position.instrument_id),
                  pnl: position.realized_pnl,
                  fees: position.fees,
                  open: position.open_quantity,
                })}
                {position.average_cost ? ` @ ${position.average_cost}` : ""}
              </li>
            ))}
          </ul>
        ) : null}
        <table>
          <thead>
            <tr>
              <th>{t("journal.symbol")}</th>
              <th>{t("journal.side")}</th>
              <th>{t("journal.qty")}</th>
              <th>{t("journal.price")}</th>
              <th>{t("journal.fillTime")}</th>
              <th>{t("journal.source")}</th>
            </tr>
          </thead>
          <tbody>
            {imported?.executions.length ? (
              imported.executions.map((item) => (
                <tr key={item.execution_id}>
                  <td>{symbolOf(item.instrument_id)}</td>
                  <td>{item.side}</td>
                  <td>{item.quantity}</td>
                  <td>{item.price}</td>
                  <td>{exchangeTime(item.executed_at_utc)}</td>
                  <td>
                    {t("journal.row", {
                      broker: item.broker_label,
                      row: item.source.row_number,
                    })}
                    {item.duplicate_review.decision === "imported_after_review"
                      ? t("journal.keptAfterReview")
                      : ""}
                  </td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={6}>{t("journal.noImported")}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="ledger">
        <h2>
          <span
            className="swatch"
            style={{ background: MARKER_COLORS.hypothetical }}
          />{" "}
          {t("journal.hypoTitle")}
        </h2>
        <table>
          <thead>
            <tr>
              <th>{t("journal.symbol")}</th>
              <th>{t("journal.direction")}</th>
              <th>{t("journal.qty")}</th>
              <th>{t("journal.entryExit")}</th>
              <th>{t("journal.netPnl")}</th>
            </tr>
          </thead>
          <tbody>
            {scenarios?.scenarios.length ? (
              scenarios.scenarios.map((item) => (
                <tr
                  key={`${item.scenario.scenario_id}:${item.scenario.version}`}
                >
                  <td>{symbolOf(item.scenario.instrument_id)}</td>
                  <td>{item.scenario.direction}</td>
                  <td>{item.scenario.quantity}</td>
                  <td>
                    {item.scenario.entry_price} → {item.scenario.exit_price} (
                    {exchangeTime(item.scenario.entry_interval_start_utc)}–
                    {exchangeTime(item.scenario.exit_interval_start_utc)})
                  </td>
                  <td>
                    {t("journal.pnl", {
                      net: item.net_pnl,
                      gross: item.gross_pnl,
                      costs: item.costs,
                    })}
                  </td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={5}>{t("journal.noScenarios")}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
