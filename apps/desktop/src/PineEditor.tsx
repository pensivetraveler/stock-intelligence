import { useMemo, useState } from "react";

import { useI18n } from "./i18n";
import {
  compilePine,
  MAX_PINE_LENGTH,
  PINE_EXAMPLE,
  PINE_FUNCTIONS,
  PineError,
  type PineProgram,
  runPine,
} from "./pine";

/** A few made-up bars to try the script on, so a misspelt name shows before adding. */
const TRIAL_BARS = Array.from({ length: 30 }, (_, i) => {
  const close = 100 + Math.sin(i / 3) * 5;
  return {
    time: 1_700_000_000 + i * 60,
    open: close - 0.5,
    high: close + 1,
    low: close - 1,
    close,
    volume: 1000,
  };
});

/** A Pine script, checked as it is typed: errors point at their line and column. */
export function PineFields({
  initial,
  submitLabel,
  onSubmit,
  onCancel,
}: {
  initial: string;
  submitLabel: string;
  onSubmit: (source: string) => void;
  onCancel: () => void;
}) {
  const { t } = useI18n();
  const [source, setSource] = useState(initial);
  const checked = useMemo((): PineProgram | string => {
    try {
      const program = compilePine(source);
      runPine(program, TRIAL_BARS, {});
      return program;
    } catch (error) {
      return error instanceof PineError
        ? t("pine.error", {
            line: error.line,
            column: error.column,
            message: error.message,
          })
        : String(error);
    }
  }, [source, t]);
  return (
    <div className="formula-fields pine-fields">
      <label>
        {t("pine.code")}
        <textarea
          className="formula-text pine-text"
          spellCheck={false}
          rows={14}
          maxLength={MAX_PINE_LENGTH}
          value={source}
          onChange={(event) => setSource(event.target.value)}
          onKeyDown={(event) => {
            // Tab indents (Pine blocks are indented), instead of leaving the box.
            if (event.key !== "Tab") return;
            event.preventDefault();
            const box = event.currentTarget;
            const start = box.selectionStart;
            const next = `${source.slice(0, start)}    ${source.slice(box.selectionEnd)}`;
            setSource(next);
            requestAnimationFrame(() => {
              box.selectionStart = box.selectionEnd = start + 4;
            });
          }}
        />
      </label>
      {typeof checked === "string" ? (
        <p className="chart-error" role="alert">
          {checked}
        </p>
      ) : (
        <p className="workspace-note">
          {t("pine.summary", {
            title: checked.title,
            pane: t(checked.overlay ? "pine.overlay" : "pine.pane"),
            inputs: checked.inputs.length,
            plots: checked.plots.map((plot) => plot.title).join(", "),
          })}
        </p>
      )}
      <details>
        <summary>{t("formula.help")}</summary>
        <p className="workspace-note">
          {t("pine.helpText", { functions: PINE_FUNCTIONS.join(", ") })}
        </p>
      </details>
      <p className="bt-actions">
        <button type="button" className="tb-button" onClick={onCancel}>
          {t("formula.cancel")}
        </button>
        <button
          type="button"
          className="tb-button bt-primary"
          disabled={typeof checked === "string"}
          onClick={() => onSubmit(source)}
        >
          {submitLabel}
        </button>
      </p>
    </div>
  );
}

/** A dialog that adds a new Pine Script indicator to the active chart. */
export function PineDialog({
  target,
  onAdd,
  onClose,
}: {
  target: string;
  onAdd: (source: string) => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  return (
    <div
      className="modal-backdrop"
      onPointerDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className="modal formula-dialog pine-dialog"
        role="dialog"
        aria-label={t("pine.title")}
        onKeyDown={(event) => {
          if (event.key === "Escape") onClose();
        }}
      >
        <header className="modal-head">
          <h2>{t("pine.title")}</h2>
          <span className="workspace-note">{t("ind.target", { target })}</span>
        </header>
        <p className="workspace-note">{t("pine.note")}</p>
        <PineFields
          initial={PINE_EXAMPLE}
          submitLabel={t("formula.add")}
          onSubmit={onAdd}
          onCancel={onClose}
        />
      </section>
    </div>
  );
}
