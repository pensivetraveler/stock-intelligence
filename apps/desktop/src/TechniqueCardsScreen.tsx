import { useCallback, useEffect, useMemo, useState } from "react";

import { useI18n } from "./i18n";
import { TechniqueDraftPanel } from "./TechniqueDraftPanel";
import { MarketApiError } from "./market-api";
import {
  slotsLabel,
  type AddedCondition,
  type CardCondition,
  type CardSummary,
  type Flag,
  type Resolution,
  type TechniqueCard,
  type TechniqueCardsApi,
  type TechniquePreview,
  type TechniqueSettings,
  MODEL_NAMES,
  type TechniqueSource,
} from "./technique-cards-api";

const POLL_MS = 3_000;
const CHOOSABLE_DEFAULT = "google/gemini-4-argon";

interface Props {
  api: TechniqueCardsApi;
  onClose: () => void;
  /** Tests shorten the job poll. */
  pollMs?: number;
}

function message(error: unknown): string {
  if (error instanceof MarketApiError)
    return `${error.message} (${error.code})`;
  return error instanceof Error ? error.message : String(error);
}

/** Numbers the user may change on a flagged condition; the rest of the slots stay. */
type NumberSlot = "left.period" | "left.value" | "right.period" | "right.value";
const NUMBER_SLOTS: NumberSlot[] = [
  "left.period",
  "left.value",
  "right.period",
  "right.value",
];

function slotValue(condition: CardCondition, slot: NumberSlot): number | null {
  const [side, key] = slot.split(".") as ["left" | "right", "period" | "value"];
  return condition[side][key];
}

function withSlot(
  condition: CardCondition,
  slot: NumberSlot,
  raw: string,
): CardCondition {
  const [side, key] = slot.split(".") as ["left" | "right", "period" | "value"];
  const parsed = raw.trim() === "" ? null : Number(raw);
  return {
    ...condition,
    [side]: {
      ...condition[side],
      [key]: Number.isFinite(parsed) ? parsed : null,
    },
  };
}

/** Why the card is not ready, from the saved checks and resolutions (numbers from 1). */
function blockedText(
  card: TechniqueCard,
  t: ReturnType<typeof useI18n>["t"],
): string {
  const parts: string[] = [];
  if (card.withdrawn) parts.push(t("tc.withdrawn"));
  if (card.user_withdrawn_at) parts.push(t("tc.userWithdrawn"));
  if (!["still_usable", "conditional"].includes(card.recheck_outcome)) {
    parts.push(t("tc.recheckState", { outcome: card.recheck_outcome }));
  }
  const open = card.checks.conditions
    .filter(
      (entry) =>
        entry.flags.length &&
        !(String(entry.index) in card.resolutions.conditions),
    )
    .map((entry) => `#${entry.index + 1}`);
  if (open.length) parts.push(t("tc.unresolved", { list: open.join(", ") }));
  return t("tc.blocked", { blocks: parts.join(" · ") });
}

/**
 * Technique sources from the library and the cards made from them (ADR 0056 L1·L2).
 * The user previews what is sent, presses extract, then confirms, edits or drops every
 * condition the script flagged; only then is the card ready for the rule builder (L3).
 */
