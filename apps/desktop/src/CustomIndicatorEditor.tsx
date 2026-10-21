import type { CustomIndicator } from "@siw/contracts/types";
import { useState } from "react";

import type { CustomIndicatorApi } from "./custom-indicator-api";
import { useI18n } from "./i18n";

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
}: {
  api: CustomIndicatorApi;
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
          <p className="bt-problem" role="alert">
            {state.error}
          </p>
        ) : null}
        <p className="bt-actions">
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
