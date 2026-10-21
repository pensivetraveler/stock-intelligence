import { useState } from "react";

import {
  FIB_LEVEL_CHOICES,
  MAX_FIB_LEVELS,
  type FibSettings,
} from "./drawings";
import { useI18n } from "./i18n";

/** Options of a selected Fibonacci retracement or extension, applied as they change. */
export function FibSettingsPanel({
  left,
  top,
  maxHeight,
  kind,
  settings,
  onChange,
  onClose,
}: {
  left: number;
  top: number;
  /** Keeps the panel inside the chart. */
  maxHeight?: number;
  kind: "fib" | "fibext";
  settings: FibSettings;
  onChange: (next: FibSettings) => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const custom = settings.levels.filter(
    (level) => !FIB_LEVEL_CHOICES.includes(level),
  );
  const [customText, setCustomText] = useState(custom.join(", "));
  const [customProblem, setCustomProblem] = useState(false);
  const toggle = (level: number) =>
    onChange({
      ...settings,
      levels: settings.levels.includes(level)
        ? settings.levels.filter((item) => item !== level)
        : [...settings.levels, level].sort((a, b) => a - b),
    });
  const applyCustom = (text: string) => {
    setCustomText(text);
    const parts = text
      .split(/[,\s]+/)
      .map((part) => part.trim())
      .filter(Boolean);
    const values = parts.map(Number);
    if (
      values.some(
        (value) => !Number.isFinite(value) || value < -10 || value > 10,
      )
    ) {
      setCustomProblem(true);
      return;
    }
    setCustomProblem(false);
    const standard = settings.levels.filter((level) =>
      FIB_LEVEL_CHOICES.includes(level),
    );
    onChange({
      ...settings,
      levels: [...new Set([...standard, ...values])]
        .sort((a, b) => a - b)
        .slice(0, MAX_FIB_LEVELS),
    });
  };
  const flag = (key: keyof Omit<FibSettings, "levels">, label: string) => (
    <label className="fib-flag">
      <input
        type="checkbox"
        checked={settings[key]}
        onChange={() => onChange({ ...settings, [key]: !settings[key] })}
      />
      {label}
    </label>
  );
  return (
    <div
      className="popover fib-settings"
      role="dialog"
      aria-label={t(kind === "fib" ? "fib.settings" : "fib.settingsExt")}
      style={{ left, top, maxHeight }}
      onPointerDown={(event) => event.stopPropagation()}
    >
      <strong>
        {t(kind === "fib" ? "draw.kind.fib" : "draw.kind.fibext")}
      </strong>
      <fieldset className="fib-levels">
        <legend>{t("fib.levels")}</legend>
        {FIB_LEVEL_CHOICES.map((level) => (
          <label key={level} className="fib-flag">
            <input
              type="checkbox"
              checked={settings.levels.includes(level)}
              onChange={() => toggle(level)}
            />
            {level}
          </label>
        ))}
      </fieldset>
      <label className="fib-custom">
        {t("fib.custom")}
        <input
          value={customText}
          placeholder="1.13, 2.272"
          onChange={(event) => applyCustom(event.target.value)}
        />
      </label>
      {customProblem ? (
        <p className="bt-problem" role="alert">
          {t("fib.customProblem")}
        </p>
      ) : null}
      {flag("extendLeft", t("fib.extendLeft"))}
      {flag("extendRight", t("fib.extendRight"))}
      {flag("reverse", t("fib.reverse"))}
      {flag("showPrices", t("fib.showPrices"))}
      {flag("background", t("fib.background"))}
      <button type="button" className="tb-button" onClick={onClose}>
        {t("ind.done")}
      </button>
    </div>
  );
}
