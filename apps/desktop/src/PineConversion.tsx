import { useState } from "react";

import { useI18n } from "./i18n";
import type {
  IndicatorContext,
  IndicatorInstance,
  OhlcvBar,
} from "./indicators";
import {
  convertInstance,
  convertPine,
  pineParamsFor,
  revertInstance,
} from "./pine-convert";

/**
 * A Pine indicator's settings: whether it can run as a formula (ADR 0058), and a button
 * that converts it after checking both draw the same on the chart's bars.
 */
export function PineConversionNote({
  instance,
  bars,
  context,
  onConvert,
}: {
  instance: IndicatorInstance;
  bars: OhlcvBar[];
  context: IndicatorContext;
  onConvert: (next: IndicatorInstance) => void;
}) {
  const { t } = useI18n();
  const [problem, setProblem] = useState<string | null>(null);
  const conversion = convertPine(instance.pine ?? "");
  if (!conversion.ok) {
    return (
      <p className="workspace-note pine-conversion">
        {t("pc.notConverted", {
          reason: conversion.reason,
          line: conversion.line,
        })}
      </p>
    );
  }
  return (
    <div className="pine-conversion">
      <p className="bt-actions">
        <button
          type="button"
          className="tb-button"
          onClick={() => {
            const result = convertInstance(instance, bars, context);
            if ("instance" in result) onConvert(result.instance);
            else setProblem(result.reason);
          }}
        >
          {t("pc.convert")}
        </button>
      </p>
      <p
        className={problem ? "bt-problem" : "workspace-note"}
        role={problem ? "alert" : undefined}
      >
        {problem
          ? t("pc.convertFailed", { reason: problem })
          : t("pc.canConvert")}
      </p>
    </div>
  );
}

/** A converted formula's settings: where it came from, the original, and the way back. */
export function ConvertedNote({
  instance,
  onRevert,
}: {
  instance: IndicatorInstance;
  onRevert: (next: IndicatorInstance) => void;
}) {
  const { t } = useI18n();
  const origin = instance.converted;
  if (!origin) return null;
  return (
    <div className="pine-conversion">
      <p className="workspace-note">
        {t(origin.edited ? "pc.fromPineEdited" : "pc.fromPine", {
          bars: origin.bars.toLocaleString(),
          converter: origin.converter,
        })}
      </p>
      <details>
        <summary>{t("pc.original")}</summary>
        <pre className="formula-example">{origin.pine}</pre>
        <p className="workspace-note">SHA-256 {origin.sha256}</p>
      </details>
      <p className="bt-actions">
        <button
          type="button"
          className="tb-button"
          onClick={() =>
            onRevert(revertInstance(instance, pineParamsFor(instance)))
          }
        >
          {t("pc.revert")}
        </button>
      </p>
    </div>
  );
}
