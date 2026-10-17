import type { ImportPreviewResponse } from "@siw/contracts/types";
import { useState } from "react";

import { exchangeTime } from "./chart-data";
import {
  MarketApiError,
  type ImportMapping,
  type MarketApi,
  type StatementImportRequest,
} from "./market-api";

const FIELD_LABELS: Record<keyof ImportMapping, string> = {
  executed_at: "Execution timestamp",
  date: "Date (if separate)",
  time: "Time (if separate)",
  symbol: "Symbol",
  side: "Side",
  quantity: "Quantity",
  price: "Price",
  fees: "Fees",
  broker_execution_id: "Broker execution ID",
};

const STATUS_LABELS: Record<string, string> = {
  new: "New",
  duplicate_exact: "Already imported",
  possible_duplicate: "Possible duplicate",
  invalid: "Invalid",
};

/** Reads a chosen file as UTF-8 text (FileReader works in every webview and test DOM). */
function readText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () =>
      reject(reader.error ?? new Error("Could not read the file"));
    reader.readAsText(file, "utf-8");
  });
}

interface ImportPanelProps {
  api: MarketApi;
  onImported: () => void;
}

function describe(error: unknown): string {
  if (
    error instanceof MarketApiError &&
    error.code === "journal_storage_unconfigured"
  ) {
    return "Imports are off: start the API with SIW_DATA_DIR set. Nothing was saved.";
  }
  return error instanceof Error ? error.message : "Request failed";
}

/**
 * Preview, review and commit a broker statement CSV. Rows land in their own
 * "statement import" ledger; unmapped columns (account numbers, notes) are never sent
 * further than the preview request and never stored.
 */
