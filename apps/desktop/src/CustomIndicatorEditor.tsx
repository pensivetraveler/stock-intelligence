import type {
  CustomIndicator,
  CustomIndicatorPreview,
} from "@siw/contracts/types";
import { useState } from "react";

import type { CustomIndicatorApi } from "./custom-indicator-api";
import { useI18n } from "./i18n";
import type { OhlcvBar } from "./indicators";

/** The bars of the active chart, for a preview before saving. */
export interface PreviewSource {
  label: string;
  bars: OhlcvBar[];
  timeZone: string;
}

/** "...: division by zero (line 7)" -> 7, so the editor can show that line. */
function failingLine(message: string): number | null {
  const match = /\(line (\d+)\)\s*$/.exec(message);
  return match ? Number(match[1]) : null;
}

export const INDICATOR_TEMPLATE = `# A Python indicator (ADR 0023). update(ctx) runs once per bar, oldest first,
# and sees only that bar and earlier ones (reading a later bar is an error).
#   ctx.bar (open/high/low/close/volume/time), ctx.history (bars so far),
#   ctx.index, ctx.params (INPUTS as set on the chart), ctx.state (a dict kept
#   between bars), ctx.indicator(id, output=None, **params): a built-in value
#   at this bar: any of the chart's 34 built-ins (sma, rsi, macd, kc, dmi, ...).
# Return {"output": number or None, ...}. No network, files or subprocesses.

NAME = "Close vs SMA (%)"
INPUTS = {"length": 20}
OUTPUTS = ["value"]
PANE = "separate"          # "price" draws over the candles


def update(ctx):
    average = ctx.indicator("sma", length=int(ctx.params["length"]))
    if average is None:
        return None
    return {"value": (ctx.bar.close / average - 1) * 100}
`;

/** Writes or revises a Python indicator; saving checks it in the sandbox first. */
export function CustomIndicatorEditor({
  api,
  initial,
  onSaved,
  onClose,
  previewSource,
}: {
  api: CustomIndicatorApi;
  previewSource?: () => PreviewSource | null;
  /** The version being revised; a save makes the next version. */
  initial: CustomIndicator | null;
  onSaved: (saved: CustomIndicator, addToChart: boolean) => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const [code, setCode] = useState(initial?.code ?? INDICATOR_TEMPLATE);
  const [state, setState] = useState<{ busy: boolean; error: string | null }>({
    busy: false,
    error: null,
  });

  const [preview, setPreview] = useState<
    | { state: "idle" }
    | { state: "busy" }
    | { state: "done"; result: CustomIndicatorPreview; source: string }
  >({ state: "idle" });
  const errorLine = state.error ? failingLine(state.error) : null;
  const runPreview = () => {
    const source = previewSource?.();
    if (!source || source.bars.length === 0) {
      setState({ busy: false, error: t("pyind.noBars") });
      return;
    }
    setPreview({ state: "busy" });
    setState({ busy: false, error: null });
    api
      .preview(code, {}, source.bars, source.timeZone)
      .then((result) =>
        setPreview({ state: "done", result, source: source.label }),
      )
      .catch((error: unknown) => {
        setPreview({ state: "idle" });
        setState({
          busy: false,
          error:
            error instanceof Error ? error.message : t("common.requestFailed"),
        });
      });
  };

  const save = (addToChart: boolean) => {
    setState({ busy: true, error: null });
    api
      .save(code, initial?.indicator_id)
      .then((saved) => {
        setState({ busy: false, error: null });
        onSaved(saved, addToChart);
      })
      .catch((error: unknown) =>
        setState({
          busy: false,
          error:
            error instanceof Error ? error.message : t("common.requestFailed"),
        }),
      );
  };

  return (
    <div
      className="modal-backdrop"
      onPointerDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className="modal custom-indicator-editor"
        role="dialog"
        aria-label={t("pyind.title")}
        onKeyDown={(event) => {
          if (event.key === "Escape") onClose();
        }}
      >
        <header className="modal-head">
          <h2>{t("pyind.title")}</h2>
          <span className="workspace-note">
            {initial
              ? t("pyind.revising", {
                  name: initial.name,
                  version: initial.version,
                })
              : t("pyind.new")}
          </span>
          <button
            type="button"
            className="tb-button"
            aria-label={t("pyind.close")}
            onClick={onClose}
          >
            ✕
          </button>
        </header>
        <p className="workspace-note">{t("pyind.note")}</p>
        <textarea
          className="bt-code"
          aria-label={t("pyind.code")}
          spellCheck={false}
          value={code}
          onChange={(event) => setCode(event.target.value)}
        />
        {state.error ? (
          <div className="bt-problem" role="alert">
            <p>{state.error}</p>
            {errorLine !== null &&
            code.split("\n")[errorLine - 1] !== undefined ? (
              <pre className="pyind-error-line">
                {t("pyind.line", { n: errorLine })}{" "}
                {code.split("\n")[errorLine - 1]}
              </pre>
            ) : null}
          </div>
        ) : null}
        {preview.state === "done" ? (
          <div className="pyind-preview" aria-label={t("pyind.preview")}>
            <p className="workspace-note">
              {t("pyind.previewOf", {
                name: preview.result.name,
                source: preview.source,
                n: preview.result.times.length,
              })}
            </p>
            <table className="bt-table">
              <thead>
                <tr>
                  <th>{t("pyind.output")}</th>
                  <th>{t("pyind.values")}</th>
                  <th>{t("pyind.lastValues")}</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(preview.result.outputs).map(
                  ([name, values]) => (
                    <tr key={name}>
                      <td>{name}</td>
                      <td>{values.filter((value) => value !== null).length}</td>
                      <td>
                        {values
                          .slice(-5)
                          .map((value) =>
                            value === null ? "—" : Number(value.toFixed(4)),
                          )
                          .join(", ")}
                      </td>
                    </tr>
                  ),
                )}
              </tbody>
            </table>
          </div>
        ) : null}
        <p className="bt-actions">
          {previewSource ? (
            <button
              type="button"
              className="tb-button"
              disabled={state.busy || preview.state === "busy"}
              onClick={runPreview}
            >
              {preview.state === "busy"
                ? t("pyind.checking")
                : t("pyind.previewButton")}
            </button>
          ) : null}
          <button
            type="button"
            className="tb-button"
            disabled={state.busy}
            onClick={() => save(false)}
          >
            {t("pyind.save")}
          </button>
          <button
            type="button"
            className="tb-button bt-primary"
            disabled={state.busy}
            onClick={() => save(true)}
          >
            {state.busy ? t("pyind.checking") : t("pyind.saveAndAdd")}
          </button>
        </p>
      </section>
    </div>
  );
}
