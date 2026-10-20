import { useState } from "react";

import { useI18n } from "./i18n";
import {
  INDICATORS,
  type IndicatorCategory,
  type IndicatorDefinition,
} from "./indicators";

interface IndicatorDialogProps {
  /** Names of the chart the indicators go to, e.g. "AAPL · 5m". */
  target: string;
  onAdd: (definition: IndicatorDefinition) => void;
  onClose: () => void;
}

const CATEGORIES: (IndicatorCategory | "all")[] = [
  "all",
  "trend",
  "momentum",
  "volatility",
  "volume",
];

/** Searchable list of the built-in indicators; picking one adds it to the active chart. */
export function IndicatorDialog({
  target,
  onAdd,
  onClose,
}: IndicatorDialogProps) {
  const { t, language } = useI18n();
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<IndicatorCategory | "all">("all");
  const needle = query.trim().toLowerCase();
  const shown = INDICATORS.filter(
    (item) =>
      (category === "all" || item.category === category) &&
      (needle === "" ||
        [item.name.en, item.name.ko, item.short].some((text) =>
          text.toLowerCase().includes(needle),
        )),
  );

  return (
    <div
      className="modal-backdrop"
      onPointerDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className="modal indicator-dialog"
        role="dialog"
        aria-label={t("ind.title")}
        onKeyDown={(event) => {
          if (event.key === "Escape") onClose();
        }}
      >
        <header className="modal-head">
          <h2>{t("ind.title")}</h2>
          <span className="workspace-note">{t("ind.target", { target })}</span>
          <button
            type="button"
            className="tb-button"
            aria-label={t("ind.close")}
            onClick={onClose}
          >
            ✕
          </button>
        </header>
        <input
          autoFocus
          className="modal-search"
          value={query}
          placeholder={t("ind.search")}
          aria-label={t("ind.search")}
          onChange={(event) => setQuery(event.target.value)}
        />
        <div className="indicator-body">
          <nav
            className="indicator-categories"
            aria-label={t("ind.categories")}
          >
            {CATEGORIES.map((item) => (
              <button
                key={item}
                type="button"
                className="tb-button"
                aria-pressed={category === item}
                onClick={() => setCategory(item)}
              >
                {t(`ind.cat.${item}`)}
              </button>
            ))}
          </nav>
          <ul className="indicator-list">
            {shown.map((item) => (
              <li key={item.id}>
                <button type="button" onClick={() => onAdd(item)}>
                  <span>{item.name[language]}</span>
                  <small>
                    {item.short} ·{" "}
                    {t(item.pane === "price" ? "ind.onPrice" : "ind.ownPane")}
                  </small>
                </button>
              </li>
            ))}
            {shown.length === 0 ? (
              <li className="symbol-empty">{t("ind.none")}</li>
            ) : null}
          </ul>
        </div>
        <p className="workspace-note">{t("ind.note")}</p>
      </section>
    </div>
  );
}
