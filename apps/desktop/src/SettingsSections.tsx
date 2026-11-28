/**
 * Settings dialog sections that gather settings scattered over screens (디자인-siw-2 1번).
 * The original places keep their own controls as shortcuts; both read and write the same API.
 */
import { useEffect, useState } from "react";

import { useI18n } from "./i18n";
import { Notice } from "./PanelParts";
import {
  MODEL_NAMES,
  type TechniqueCardsApi,
  type TechniqueSettings,
} from "./technique-cards-api";

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
