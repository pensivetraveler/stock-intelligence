import type {
  BrokerLedgerResponse,
  ImportedLedgerResponse,
  LedgerPosition,
  ManualLedgerResponse,
  ScenarioLedgerResponse,
} from "@siw/contracts/types";

import { exchangeTime } from "./chart-data";
import { zoneOf } from "./market-zone";
import { useI18n, type Translate } from "./i18n";
import { MARKER_COLORS } from "./journal-markers";

interface JournalPanelProps {
  manual: ManualLedgerResponse | null;
  imported: ImportedLedgerResponse | null;
  broker: BrokerLedgerResponse | null;
  scenarios: ScenarioLedgerResponse | null;
  error: string | null;
  symbolOf: (instrumentId: string) => string;
}

/**
 * Four ledgers side by side and never summed: manual entries, statement imports and
 * broker-API executions (all actual fills, possibly of the same trades) and hypothetical
 * scenarios.
 */
/** An average cost for display: at most four decimals (the API keeps the exact value). */
function shortPrice(value: string): string {
  const [whole, fraction = ""] = value.split(".");
  const kept = fraction.slice(0, 4).replace(/0+$/, "");
  return kept ? `${whole}.${kept}` : whole;
}

/** A broker account as the user knows it, without any account number. */
function accountLabel(ref: string, t: Translate): string {
  if (ref.startsWith("toss-")) {
    return t("journal.accountToss", { n: ref.split("-").at(-1) ?? "?" });
  }
  if (ref.startsWith("kiwoom-")) return t("journal.accountKiwoom");
  return ref;
}

export function JournalPanel({
  manual,
  imported,
  broker,
  scenarios,
  error,
  symbolOf,
}: JournalPanelProps) {
  const { t } = useI18n();
  const brokerPosition = (position: LedgerPosition) => (
    <li key={`${position.account_ref ?? ""}|${position.instrument_id}`}>
      {position.account_ref
        ? `[${accountLabel(position.account_ref, t)}] `
        : ""}
      {position.fees_unreported_count
        ? t("journal.positionFeesUnreported", {
            symbol: symbolOf(position.instrument_id),
            currency: zoneOf(position.instrument_id).currency,
            pnl: position.realized_pnl,
            n: position.fees_unreported_count,
            open: position.open_quantity,
          })
        : t("journal.position", {
            symbol: symbolOf(position.instrument_id),
            currency: zoneOf(position.instrument_id).currency,
            pnl: position.realized_pnl,
            fees: position.fees,
            open: position.open_quantity,
          })}
      {position.average_cost ? ` @ ${shortPrice(position.average_cost)}` : ""}
    </li>
  );
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
                  currency: zoneOf(position.instrument_id).currency,
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
                  currency: zoneOf(position.instrument_id).currency,
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
            style={{ background: MARKER_COLORS.broker }}
          />{" "}
          {t("journal.brokerTitle")}
        </h2>
        <p className="workspace-note">{t("journal.brokerNote")}</p>
        {broker &&
        broker.execution_count !== undefined &&
        broker.execution_count > broker.executions.length ? (
          <p className="workspace-note">
            {t("journal.newestOnly", {
              shown: broker.executions.length,
              total: broker.execution_count,
            })}
          </p>
        ) : null}
        {broker && broker.positions.length > 0 ? (
          <>
            <ul className="ledger-summary">
              {broker.positions
                .filter((position) => Number(position.open_quantity) !== 0)
                .map(brokerPosition)}
            </ul>
            <details>
              <summary>
                {t("journal.closedPositions", {
                  n: broker.positions.filter(
                    (position) => Number(position.open_quantity) === 0,
                  ).length,
                })}
              </summary>
              <ul className="ledger-summary">
                {broker.positions
                  .filter((position) => Number(position.open_quantity) === 0)
                  .map(brokerPosition)}
              </ul>
            </details>
          </>
        ) : null}
        <table>
          <thead>
            <tr>
              <th>{t("journal.symbol")}</th>
              <th>{t("journal.side")}</th>
              <th>{t("journal.qty")}</th>
              <th>{t("journal.avgPrice")}</th>
              <th>{t("journal.lastFill")}</th>
              <th>{t("journal.fees")}</th>
            </tr>
          </thead>
          <tbody>
            {broker?.executions.length ? (
              broker.executions.map((item) => (
                <tr key={item.execution_id}>
                  <td>{item.symbol}</td>
                  <td>{item.side}</td>
                  <td>{item.quantity}</td>
                  <td>{item.price}</td>
                  <td>
                    {item.executed_at_utc
                      ? exchangeTime(item.executed_at_utc)
                      : t("journal.unknownTime")}
                  </td>
                  <td>{item.fees}</td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={6}>{t("journal.noBroker")}</td>
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
