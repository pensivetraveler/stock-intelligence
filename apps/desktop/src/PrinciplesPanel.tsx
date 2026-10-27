import type { ReviewPrinciples } from "@siw/contracts/types";
import { useEffect, useState } from "react";

import { useI18n } from "./i18n";
import type { PrincipleInput, ReviewApi } from "./review-api";

const KINDS = [
  "stop_loss_pct",
  "take_profit_pct",
  "max_position_pct",
  "no_entry_first_minutes",
  "max_gap_chase_pct",
  "max_trades_per_day",
  "text",
] as const;

/**
 * The investor's trading principles (ADR 0042, step 3a). Written in their own words,
 * turned into items by Claude, edited, then saved as a new version. Nothing is saved
 * before the investor presses save.
 */
export function PrinciplesPanel({
  api,
  strategies,
  onSaved,
}: {
  api: ReviewApi;
  strategies: { strategy_id: string; name: string }[];
  /** Called after a new version is saved, so checks shown elsewhere reload. */
  onSaved?: () => void;
}) {
  const { t, language } = useI18n();
  const [loaded, setLoaded] = useState<ReviewPrinciples | null>(null);
  const [text, setText] = useState("");
  const [items, setItems] = useState<PrincipleInput[] | null>(null);
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const fail = (error: unknown) =>
    setProblem(
      error instanceof Error ? error.message : t("common.requestFailed"),
    );

  useEffect(() => {
    let current = true;
    api
      .principles()
      .then((document) => {
        if (!current) return;
        setLoaded(document);
        setText(document.current?.source_text ?? "");
      })
      .catch((error: unknown) => current && fail(error));
    return () => {
      current = false;
    };
    // `fail` only formats the error; reloading on its identity would loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api]);

  const scopeName = (scope: string) =>
    scope === "shared"
      ? t("pr.shared")
      : (strategies.find((s) => s.strategy_id === scope)?.name ?? scope);

  const convert = () => {
    setBusy(true);
    setProblem(null);
    api
      .draftPrinciples(text, language === "ko" ? "ko" : "en")
      .then((draft) => {
        setItems(draft.items);
        setNotes(draft.notes);
      })
      .catch(fail)
      .finally(() => setBusy(false));
  };

  const save = () => {
    if (!items) return;
    setBusy(true);
    setProblem(null);
    api
      .savePrinciples(items, text)
      .then((document) => {
        setLoaded(document);
        setItems(null);
        setNotes("");
        onSaved?.();
      })
      .catch(fail)
      .finally(() => setBusy(false));
  };

  const edit = (index: number, change: Partial<PrincipleInput>) =>
    items &&
    setItems(
      items.map((item, i) => (i === index ? { ...item, ...change } : item)),
    );

  const current = loaded?.current;
  return (
    <section className="rv-settings rv-principles" aria-label={t("pr.title")}>
      <h4>
        {t("pr.title")}
        {current ? (
          <span className="workspace-note">
            {" "}
            {t("pr.version", {
              version: current.version,
              at: current.created_at_utc,
            })}
          </span>
        ) : null}
      </h4>
      {problem ? (
        <p className="bt-problem" role="alert">
          {problem}
        </p>
      ) : null}
      {current && !items ? (
        <ul className="rv-principle-list">
          {current.items.map((item) => (
            <li key={item.item_id}>
              <span className="workspace-note">{scopeName(item.scope)} · </span>
              {t(`pr.kind.${item.kind}`)}
              {item.value !== null ? ` ${item.value}` : ""}
              {item.text ? ` — ${item.text}` : ""}
            </li>
          ))}
        </ul>
      ) : null}
      {!current && !items ? (
        <p className="workspace-note">{t("pr.none")}</p>
      ) : null}
      <label className="rv-question">
        {t("pr.write")}
        <textarea
          value={text}
          maxLength={8000}
          placeholder={t("pr.placeholder")}
          onChange={(event) => setText(event.target.value)}
        />
      </label>
      <p className="bt-actions">
        <button
          type="button"
          className="tb-button"
          disabled={busy || !text.trim()}
          onClick={convert}
        >
          {busy && !items ? t("pr.converting") : t("pr.convert")}
        </button>
        <button
          type="button"
          className="tb-button"
          disabled={busy}
          onClick={() =>
            setItems([
              ...(items ?? current?.items ?? []),
              { scope: "shared", kind: "text", value: null, text: "" },
            ])
          }
        >
          {t("pr.addItem")}
        </button>
      </p>
      {items ? (
        <div className="rv-draft" aria-label={t("pr.proposed")}>
          <p className="workspace-note">{t("pr.proposedNote")}</p>
          {notes ? <p className="workspace-note">{notes}</p> : null}
          {items.map((item, index) => (
            <div key={index} className="rv-principle-row">
              <select
                aria-label={t("pr.scope")}
                value={item.scope}
                onChange={(event) => edit(index, { scope: event.target.value })}
              >
                <option value="shared">{t("pr.shared")}</option>
                {strategies.map((s) => (
                  <option key={s.strategy_id} value={s.strategy_id}>
                    {s.name}
                  </option>
                ))}
              </select>
              <select
                aria-label={t("pr.kind")}
                value={item.kind}
                onChange={(event) =>
                  edit(index, {
                    kind: event.target.value,
                    value: event.target.value === "text" ? null : item.value,
                  })
                }
              >
                {KINDS.map((kind) => (
                  <option key={kind} value={kind}>
                    {t(`pr.kind.${kind}`)}
                  </option>
                ))}
              </select>
              {item.kind !== "text" ? (
                <input
                  aria-label={t("pr.value")}
                  className="bt-param"
                  inputMode="decimal"
                  value={item.value ?? ""}
                  onChange={(event) =>
                    edit(index, { value: event.target.value })
                  }
                />
              ) : null}
              <input
                aria-label={t("pr.text")}
                value={item.text}
                onChange={(event) => edit(index, { text: event.target.value })}
              />
              <button
                type="button"
                className="tb-button"
                aria-label={t("pr.remove")}
                onClick={() => setItems(items.filter((_, i) => i !== index))}
              >
                ✕
              </button>
            </div>
          ))}
          <p className="bt-actions">
            <button
              type="button"
              className="tb-button"
              disabled={busy}
              onClick={save}
            >
              {t("pr.save")}
            </button>
            <button
              type="button"
              className="tb-button"
              disabled={busy}
              onClick={() => setItems(null)}
            >
              {t("rp.cancel")}
            </button>
          </p>
        </div>
      ) : null}
    </section>
  );
}