export function ImportPanel({ api, onImported }: ImportPanelProps) {
  const [file, setFile] = useState<{ name: string; content: string } | null>(
    null,
  );
  const [brokerLabel, setBrokerLabel] = useState("");
  const [timezone, setTimezone] =
    useState<StatementImportRequest["timestamp_timezone"]>("America/New_York");
  const [delimiter, setDelimiter] =
    useState<StatementImportRequest["delimiter"]>(",");
  const [preview, setPreview] = useState<ImportPreviewResponse | null>(null);
  const [decisions, setDecisions] = useState<Record<string, "import" | "skip">>(
    {},
  );
  const [status, setStatus] = useState<{
    busy: boolean;
    error: string | null;
    done: string | null;
  }>({ busy: false, error: null, done: null });

  const request = (
    mapping: ImportMapping | null,
  ): StatementImportRequest | null =>
    file
      ? {
          file_name: file.name,
          content: file.content,
          broker_label: brokerLabel,
          timestamp_timezone: timezone,
          delimiter,
          mapping,
        }
      : null;

  const runPreview = async (mapping: ImportMapping | null) => {
    const body = request(mapping);
    if (!body) return;
    setStatus({ busy: true, error: null, done: null });
    try {
      setPreview(await api.previewImport(body));
      setDecisions({});
      setStatus({ busy: false, error: null, done: null });
    } catch (error) {
      setStatus({ busy: false, error: describe(error), done: null });
    }
  };

  const commit = async () => {
    const body = request(preview?.mapping ?? null);
    if (!body || !preview) return;
    setStatus({ busy: true, error: null, done: null });
    try {
      const batch = await api.commitImport({ ...body, decisions });
      setStatus({
        busy: false,
        error: null,
        done: `Imported ${batch.imported_execution_ids.length} of ${batch.row_count} rows from ${batch.file_name}; skipped ${batch.skipped_rows.length}.`,
      });
      setPreview(null);
      onImported();
    } catch (error) {
      setStatus({ busy: false, error: describe(error), done: null });
    }
  };

  const pending =
    preview?.rows.filter(
      (row) =>
        row.status === "possible_duplicate" &&
        !decisions[String(row.row_number)],
    ).length ?? 0;

  return (
    <details className="import-panel">
      <summary>Import broker statement (CSV)</summary>
      <p className="workspace-note">
        Rows become a separate “statement import” ledger, never merged with
        manual entries. Only mapped columns are read; the file itself and
        unmapped columns such as account numbers are not stored.
      </p>
      <div className="trade-fields">
        <label>
          CSV file
          <input
            type="file"
            accept=".csv,text/csv"
            onChange={async (event) => {
              const chosen = event.target.files?.[0];
              setPreview(null);
              setStatus({ busy: false, error: null, done: null });
              setFile(
                chosen
                  ? { name: chosen.name, content: await readText(chosen) }
                  : null,
              );
            }}
          />
        </label>
        <label>
          Broker label (a name, not an account number)
          <input
            value={brokerLabel}
            maxLength={64}
            onChange={(event) => setBrokerLabel(event.target.value)}
          />
        </label>
        <label>
          Times without an offset are in
          <select
            value={timezone}
            onChange={(event) =>
              setTimezone(
                event.target
                  .value as StatementImportRequest["timestamp_timezone"],
              )
            }
          >
            <option value="America/New_York">US Eastern (ET)</option>
            <option value="UTC">UTC</option>
          </select>
        </label>
        <label>
          Delimiter
          <select
            value={delimiter}
            onChange={(event) =>
              setDelimiter(
                event.target.value as StatementImportRequest["delimiter"],
              )
            }
          >
            <option value=",">Comma</option>
            <option value=";">Semicolon</option>
            <option value={"\t"}>Tab</option>
          </select>
        </label>
      </div>
      <div className="trade-actions">
        <button
          type="button"
          disabled={!file || !brokerLabel.trim() || status.busy}
          onClick={() => void runPreview(null)}
        >
          Preview
        </button>
        {status.error ? (
          <span className="chart-error" role="alert">
            {status.error}
          </span>
        ) : null}
        {status.done ? <span role="status">{status.done}</span> : null}
      </div>

      {preview ? (
        <div className="import-preview">
          <h3>Column mapping</h3>
          <div className="trade-fields">
            {(Object.keys(FIELD_LABELS) as (keyof ImportMapping)[]).map(
              (field) => (
                <label key={field}>
                  {FIELD_LABELS[field]}
                  <select
                    value={preview.mapping[field] ?? ""}
                    onChange={(event) =>
                      void runPreview({
                        ...preview.mapping,
                        [field]: event.target.value || null,
                      })
                    }
                  >
                    <option value="">(not mapped)</option>
                    {preview.columns.map((column) => (
                      <option key={column} value={column}>
                        {column}
                      </option>
                    ))}
                  </select>
                </label>
              ),
            )}
          </div>
          <p className="workspace-note" role="status">
            {preview.already_imported
              ? "This exact file was already imported; committing again changes nothing."
              : `${preview.summary.new} new · ${preview.summary.duplicate_exact} already imported · ${preview.summary.possible_duplicate} possible duplicates · ${preview.summary.invalid} invalid`}
          </p>
          <div className="ledger">
            <table>
              <thead>
                <tr>
                  <th>Row</th>
                  <th>Status</th>
                  <th>Fill</th>
                  <th>Notes</th>
                  <th>Decision</th>
                </tr>
              </thead>
              <tbody>
                {preview.rows.map((row) => (
                  <tr key={row.row_number}>
                    <td>{row.row_number}</td>
                    <td>
                      <span className={`import-status import-${row.status}`}>
                        {STATUS_LABELS[row.status]}
                      </span>
                    </td>
                    <td>
                      {row.execution
                        ? `${row.execution.side} ${row.execution.quantity} @ ${row.execution.price} · ${exchangeTime(row.execution.executed_at_utc)}`
                        : "—"}
                    </td>
                    <td className="import-notes">
                      {[
                        ...row.errors,
                        ...row.matches.map((match) => `matches ${match}`),
                      ].join("; ") || "—"}
                    </td>
                    <td>
                      {row.status === "possible_duplicate" ? (
                        <select
                          aria-label={`Decision for row ${row.row_number}`}
                          value={decisions[String(row.row_number)] ?? ""}
                          onChange={(event) =>
                            setDecisions({
                              ...decisions,
                              [String(row.row_number)]: event.target.value as
                                "import" | "skip",
                            })
                          }
                        >
                          <option value="">Choose…</option>
                          <option value="import">
                            Import (a separate fill)
                          </option>
                          <option value="skip">Skip (same fill)</option>
                        </select>
                      ) : row.status === "new" ? (
                        "Import"
                      ) : (
                        "Skip"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="trade-actions">
            <button
              type="button"
              disabled={status.busy || pending > 0}
              onClick={() => void commit()}
            >
              {preview.already_imported
                ? "Confirm (no changes)"
                : "Commit import"}
            </button>
            {pending > 0 ? (
              <span className="workspace-note">
                Decide on {pending} possible duplicate(s) first.
              </span>
            ) : null}
          </div>
        </div>
      ) : null}
    </details>
  );
}
