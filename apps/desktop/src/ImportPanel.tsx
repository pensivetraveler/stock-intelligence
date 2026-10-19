import type { ImportPreviewResponse } from "@siw/contracts/types";
import { useState } from "react";

import { exchangeTime } from "./chart-data";
import { translateDynamic, useI18n, type Translate } from "./i18n";
import {
  MarketApiError,
  type ImportMapping,
  type MarketApi,
  type StatementImportRequest,
} from "./market-api";

const MAPPING_FIELDS: (keyof ImportMapping)[] = [
  "executed_at",
  "date",
  "time",
  "symbol",
  "side",
  "quantity",
  "price",
  "fees",
  "broker_execution_id",
];

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

function describe(t: Translate, error: unknown): string {
  if (
    error instanceof MarketApiError &&
    error.code === "journal_storage_unconfigured"
  ) {
    return t("import.off");
  }
  return error instanceof Error ? error.message : t("common.requestFailed");
}

/**
 * Preview, review and commit a broker statement CSV. Rows land in their own
 * "statement import" ledger; unmapped columns (account numbers, notes) are never sent
 * further than the preview request and never stored.
 */
export function ImportPanel({ api, onImported }: ImportPanelProps) {
  const { t } = useI18n();
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
    done: {
      imported: number;
      rows: number;
      file: string;
      skipped: number;
    } | null;
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
      setStatus({ busy: false, error: describe(t, error), done: null });
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
        done: {
          imported: batch.imported_execution_ids.length,
          rows: batch.row_count,
          file: batch.file_name,
          skipped: batch.skipped_rows.length,
        },
      });
      setPreview(null);
      onImported();
    } catch (error) {
      setStatus({ busy: false, error: describe(t, error), done: null });
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
      <summary>{t("import.title")}</summary>
      <p className="workspace-note">{t("import.note")}</p>
      <div className="trade-fields">
        <label>
          {t("import.file")}
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
          {t("import.broker")}
          <input
            value={brokerLabel}
            maxLength={64}
            onChange={(event) => setBrokerLabel(event.target.value)}
          />
        </label>
        <label>
          {t("import.timezone")}
          <select
            value={timezone}
            onChange={(event) =>
              setTimezone(
                event.target
                  .value as StatementImportRequest["timestamp_timezone"],
              )
            }
          >
            <option value="America/New_York">{t("import.eastern")}</option>
            <option value="UTC">UTC</option>
          </select>
        </label>
        <label>
          {t("import.delimiter")}
          <select
            value={delimiter}
            onChange={(event) =>
              setDelimiter(
                event.target.value as StatementImportRequest["delimiter"],
              )
            }
          >
            <option value=",">{t("import.comma")}</option>
            <option value=";">{t("import.semicolon")}</option>
            <option value={"\t"}>{t("import.tab")}</option>
          </select>
        </label>
      </div>
      <div className="trade-actions">
        <button
          type="button"
          disabled={!file || !brokerLabel.trim() || status.busy}
          onClick={() => void runPreview(null)}
        >
          {t("import.preview")}
        </button>
        {status.error ? (
          <span className="chart-error" role="alert">
            {status.error}
          </span>
        ) : null}
        {status.done ? (
          <span role="status">{t("import.done", status.done)}</span>
        ) : null}
      </div>

      {preview ? (
        <div className="import-preview">
          <h3>{t("import.mapping")}</h3>
          <div className="trade-fields">
            {MAPPING_FIELDS.map((field) => (
              <label key={field}>
                {t(`importField.${field}`)}
                <select
                  value={preview.mapping[field] ?? ""}
                  onChange={(event) =>
                    void runPreview({
                      ...preview.mapping,
                      [field]: event.target.value || null,
                    })
                  }
                >
                  <option value="">{t("import.notMapped")}</option>
                  {preview.columns.map((column) => (
                    <option key={column} value={column}>
                      {column}
                    </option>
                  ))}
                </select>
              </label>
            ))}
          </div>
          <p className="workspace-note" role="status">
            {preview.already_imported
              ? t("import.alreadyImported")
              : t("import.summary", {
                  new: preview.summary.new,
                  exact: preview.summary.duplicate_exact,
                  possible: preview.summary.possible_duplicate,
                  invalid: preview.summary.invalid,
                })}
          </p>
          <div className="ledger">
            <table>
              <thead>
                <tr>
                  <th>{t("import.row")}</th>
                  <th>{t("import.status")}</th>
                  <th>{t("import.fill")}</th>
                  <th>{t("import.notes")}</th>
                  <th>{t("import.decision")}</th>
                </tr>
              </thead>
              <tbody>
                {preview.rows.map((row) => (
                  <tr key={row.row_number}>
                    <td>{row.row_number}</td>
                    <td>
                      <span className={`import-status import-${row.status}`}>
                        {translateDynamic(
                          t,
                          `importStatus.${row.status}`,
                          row.status,
                        )}
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
                        ...row.matches.map((match) =>
                          t("import.matches", { id: match }),
                        ),
                      ].join("; ") || "—"}
                    </td>
                    <td>
                      {row.status === "possible_duplicate" ? (
                        <select
                          aria-label={t("import.decisionFor", {
                            row: row.row_number,
                          })}
                          value={decisions[String(row.row_number)] ?? ""}
                          onChange={(event) =>
                            setDecisions({
                              ...decisions,
                              [String(row.row_number)]: event.target.value as
                                "import" | "skip",
                            })
                          }
                        >
                          <option value="">{t("import.choose")}</option>
                          <option value="import">
                            {t("import.importSeparate")}
                          </option>
                          <option value="skip">{t("import.skipSame")}</option>
                        </select>
                      ) : row.status === "new" ? (
                        t("import.import")
                      ) : (
                        t("import.skip")
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
                ? t("import.confirmNoChanges")
                : t("import.commit")}
            </button>
            {pending > 0 ? (
              <span className="workspace-note">
                {t("import.decideFirst", { n: pending })}
              </span>
            ) : null}
          </div>
        </div>
      ) : null}
    </details>
  );
}
