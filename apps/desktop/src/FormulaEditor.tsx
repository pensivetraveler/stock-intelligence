import { useState } from "react";

import {
  FORMULA_FIELDS,
  FORMULA_FUNCTIONS,
  FormulaError,
  parseFormula,
} from "./formula";
import { useI18n } from "./i18n";

export interface FormulaValue {
  label: string;
  formula: string;
  pane: "price" | "separate";
}

const FORMULA_EXAMPLE: FormulaValue = {
  label: "EMA-SMA / ATR",
  formula: "(ema(close, 10) - sma(close, 30)) / atr(14)",
  pane: "separate",
};

/** Name, expression and pane of a formula indicator, checked as it is typed. */
export function FormulaFields({
  initial,
  submitLabel,
  onSubmit,
  onCancel,
}: {
  initial: FormulaValue;
  submitLabel: string;
  onSubmit: (value: FormulaValue) => void;
  onCancel: () => void;
}) {
  const { t } = useI18n();
  const [value, setValue] = useState(initial);
  let problem: string | null = null;
  try {
    parseFormula(value.formula);
  } catch (error) {
    problem =
      error instanceof FormulaError
        ? t("formula.error", {
            message: error.message,
            column: error.position + 1,
          })
        : String(error);
  }
  const label = value.label.trim();
  return (
    <div className="formula-fields">
      <label>
        {t("formula.name")}
        <input
          value={value.label}
          maxLength={40}
          onChange={(event) =>
            setValue({ ...value, label: event.target.value })
          }
        />
      </label>
      <label>
        {t("formula.expression")}
        <textarea
          className="formula-text"
          spellCheck={false}
          maxLength={500}
          value={value.formula}
          onChange={(event) =>
            setValue({ ...value, formula: event.target.value })
          }
        />
      </label>
      <label>
        {t("formula.pane")}
        <select
          value={value.pane}
          onChange={(event) =>
            setValue({
              ...value,
              pane: event.target.value as FormulaValue["pane"],
            })
          }
        >
          <option value="separate">{t("ind.ownPane")}</option>
          <option value="price">{t("ind.onPrice")}</option>
        </select>
      </label>
      <p
        className={problem ? "bt-problem" : "workspace-note"}
        role={problem ? "alert" : undefined}
      >
        {problem ?? t("formula.ok")}
      </p>
      <details>
        <summary>{t("formula.help")}</summary>
        <p className="workspace-note">
          {t("formula.helpText", {
            fields: FORMULA_FIELDS.join(", "),
            functions: FORMULA_FUNCTIONS.join(", "),
          })}
        </p>
      </details>
      <p className="bt-actions">
        <button type="button" className="tb-button" onClick={onCancel}>
          {t("formula.cancel")}
        </button>
        <button
          type="button"
          className="tb-button bt-primary"
          disabled={problem !== null || label === ""}
          onClick={() => onSubmit({ ...value, label })}
        >
          {submitLabel}
        </button>
      </p>
    </div>
  );
}

/** A new formula indicator for the active chart. */
export function FormulaDialog({
  target,
  onAdd,
  onClose,
}: {
  target: string;
  onAdd: (value: FormulaValue) => void;
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
        className="modal formula-dialog"
        role="dialog"
        aria-label={t("formula.title")}
        onKeyDown={(event) => {
          if (event.key === "Escape") onClose();
        }}
      >
        <header className="modal-head">
          <h2>{t("formula.title")}</h2>
          <span className="workspace-note">{t("ind.target", { target })}</span>
        </header>
        <p className="workspace-note">{t("formula.note")}</p>
        <FormulaFields
          initial={FORMULA_EXAMPLE}
          submitLabel={t("formula.add")}
          onSubmit={onAdd}
          onCancel={onClose}
        />
      </section>
    </div>
  );
}
