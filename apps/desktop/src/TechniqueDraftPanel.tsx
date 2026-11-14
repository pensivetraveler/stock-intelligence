import { useCallback, useEffect, useState } from "react";

import { useI18n } from "./i18n";
import { MarketApiError } from "./market-api";
import {
  CHOOSABLE_MODELS,
  MODEL_NAMES,
  type TechniqueCard,
  type TechniqueCardsApi,
  type TechniqueDraft,
} from "./technique-cards-api";

interface Props {
  api: TechniqueCardsApi;
  card: TechniqueCard;
  pollMs: number;
  /** The default model setting; the user may pick another for this draft. */
  defaultModel: string;
}

function message(error: unknown): string {
  if (error instanceof MarketApiError)
    return `${error.message} (${error.code})`;
  return error instanceof Error ? error.message : String(error);
}

/**
 * Rules strategy draft from a ready card (ADR 0056 L3). The user picks the model and presses
 * the button; every card item is shown with where it went in the rules or why it could not.
 * Saving needs a draft without problems, and the user's tick when items are left out.
 */
export function TechniqueDraftPanel({
  api,
  card,
  pollMs,
  defaultModel,
}: Props) {
  const { t } = useI18n();
  const [model, setModel] = useState<string>(defaultModel);
  const [drafts, setDrafts] = useState<TechniqueDraft[]>([]);
  const [shown, setShown] = useState<TechniqueDraft | null>(null);
  const [running, setRunning] = useState<string | null>(null);
  const [name, setName] = useState(card.title.slice(0, 120));
  const [accept, setAccept] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .drafts(card.card_id)
      .then((list) => {
        setDrafts(list);
        setShown((current) =>
          current
            ? (list.find((d) => d.draft_id === current.draft_id) ?? current)
            : (list[0] ?? null),
        );
        const open = list.find((d) => d.state === "running");
        if (open) setRunning(open.draft_id);
      })
      .catch((reason: unknown) => setError(message(reason)));
  }, [api, card.card_id]);
  useEffect(() => load(), [load]);

  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => {
      api
        .draft(running)
        .then((found) => {
          if (found.state === "running") return;
          setRunning(null);
          setShown(found);
          if (found.state === "failed") {
            setError(t("td.failed", { reason: found.error }));
          }
          load();
        })
        .catch((reason: unknown) => {
          setRunning(null);
          setError(message(reason));
        });
    }, pollMs);
    return () => window.clearInterval(timer);
  }, [api, load, pollMs, running, t]);

  const start = () => {
    setError(null);
    api
      .startDraft(card.card_id, model)
      .then((job) => setRunning(job.draft_id))
      .catch((reason: unknown) => setError(message(reason)));
  };

  const save = () => {
    if (!shown) return;
    api
      .saveDraft(shown.draft_id, name, accept)
      .then((saved) => {
        setShown(saved);
        setError(null);
        load();
      })
      .catch((reason: unknown) => setError(message(reason)));
  };

  const result = shown?.result ?? null;
  const blocks = shown
    ? [
        ...shown.save_blocks,
        ...(shown.needs_accept && !accept
          ? [t("td.tickNeeded", { n: result?.unconvertible ?? 0 })]
          : []),
      ]
    : [];

  return (
    <section className="td-panel" aria-label={t("td.title")}>
      <h4>{t("td.title")}</h4>
      <p className="workspace-note">{t("td.note")}</p>
      {error ? (
        <p className="form-error" role="alert">
          {error}
        </p>
      ) : null}
      <div className="td-start">
        <label>
          {t("td.model")}
          <select
            value={model}
            onChange={(event) => setModel(event.target.value)}
          >
            {CHOOSABLE_MODELS.map((choice) => (
              <option key={choice} value={choice}>
                {choice === defaultModel
                  ? t("tc.assigned", { model: MODEL_NAMES[choice] ?? choice })
                  : (MODEL_NAMES[choice] ?? choice)}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="primary-button"
          disabled={running !== null}
          onClick={start}
        >
          {running ? t("td.running") : t("td.start")}
        </button>
        {drafts.length > 1 ? (
          <label>
            {t("td.earlier")}
            <select
              value={shown?.draft_id ?? ""}
              onChange={(event) =>
                setShown(
                  drafts.find((d) => d.draft_id === event.target.value) ?? null,
                )
              }
            >
              {drafts.map((d) => (
                <option key={d.draft_id} value={d.draft_id}>
                  {d.model} · {d.state}
                  {d.strategy_id
                    ? ` · ${d.strategy_id} v${d.strategy_version}`
                    : ""}
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </div>

      {shown && result ? (
        <div className="td-result">
          <p className="workspace-note">
            {t("td.summary", {
              model: shown.model,
              cost: result.cost_usd,
              unconvertible: result.unconvertible,
              problems: result.blocking.length,
            })}
          </p>
          <table className="tc-conditions">
            <thead>
              <tr>
                <th>#</th>
                <th>{t("td.item")}</th>
                <th>{t("td.status")}</th>
                <th>{t("td.where")}</th>
              </tr>
            </thead>
            <tbody>
              {result.items.map((item, index) => {
                const entry = result.conversion.mapping.find(
                  (m) => m.index === index,
                );
                return (
                  <tr
                    key={index}
                    className={
                      entry?.status === "mapped" ? undefined : "tc-flagged"
                    }
                  >
                    <td>{index + 1}</td>
                    <td>{item}</td>
                    <td>
                      {entry ? t(`td.s.${entry.status}`) : t("td.s.missing")}
                    </td>
                    <td>
                      {entry?.status === "mapped"
                        ? entry.rule_paths.join(", ")
                        : (entry?.reason ?? "")}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {result.blocking.length ? (
            <ul className="td-problems">
              {result.blocking.map((problem) => (
                <li key={problem}>{problem}</li>
              ))}
            </ul>
          ) : null}
          {result.conversion.notes ? (
            <p className="workspace-note">{result.conversion.notes}</p>
          ) : null}
          {result.conversion.definition ? (
            <pre className="td-definition">
              {JSON.stringify(result.conversion.definition, null, 2)}
            </pre>
          ) : null}
          {shown.strategy_id ? (
            <p className="tc-ready" role="status">
              {t("td.saved", {
                id: shown.strategy_id,
                version: shown.strategy_version ?? 0,
              })}
            </p>
          ) : (
            <div className="td-save">
              {shown.needs_accept ? (
                <label className="checkbox-row">
                  <input
                    type="checkbox"
                    checked={accept}
                    onChange={(event) => setAccept(event.target.checked)}
                  />
                  {t("td.accept", { n: result.unconvertible })}
                </label>
              ) : null}
              <label>
                {t("td.name")}
                <input
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                />
              </label>
              {blocks.length ? (
                <p className="tc-blocked">
                  {t("td.blocked", { blocks: blocks.join(" · ") })}
                </p>
              ) : null}
              <button
                type="button"
                className="primary-button"
                disabled={blocks.length > 0 || !name.trim()}
                onClick={save}
              >
                {t("td.save")}
              </button>
            </div>
          )}
        </div>
      ) : null}
    </section>
  );
}
