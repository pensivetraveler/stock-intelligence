/**
 * Settings dialog sections that gather settings scattered over screens (디자인-siw-2 1번).
 * The original places keep their own controls as shortcuts; both read and write the same API.
 */
import { useEffect, useState } from "react";

import { useI18n, type MessageKey } from "./i18n";
import { Notice } from "./PanelParts";
import {
  MODEL_NAMES,
  type TechniqueCardsApi,
  type TechniqueSettings,
} from "./technique-cards-api";
import { applyTheme, currentTheme, THEMES, type ThemeId } from "./theme-choice";

/** Default model for technique-card extraction (the same choice as in the technique cards screen). */
export function TechniqueDefaultModel({ api }: { api: TechniqueCardsApi }) {
  const { t } = useI18n();
  const [settings, setSettings] = useState<TechniqueSettings | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    api
      .settings()
      .then((value) => {
        if (live) setSettings(value);
      })
      .catch(() => {
        if (live) setProblem(t("common.requestFailed"));
      });
    return () => {
      live = false;
    };
  }, [api, t]);
  return (
    <section className="settings-section" aria-label={t("panel.techniques")}>
      <h3>{t("panel.techniques")}</h3>
      {settings ? (
        <label className="settings-field">
          <span>{t("tc.defaultModel")}</span>
          <select
            value={settings.default_model}
            onChange={(event) =>
              api
                .setDefaultModel(event.target.value)
                .then(setSettings)
                .catch(() => setProblem(t("common.requestFailed")))
            }
          >
            {settings.default_choices.map((model) => (
              <option key={model} value={model}>
                {MODEL_NAMES[model] ?? model}
              </option>
            ))}
          </select>
        </label>
      ) : problem ? null : (
        <p className="workspace-note">{t("research.loading")}</p>
      )}
      {problem ? (
        <Notice tone="fail" title={t("parts.failed")}>
          {problem}
        </Notice>
      ) : null}
    </section>
  );
}

const THEME_TEXT: Record<ThemeId, [MessageKey, MessageKey]> = {
  a: ["theme.a.name", "theme.a.desc"],
  b: ["theme.b.name", "theme.b.desc"],
};

/** Display theme (디자인-siw-3 r3): applied at once and kept on this computer, no save button. */
export function ThemeSection() {
  const { t } = useI18n();
  const [theme, setTheme] = useState<ThemeId>(currentTheme);
  return (
    <section className="settings-section" aria-labelledby="settings-theme">
      <h3 id="settings-theme">{t("theme.title")}</h3>
      <div
        className="theme-cards"
        role="radiogroup"
        aria-labelledby="settings-theme"
      >
        {THEMES.map((id) => (
          <label key={id} className="theme-card">
            <input
              type="radio"
              name="siw-theme"
              value={id}
              checked={theme === id}
              onChange={() => {
                applyTheme(id);
                setTheme(id);
              }}
            />
            <span className="theme-mini" data-theme={id} aria-hidden="true">
              <i />
              <i />
              <i />
              <i />
            </span>
            <span className="theme-name">{t(THEME_TEXT[id][0])}</span>
            <span className="theme-desc">{t(THEME_TEXT[id][1])}</span>
          </label>
        ))}
      </div>
      <p className="workspace-note">{t("theme.note")}</p>
    </section>
  );
}
