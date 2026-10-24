import { useState } from "react";

import type { CustomIndicator } from "@siw/contracts/types";

import { useI18n } from "./i18n";
import {
  INDICATOR_BY_ID,
  INDICATORS,
  type IndicatorCategory,
  type IndicatorDefinition,
} from "./indicators";

interface IndicatorDialogProps {
  /** Names of the chart the indicators go to, e.g. "AAPL · 5m". */
  target: string;
  onAdd: (definition: IndicatorDefinition) => void;
  onClose: () => void;
  /** The latest version of each Python indicator (ADR 0023), when they are offered. */
  custom?: CustomIndicator[];
  /** Open the Python editor: a new indicator (null) or the next version of one. */
  onEditCustom?: (item: CustomIndicator | null) => void;
  /** Open the formula editor for a new formula indicator. */
  onNewFormula?: () => void;
  /** Open the Pine Script editor for a new Pine indicator. */
  onNewPine?: () => void;
}

type Category = IndicatorCategory | "all" | "formula" | "pine";

const CATEGORIES: Category[] = [
  "all",
  "trend",
  "momentum",
  "volatility",
  "volume",
  "formula",
  "pine",
  "custom",
];

/** Searchable list of the built-in indicators; picking one adds it to the active chart. */
export function IndicatorDialog({
  target,
  onAdd,
  onClose,
  custom,
  onEditCustom,
  onNewFormula,
  onNewPine,
}: IndicatorDialogProps) {
  const { t, language } = useI18n();
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<Category>("all");
  const needle = query.trim().toLowerCase();
  const customDefinitions = (custom ?? []).flatMap((item) => {
    const definition = INDICATOR_BY_ID.get(item.type);
    return definition ? [{ item, definition }] : [];
  });
  const shown = [
    ...INDICATORS,
    ...customDefinitions.map((c) => c.definition),
  ].filter(
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
            {CATEGORIES.filter(
              (item) =>
                (item !== "custom" || custom) &&
                (item !== "formula" || onNewFormula) &&
                (item !== "pine" || onNewPine),
            ).map((item) => (
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
            {onNewFormula && category === "formula" ? (
              <li>
                <button type="button" onClick={onNewFormula}>
                  <span>{t("formula.newButton")}</span>
                  <small>{t("formula.newHint")}</small>
                </button>
              </li>
            ) : null}
            {onNewPine && category === "pine" ? (
              <li>
                <button type="button" onClick={onNewPine}>
                  <span>{t("pine.newButton")}</span>
                  <small>{t("pine.newHint")}</small>
                </button>
              </li>
            ) : null}
            {custom && onEditCustom && category === "custom" ? (
              <li>
                <button type="button" onClick={() => onEditCustom(null)}>
                  <span>{t("pyind.newButton")}</span>
                  <small>{t("pyind.newHint")}</small>
                </button>
              </li>
            ) : null}
            {shown.map((item) => {
              const own = customDefinitions.find((c) => c.definition === item);
              return (
                <li
                  key={item.id}
                  className={own ? "indicator-custom" : undefined}
                >
                  <button type="button" onClick={() => onAdd(item)}>
                    <span>{item.name[language]}</span>
                    <small>
                      {item.short} ·{" "}
                      {t(item.pane === "price" ? "ind.onPrice" : "ind.ownPane")}
                    </small>
                  </button>
                  {own && onEditCustom ? (
                    <button
                      type="button"
                      className="tb-button"
                      aria-label={t("pyind.edit", { name: own.item.name })}
                      onClick={() => onEditCustom(own.item)}
                    >
                      ✎
                    </button>
                  ) : null}
                </li>
              );
            })}
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
