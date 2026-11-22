import { useCallback, useEffect, useState } from "react";

import { useI18n } from "./i18n";
import { MarketApiError } from "./market-api";
import {
  CHOOSABLE_MODELS,
  MODEL_NAMES,
  SCRIPT_MODEL,
  type DraftAssumption,
  type StrategyPine,
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

function modelName(model: string, scriptOnly: string): string {
  return model === SCRIPT_MODEL ? scriptOnly : (MODEL_NAMES[model] ?? model);
}

/**
 * Rules strategy draft from a ready card (ADR 0056 L3). The script converts the items whose
 * slots settle a rule; the model the user picks gets the rest ("script" calls none). Every
 * card item is shown with where it went in the rules or why it could not. Saving needs a
 * draft without problems, the user's tick when items are left out, and another when a
 * scripted rule rests on a reading the source did not state.
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
  const [confirm, setConfirm] = useState(false);
  const [pine, setPine] = useState<StrategyPine | null>(null);
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
      .saveDraft(shown.draft_id, name, accept, confirm)
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
        ...(shown.needs_confirm && !confirm ? [t("td.confirmNeeded")] : []),
      ]
    : [];
  const scripted = new Set(result?.scripted ?? []);
  const assumptions: DraftAssumption[] = result?.assumptions ?? [];
  const assumption = (a: DraftAssumption) =>
    t(`td.a.${a.kind}`, {
      n: a.index + 1,
      indicator: a.indicator ?? "",
      param: a.param ?? "",
      value: a.value ?? "",
    });

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
            <option value={SCRIPT_MODEL}>{t("td.scriptOnly")}</option>
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
          {running
            ? t("td.running")
            : t(model === SCRIPT_MODEL ? "td.startFree" : "td.start")}
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
                  {modelName(d.model, t("td.scriptOnly"))} · {d.state}
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
              model: modelName(shown.model, t("td.scriptOnly")),
              cost: result.cost_usd,
              unconvertible: result.unconvertible,
              problems: result.blocking.length,
            })}
          </p>
          {scripted.size ? (
            <p className="workspace-note">
              {t("td.scriptNote", { n: scripted.size })}
            </p>
          ) : null}
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
                      entry?.status === "mapped" || entry?.status === "absent"
                        ? undefined
                        : "tc-flagged"
                    }
                  >
                    <td>{index + 1}</td>
                    <td>{item}</td>
                    <td>
                      {entry ? t(`td.s.${entry.status}`) : t("td.s.missing")}
                      {scripted.has(index) ? ` · ${t("td.scripted")}` : ""}
                    </td>
                    <td>
                      {entry?.status === "mapped"
                        ? entry.rule_paths.join(", ")
                        : entry?.status === "absent"
                          ? [entry.rule_paths.join(", "), entry.reason]
                              .filter(Boolean)
                              .join(" — ")
                          : (entry?.reason ?? "")}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {assumptions.length ? (
            <div className="td-assumptions">
              <h5>{t("td.assumptions")}</h5>
              <ul>
                {assumptions.map((a, i) => (
                  <li key={i}>{assumption(a)}</li>
                ))}
              </ul>
            </div>
          ) : null}
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
            <div className="td-saved">
              <p className="tc-ready" role="status">
                {t("td.saved", {
                  id: shown.strategy_id,
                  version: shown.strategy_version ?? 0,
                })}
              </p>
              <button
                type="button"
                className="tb-button"
                onClick={() => {
                  api
                    .pine(shown.strategy_id!, shown.strategy_version ?? 1)
                    .then(setPine)
                    .catch((reason: unknown) => setError(message(reason)));
                }}
              >
                {t("td.pine")}
              </button>
              {pine && pine.strategy_id === shown.strategy_id ? (
                <div className="td-pine">
                  <p className="workspace-note">{t("td.pineNote")}</p>
                  {pine.unsupported.length ? (
                    <ul
                      className="td-problems"
                      aria-label={t("td.pineUnsupported")}
                    >
                      {pine.unsupported.map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  ) : null}
                  <pre className="td-definition" aria-label={t("td.pine")}>
                    {pine.source}
                  </pre>
                  <button
                    type="button"
                    className="tb-button"
                    onClick={() => {
                      void navigator.clipboard?.writeText(pine.source);
                    }}
                  >
                    {t("td.pineCopy")}
                  </button>
                </div>
              ) : null}
            </div>
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
              {shown.needs_confirm ? (
                <label className="checkbox-row">
                  <input
                    type="checkbox"
                    checked={confirm}
                    onChange={(event) => setConfirm(event.target.checked)}
                  />
                  {t("td.confirm", {
                    n: new Set(assumptions.map((a) => a.index)).size,
                  })}
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
