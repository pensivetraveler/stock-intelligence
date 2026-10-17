import type {
  ImportedLedgerResponse,
  ManualLedgerResponse,
  ScenarioLedgerResponse,
} from "@siw/contracts/types";

import { exchangeTime } from "./chart-data";
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
  if (error) {
    return (
      <section className="journal" aria-label="Trade journal">
        <p className="workspace-note" role="status">
          {error}
        </p>
      </section>
    );
  }
  return (
    <section className="journal" aria-label="Trade journal">
      <div className="ledger">
        <h2>
          <span
            className="swatch"
            style={{ background: MARKER_COLORS.manual }}
          />{" "}
          Actual executions (manual, unverified)
        </h2>
        {manual && manual.positions.length > 0 ? (
          <ul className="ledger-summary">
            {manual.positions.map((position) => (
              <li key={position.instrument_id}>
                {symbolOf(position.instrument_id)}: realized{" "}
                {position.realized_pnl} USD after {position.fees} fees · open{" "}
                {position.open_quantity}
                {position.average_cost ? ` @ ${position.average_cost}` : ""}
                {position.unordered_execution_ids.length
                  ? ` · ${position.unordered_execution_ids.length} with unknown time not counted`
                  : ""}
              </li>
            ))}
          </ul>
        ) : null}
        <table>
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Side</th>
              <th>Qty</th>
              <th>Price</th>
              <th>Fill time</th>
              <th>Evidence</th>
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
                      : "unknown"}
                  </td>
                  <td>{item.evidence_status}</td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={6}>
                  No actual executions recorded. Click a candle to add one.
                </td>
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
          Statement imports (file, not broker-verified)
        </h2>
        {imported && imported.positions.length > 0 ? (
          <ul className="ledger-summary">
            {imported.positions.map((position) => (
              <li key={position.instrument_id}>
                {symbolOf(position.instrument_id)}: realized{" "}
                {position.realized_pnl} USD after {position.fees} fees · open{" "}
                {position.open_quantity}
                {position.average_cost ? ` @ ${position.average_cost}` : ""}
              </li>
            ))}
          </ul>
        ) : null}
        <table>
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Side</th>
              <th>Qty</th>
              <th>Price</th>
              <th>Fill time</th>
              <th>Source</th>
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
                    {item.broker_label} · row {item.source.row_number}
                    {item.duplicate_review.decision === "imported_after_review"
                      ? " · kept after duplicate review"
                      : ""}
                  </td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={6}>No statements imported.</td>
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
          Hypothetical scenarios (not real P&amp;L)
        </h2>
        <table>
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Direction</th>
              <th>Qty</th>
              <th>Entry → exit</th>
              <th>Net P&amp;L</th>
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
                    {item.net_pnl} USD (gross {item.gross_pnl}, costs{" "}
                    {item.costs})
                  </td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={5}>No hypothetical scenarios yet.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