export function TechniqueCardsScreen({
  api,
  onClose,
  pollMs = POLL_MS,
}: Props) {
  const { t } = useI18n();
  const [sources, setSources] = useState<TechniqueSource[] | null>(null);
  const [cards, setCards] = useState<CardSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [preview, setPreview] = useState<TechniquePreview | null>(null);
  const [previewing, setPreviewing] = useState<string | null>(null);
  const [job, setJob] = useState<{ id: string; item: string } | null>(null);
  const [card, setCard] = useState<TechniqueCard | null>(null);
  const [draft, setDraft] = useState<Record<string, Resolution>>({});
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [added, setAdded] = useState<AddedCondition[]>([]);
  const [onlyUncited, setOnlyUncited] = useState(false);
  const [settings, setSettings] = useState<TechniqueSettings | null>(null);
  // With a card already for the source: "version" (new version of its newest card) or
  // "card" (a separate card); the user must pick before extracting (ADR 0056, 2026-11-23).
  const [choice, setChoice] = useState<"version" | "card" | null>(null);

  const load = useCallback(
    (refresh = false) => {
      setLoading(true);
      Promise.all([api.sources(refresh), api.cards(), api.settings()])
        .then(([nextSources, nextCards, nextSettings]) => {
          setSources(nextSources);
          setCards(nextCards);
          setSettings(nextSettings);
          setError(null);
        })
        .catch((reason: unknown) => setError(message(reason)))
        .finally(() => setLoading(false));
    },
    [api],
  );
  useEffect(() => load(), [load]);

  const openCard = useCallback(
    (cardId: string) => {
      api
        .card(cardId)
        .then((next) => {
          setCard(next);
          setDraft(next.resolutions.conditions);
          setAnswers(next.resolutions.ambiguities);
          setAdded(next.resolutions.added ?? []);
          setError(null);
        })
        .catch((reason: unknown) => setError(message(reason)));
    },
    [api],
  );

  useEffect(() => {
    if (!job) return;
    const timer = window.setInterval(() => {
      api
        .job(job.id)
        .then((state) => {
          if (state.state === "running") return;
          setJob(null);
          if (state.state === "failed") {
            setError(t("tc.failed", { reason: state.error ?? "" }));
          } else if (state.card_id) {
            openCard(state.card_id);
            load();
          }
        })
        .catch((reason: unknown) => {
          setJob(null);
          setError(message(reason));
        });
    }, pollMs);
    return () => window.clearInterval(timer);
  }, [api, job, load, openCard, pollMs, t]);

  const showPreview = (source: TechniqueSource, model?: string) => {
    setPreview(null);
    setChoice(null);
    setCard(null);
    setPreviewing(source.title || source.source_key);
    api
      .preview(source.item_ref, model)
      .then((next) => {
        setPreview(next);
        setError(null);
      })
      .catch((reason: unknown) => setError(message(reason)))
      .finally(() => setPreviewing(null));
  };

  const existing =
    sources?.find((item) => item.item_ref === preview?.item_ref)?.card_ids ??
    [];
  const extract = () => {
    if (!preview) return;
    if (existing.length && choice === null) return;
    const started = !existing.length
      ? api.start(preview.item_ref, preview.model)
      : api.start(
          preview.item_ref,
          preview.model,
          choice === "version"
            ? { mode: "new_version", cardId: existing[0] }
            : { mode: "new_card" },
        );
    started
      .then((started) => {
        setJob({ id: started.job_id, item: preview.item_ref });
        setPreview(null);
        setError(null);
      })
      .catch((reason: unknown) => setError(message(reason)));
  };

  const flagsOf = useMemo(() => {
    const out = new Map<number, Flag["code"][]>();
    for (const entry of card?.checks.conditions ?? []) {
      out.set(
        entry.index,
        entry.flags.map((flag) => flag.code),
      );
    }
    return out;
  }, [card]);

  const save = () => {
    if (!card) return;
    api
      .resolve(
        card.card_id,
        card.version,
        draft,
        answers,
        added.filter((entry) => entry.text.trim()),
      )
      .then(() => {
        openCard(card.card_id);
        load();
      })
      .catch((reason: unknown) => setError(message(reason)));
  };

  const withdraw = () => {
    if (!card || !window.confirm(t("tc.withdrawConfirm"))) return;
    api
      .withdraw(card.card_id)
      .then(() => {
        openCard(card.card_id);
        load();
      })
      .catch((reason: unknown) => setError(message(reason)));
  };

  const recheck = () => {
    if (!card) return;
    api
      .recheck(card.card_id)
      .then(() => openCard(card.card_id))
      .catch((reason: unknown) => setError(message(reason)));
  };

  const setAction = (index: number, action: Resolution["action"]) => {
    const key = String(index);
    const condition = card?.card.conditions[index];
    setDraft((previous) => ({
      ...previous,
      [key]:
        action === "edit" && condition
          ? { action, condition: previous[key]?.condition ?? condition }
          : { action },
    }));
  };

  const editNumber = (index: number, slot: NumberSlot, raw: string) => {
    const key = String(index);
    const original = card?.card.conditions[index];
    setDraft((previous) => {
      const base = previous[key]?.condition ?? original;
      if (!base) return previous;
      return {
        ...previous,
        [key]: { action: "edit", condition: withSlot(base, slot, raw) },
      };
    });
  };

  const timeline = card?.timeline ?? null;
  const rows = timeline
    ? timeline.filter(
        (row) =>
          !onlyUncited || (!row.conditions.length && !row.universe.length),
      )
    : [];

  return (
    <div
      className="modal-backdrop bt-backdrop"
      onPointerDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className="modal bt-screen tc-screen"
        role="dialog"
        aria-label={t("tc.title")}
      >
        <header className="modal-head">
          <h2>{t("tc.title")}</h2>
          <span className="workspace-note">{t("tc.subtitle")}</span>
          {settings ? (
            <label className="tc-default">
              {t("tc.defaultModel")}
              <select
                value={settings.default_model}
                onChange={(event) =>
                  api
                    .setDefaultModel(event.target.value)
                    .then(setSettings)
                    .catch((reason: unknown) => setError(message(reason)))
                }
              >
                {settings.default_choices.map((model) => (
                  <option key={model} value={model}>
                    {MODEL_NAMES[model] ?? model}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          {settings?.default_reset ? (
            <span className="workspace-note" role="status">
              {t("tc.defaultReset", {
                from:
                  MODEL_NAMES[settings.default_reset.from] ??
                  settings.default_reset.from,
                to:
                  MODEL_NAMES[settings.default_reset.to] ??
                  settings.default_reset.to,
              })}
            </span>
          ) : null}
          <button
            type="button"
            className="tb-button"
            disabled={loading}
            onClick={() => load(true)}
          >
            {t("tc.refresh")}
          </button>
          <button
            type="button"
            className="tb-button"
            aria-label={t("tc.close")}
            onClick={onClose}
          >
            ✕
          </button>
        </header>
        {error ? (
          <p className="form-error" role="alert">
            {error}
          </p>
        ) : null}
        <div className="tc-body">
          <aside className="tc-list" aria-label={t("tc.sources")}>
            <h3>{t("tc.sources")}</h3>
            {sources === null ? (
              <p className="workspace-note">{t("tc.loading")}</p>
            ) : null}
            {sources?.length === 0 ? (
              <p className="workspace-note">{t("tc.noSources")}</p>
            ) : null}
            <ul>
              {sources?.map((source) => (
                <li key={source.item_ref}>
                  <button
                    type="button"
                    className="tc-source"
                    disabled={job !== null}
                    onClick={() => showPreview(source)}
                  >
                    {source.title || source.source_key}
                  </button>
                  {source.card_ids.length ? (
                    <span className="workspace-note">
                      {t("tc.cardCount", { n: source.card_ids.length })}
                    </span>
                  ) : null}
                  {job?.item === source.item_ref ? (
                    <span className="workspace-note" role="status">
                      {t("tc.running")}
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
            <h3>{t("tc.cards")}</h3>
            <ul>
              {cards.map((summary) => (
                <li key={summary.card_id}>
                  <button
                    type="button"
                    className="tc-source"
                    aria-pressed={card?.card_id === summary.card_id}
                    onClick={() => openCard(summary.card_id)}
                  >
                    {summary.title}
                  </button>
                  {/* Cards from one source look alike by title (request #종목선정-1). */}
                  <span className="workspace-note tc-card-meta">
                    {t("tc.cardMeta", {
                      models:
                        summary.usage.models
                          .map((m) => MODEL_NAMES[m] ?? m)
                          .join(", ") || "-",
                      date: `${summary.created_at.slice(0, 16).replace("T", " ")} UTC`,
                      version: summary.version,
                    })}
                  </span>
                  <span className="workspace-note">
                    {summary.user_withdrawn_at
                      ? t("tc.userWithdrawn")
                      : summary.withdrawn
                        ? t("tc.withdrawn")
                        : summary.ready
                          ? t("tc.ready")
                          : t("tc.needsReview", { n: summary.blocks.length })}
                  </span>
                </li>
              ))}
            </ul>
          </aside>

          <div className="tc-main">
            {preview ? (
              <section aria-label={t("tc.preview")}>
                <h3>{preview.title}</h3>
                <dl className="tc-facts">
                  <dt>{t("tc.parts")}</dt>
                  <dd>
                    {t("tc.partsValue", {
                      parts: preview.parts,
                      lines: preview.lines,
                      chars: preview.characters,
                    })}
                  </dd>
                  <dt>{t("tc.model")}</dt>
                  <dd>
                    <select
                      aria-label={t("tc.chooseModel")}
                      value={preview.model}
                      onChange={(event) => {
                        const source = sources?.find(
                          (item) => item.item_ref === preview.item_ref,
                        );
                        if (source) showPreview(source, event.target.value);
                      }}
                    >
                      {preview.models.map((model) => (
                        <option key={model} value={model}>
                          {model === preview.default_model
                            ? t("tc.assigned", {
                                model: MODEL_NAMES[model] ?? model,
                              })
                            : (MODEL_NAMES[model] ?? model)}
                        </option>
                      ))}
                    </select>{" "}
                    → {preview.fallback}
                    {preview.limit_fallback ? (
                      <span className="workspace-note">
                        {" "}
                        {t("tc.limitFallback", {
                          chain: preview.limit_fallback
                            .split(" → ")
                            .map((m) => MODEL_NAMES[m] ?? m)
                            .join(" → "),
                        })}
                      </span>
                    ) : null}
                  </dd>
                  <dt>{t("tc.cost")}</dt>
                  <dd>
                    ${preview.estimated_cost_usd} ({preview.estimate_note})
                  </dd>
                  <dt>{t("tc.source")}</dt>
                  <dd>
                    {preview.verification_status} · {preview.data_grade} ·{" "}
                    {preview.recheck_outcome}
                  </dd>
                </dl>
                <p className="workspace-note">{t("tc.sendNote")}</p>
                {existing.length ? (
                  <fieldset className="tc-choice">
                    <legend>
                      {t("tc.existingCards", { n: existing.length })}
                    </legend>
                    <label className="checkbox-row">
                      <input
                        type="radio"
                        name="tc-choice"
                        checked={choice === "version"}
                        onChange={() => setChoice("version")}
                      />
                      {t("tc.newVersion", {
                        title: (() => {
                          const newest = cards.find(
                            (c) => c.card_id === existing[0],
                          );
                          return newest
                            ? `${newest.created_at.slice(0, 16).replace("T", " ")} UTC · ${t("tc.versionN", { version: newest.version })}`
                            : existing[0].slice(0, 8);
                        })(),
                      })}
                    </label>
                    <label className="checkbox-row">
                      <input
                        type="radio"
                        name="tc-choice"
                        checked={choice === "card"}
                        onChange={() => setChoice("card")}
                      />
                      {t("tc.newCard")}
                    </label>
                  </fieldset>
                ) : null}
                <button
                  type="button"
                  className="primary-button"
                  disabled={existing.length > 0 && choice === null}
                  onClick={extract}
                >
                  {t("tc.extract")}
                </button>
              </section>
            ) : null}

            {card ? (
              <section aria-label={t("tc.card")}>
                <h3>{card.title}</h3>
                <p
                  className={card.ready ? "tc-ready" : "tc-blocked"}
                  role="status"
                >
                  {card.ready ? t("tc.readyLong") : blockedText(card, t)}
                </p>
                <p className="workspace-note">
                  {t("tc.usage", {
                    models: card.usage.models.join(", "),
                    input: card.usage.input_tokens,
                    output: card.usage.output_tokens,
                    cost: card.usage.cost_usd,
                    unbacked: card.checks.counts.unbacked ?? 0,
                    noCriterion: card.checks.counts.no_criterion ?? 0,
                    notComputable: card.checks.counts.not_computable ?? 0,
                    corrected: card.corrected,
                  })}{" "}
                  {t("tc.recheckState", { outcome: card.recheck_outcome })}{" "}
                  <button type="button" className="tb-button" onClick={recheck}>
                    {t("tc.recheck")}
                  </button>
                </p>
                {card.usage.switches?.length ? (
                  <ul className="tc-switches" role="status">
                    {card.usage.switches.map((s, i) => (
                      <li key={i}>
                        {t("tc.switch", {
                          part: s.part,
                          from: MODEL_NAMES[s.from] ?? s.from,
                          to: MODEL_NAMES[s.to] ?? s.to,
                          reason: t(
                            s.reason === "limit"
                              ? "tc.switchLimit"
                              : s.reason === "key"
                                ? "tc.switchKey"
                                : "tc.switchFailed",
                          ),
                        })}
                      </li>
                    ))}
                  </ul>
                ) : null}
                <table className="tc-conditions">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>{t("tc.field")}</th>
                      <th>{t("tc.condition")}</th>
                      <th>{t("tc.cited")}</th>
                      <th>{t("tc.flags")}</th>
                      <th>{t("tc.decision")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {card.card.conditions.map((condition, index) => {
                      const flags = flagsOf.get(index) ?? [];
                      const resolution = draft[String(index)];
                      const shown = resolution?.condition ?? condition;
                      return (
                        <tr
                          key={index}
                          className={flags.length ? "tc-flagged" : undefined}
                        >
                          <td>{index + 1}</td>
                          <td>
                            {t(`tc.f.${condition.field}`)} · {condition.side}
                          </td>
                          <td>
                            <div>{condition.text}</div>
                            <code>{slotsLabel(shown)}</code>
                            {resolution?.action === "edit" ? (
                              <div className="tc-edit">
                                {NUMBER_SLOTS.map((slot) => (
                                  <label key={slot}>
                                    {slot}
                                    <input
                                      type="number"
                                      value={slotValue(shown, slot) ?? ""}
                                      onChange={(event) =>
                                        editNumber(
                                          index,
                                          slot,
                                          event.target.value,
                                        )
                                      }
                                    />
                                  </label>
                                ))}
                              </div>
                            ) : null}
                          </td>
                          <td>
                            {condition.citations.map((citation) => (
                              <div key={`${citation.locator}${citation.quote}`}>
                                [{citation.locator}] “{citation.quote}”
                              </div>
                            ))}
                          </td>
                          <td>
                            {flags.map((code) => (
                              <span
                                key={code}
                                className={`tc-flag tc-flag-${code}`}
                              >
                                {t(`tc.flag.${code}`)}
                              </span>
                            ))}
                          </td>
                          <td>
                            {(["confirm", "edit", "drop"] as const).map(
                              (action) => (
                                <button
                                  key={action}
                                  type="button"
                                  className="tb-button"
                                  aria-pressed={resolution?.action === action}
                                  onClick={() => setAction(index, action)}
                                >
                                  {t(`tc.a.${action}`)}
                                </button>
                              ),
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                {card.card.ambiguities.length ? (
                  <section aria-label={t("tc.ambiguities")}>
                    <h4>{t("tc.ambiguities")}</h4>
                    {card.card.ambiguities.map((ambiguity, index) => (
                      <label key={index} className="tc-answer">
                        <span>
                          {ambiguity.text} — {ambiguity.question}
                        </span>
                        <input
                          value={answers[String(index)] ?? ""}
                          placeholder={t("tc.answerHint")}
                          onChange={(event) =>
                            setAnswers((previous) => ({
                              ...previous,
                              [String(index)]: event.target.value,
                            }))
                          }
                        />
                      </label>
                    ))}
                  </section>
                ) : null}
                <section aria-label={t("tc.added")}>
                  <h4>{t("tc.added")}</h4>
                  <p className="workspace-note">{t("tc.addedNote")}</p>
                  {added.map((entry, index) => (
                    <div key={index} className="tc-added">
                      <select
                        aria-label={t("tc.addedField")}
                        value={entry.field}
                        onChange={(event) => {
                          const field = event.target
                            .value as AddedCondition["field"];
                          setAdded((previous) =>
                            previous.map((item, at) =>
                              at === index ? { ...item, field } : item,
                            ),
                          );
                        }}
                      >
                        <option value="entry">{t("tc.f.entry")}</option>
                        <option value="exit">{t("tc.f.exit")}</option>
                      </select>
                      <input
                        aria-label={t("tc.addedText")}
                        value={entry.text}
                        maxLength={500}
                        placeholder={t("tc.addedHint")}
                        onChange={(event) => {
                          const text = event.target.value;
                          setAdded((previous) =>
                            previous.map((item, at) =>
                              at === index ? { ...item, text } : item,
                            ),
                          );
                        }}
                      />
                      <button
                        type="button"
                        className="tb-button"
                        onClick={() =>
                          setAdded((previous) =>
                            previous.filter((_, at) => at !== index),
                          )
                        }
                      >
                        {t("tc.addedRemove")}
                      </button>
                    </div>
                  ))}
                  <button
                    type="button"
                    className="tb-button"
                    disabled={added.length >= 20}
                    onClick={() =>
                      setAdded((previous) => [
                        ...previous,
                        { field: "entry", text: "" },
                      ])
                    }
                  >
                    {t("tc.addedAdd")}
                  </button>
                </section>
                {(
                  [
                    "universe",
                    "timeframe",
                    "position",
                    "inexpressible",
                  ] as const
                ).map((name) =>
                  card.card[name].length ? (
                    <section key={name} aria-label={t(`tc.s.${name}`)}>
                      <h4>{t(`tc.s.${name}`)}</h4>
                      <ul>
                        {card.card[name].map((statement, index) => (
                          <li key={index}>
                            {statement.text}{" "}
                            {statement.citations
                              .map((citation) => `[${citation.locator}]`)
                              .join(" ")}
                          </li>
                        ))}
                      </ul>
                    </section>
                  ) : null,
                )}
                <button
                  type="button"
                  className="primary-button"
                  disabled={card.withdrawn || Boolean(card.user_withdrawn_at)}
                  onClick={save}
                >
                  {t("tc.save")}
                </button>{" "}
                {card.user_withdrawn_at ? null : (
                  <button type="button" onClick={withdraw}>
                    {t("tc.withdraw")}
                  </button>
                )}
                {card.ready && !card.withdrawn ? (
                  <TechniqueDraftPanel
                    key={`${card.card_id}:${card.version}`}
                    api={api}
                    card={card}
                    pollMs={pollMs}
                    defaultModel={settings?.default_model ?? CHOOSABLE_DEFAULT}
                  />
                ) : null}
                <section aria-label={t("tc.timeline")}>
                  <h4>{t("tc.timeline")}</h4>
                  {timeline === null ? (
                    <p className="workspace-note">{t("tc.noTimeline")}</p>
                  ) : (
                    <>
                      <label className="checkbox-row">
                        <input
                          type="checkbox"
                          checked={onlyUncited}
                          onChange={(event) =>
                            setOnlyUncited(event.target.checked)
                          }
                        />
                        {t("tc.onlyUncited")}
                      </label>
                      <ol className="tc-timeline">
                        {rows.map((row) => (
                          <li
                            key={`${row.time}${row.text}`}
                            className={
                              row.conditions.length || row.universe.length
                                ? "tc-cited"
                                : undefined
                            }
                          >
                            <span className="tc-time">{row.time}</span>{" "}
                            {row.text}
                            {row.conditions.map((index) => (
                              <span key={`c${index}`} className="tc-mark">
                                #{index + 1}
                              </span>
                            ))}
                            {row.universe.map((index) => (
                              <span key={`u${index}`} className="tc-mark">
                                {t("tc.s.universe")} {index + 1}
                              </span>
                            ))}
                          </li>
                        ))}
                      </ol>
                    </>
                  )}
                </section>
              </section>
            ) : null}

            {previewing ? (
              <p className="workspace-note" role="status">
                {t("tc.previewing", { title: previewing })}
              </p>
            ) : null}
            {!preview && !card && !previewing ? (
              <p className="workspace-note">{t("tc.pick")}</p>
            ) : null}
          </div>
        </div>
      </section>
    </div>
  );
}
