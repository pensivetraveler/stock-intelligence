import type {
  ReviewPeriodCommentary,
  ReviewPeriodPayload,
} from "@siw/contracts/types";
import { useEffect, useState } from "react";

import { useI18n, type MessageKey } from "./i18n";
import { applySuggestions } from "./period-review";
import { PrinciplesPanel } from "./PrinciplesPanel";
import { ReportConversation } from "./ReportConversation";
import type { PrincipleInput, ReviewApi } from "./review-api";

const LISTS = ["repeated_mistakes", "went_well", "focus_next"] as const;

type Suggestion = ReviewPeriodCommentary["principle_suggestions"][number];

/**
 * Claude on a period review (ADR 0042, step 4b): see what will be sent, ask for the
 * commentary, pick suggested principle changes into the editor, ask follow-ups.
 */
export function PeriodCommentary({
  api,
  reviewId,
  strategies,
}: {
  api: ReviewApi;
  reviewId: string;
  strategies: { strategy_id: string; name: string }[];
}) {
  const { t, language } = useI18n();
  const [commentaries, setCommentaries] = useState<ReviewPeriodCommentary[]>(
    [],
  );
  const [preview, setPreview] = useState<ReviewPeriodPayload | null>(null);
  const [busy, setBusy] = useState<"preview" | "send" | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [editing, setEditing] = useState<PrincipleInput[] | null>(null);
  const [saved, setSaved] = useState(false);

  const fail = (error: unknown) =>
    setProblem(
      error instanceof Error ? error.message : t("common.requestFailed"),
    );

  useEffect(() => {
    let current = true;
    api
      .periodCommentaries(reviewId)
      .then((list) => current && setCommentaries(list.commentaries))
      .catch((error: unknown) => current && fail(error));
    return () => {
      current = false;
    };
    // `fail` only formats the error; reloading on its identity would loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, reviewId]);

  const prepare = () => {
    setBusy("preview");
    setProblem(null);
    api
      .periodPayload(reviewId)
      .then(setPreview)
      .catch(fail)
      .finally(() => setBusy(null));
  };

  const send = () => {
    setBusy("send");
    setProblem(null);
    api
      .makePeriodCommentary(reviewId, language === "ko" ? "ko" : "en")
      .then((made) => {
        setCommentaries([made, ...commentaries]);
        setPreview(null);
      })
      .catch(fail)
      .finally(() => setBusy(null));
  };

  const apply = (chosen: Suggestion[]) => {
    setSaved(false);
    api
      .principles()
      .then((document) =>
        setEditing(applySuggestions(document.current?.items ?? [], chosen)),
      )
      .catch(fail);
  };

  const payload = preview?.payload as
    | {
        statistics?: unknown[];
        principles?: unknown[];
        best_and_worst_trades?: Record<
          string,
          {
            best: { note_written_after_the_trade: string | null }[];
            worst: { note_written_after_the_trade: string | null }[];
          }
        >;
      }
    | undefined;
  const trades = Object.values(payload?.best_and_worst_trades ?? {}).flatMap(
    (side) => [...side.best, ...side.worst],
  );

  return (
    <section className="rv-report" aria-label={t("pdc.title")}>
      <h4>{t("pdc.title")}</h4>
      {problem ? (
        <p className="bt-problem" role="alert">
          {problem}
        </p>
      ) : null}
      {!preview ? (
        <p className="bt-actions">
          <button
            type="button"
            className="tb-button"
            disabled={busy !== null}
            onClick={prepare}
          >
            {t("pdc.prepare")}
          </button>
          <span className="workspace-note">{t("rp.prepareNote")}</span>
        </p>
      ) : (
        <div className="rv-draft" aria-label={t("rp.preview")}>
          <p className="workspace-note">
            {t("pdc.previewNote", {
              sections: payload?.statistics?.length ?? 0,
              principles: payload?.principles?.length ?? 0,
              trades: trades.length,
              notes: trades.filter(
                (trade) => trade.note_written_after_the_trade,
              ).length,
            })}
          </p>
          <details>
            <summary>{t("pdc.raw")}</summary>
            <pre className="bt-logs">
              {JSON.stringify(preview.payload, null, 2)}
            </pre>
          </details>
          <p className="bt-actions">
            <button
              type="button"
              className="tb-button"
              disabled={busy !== null}
              onClick={send}
            >
              {busy === "send" ? t("rp.sending") : t("rp.send")}
            </button>
            <button
              type="button"
              className="tb-button"
              disabled={busy !== null}
              onClick={() => setPreview(null)}
            >
              {t("rp.cancel")}
            </button>
          </p>
        </div>
      )}
      {editing ? (
        <div aria-label={t("pdc.editing")}>
          <p className="workspace-note">{t("pdc.editingNote")}</p>
          <PrinciplesPanel
            api={api}
            strategies={strategies}
            initialItems={editing}
            onSaved={() => {
              setEditing(null);
              setSaved(true);
            }}
          />
        </div>
      ) : null}
      {saved ? <p className="workspace-note">{t("pdc.saved")}</p> : null}
      {commentaries.map((commentary, index) => (
        <CommentaryView
          key={`${commentary.created_at_utc}-${index}`}
          commentary={commentary}
          onApply={apply}
        />
      ))}
      <ReportConversation
        api={api}
        reportId={reviewId}
        periodReviewId={reviewId}
      />
    </section>
  );
}

function CommentaryView({
  commentary,
  onApply,
}: {
  commentary: ReviewPeriodCommentary;
  onApply: (chosen: Suggestion[]) => void;
}) {
  const { t } = useI18n();
  const [chosen, setChosen] = useState<Set<number>>(new Set());
  return (
    <article className="rv-report-view" aria-label={t("pdc.commentary")}>
      <p className="rv-summary">{commentary.summary}</p>
      {LISTS.map((list) =>
        commentary[list].length > 0 ? (
          <div key={list}>
            <h5>{t(`pdc.list.${list}`)}</h5>
            <ul>
              {commentary[list].map((item, index) => (
                <li key={index}>{item}</li>
              ))}
            </ul>
          </div>
        ) : null,
      )}
      {commentary.principle_suggestions.length > 0 ? (
        <div aria-label={t("pdc.suggestions")}>
          <h5>{t("pdc.suggestions")}</h5>
          {commentary.principle_suggestions.map((suggestion, index) => (
            <label key={index} className="rv-suggestion">
              <input
                type="checkbox"
                checked={chosen.has(index)}
                onChange={(event) => {
                  const next = new Set(chosen);
                  if (event.target.checked) next.add(index);
                  else next.delete(index);
                  setChosen(next);
                }}
              />
              <span>
                <strong>{t(`pdc.action.${suggestion.action}`)}</strong>{" "}
                {t(`pr.kind.${suggestion.kind}` as MessageKey)}
                {suggestion.value !== null ? ` ${suggestion.value}` : ""}
                {suggestion.text ? ` — ${suggestion.text}` : ""}
                <span className="workspace-note"> ({suggestion.reason})</span>
              </span>
            </label>
          ))}
          <p className="bt-actions">
            <button
              type="button"
              className="tb-button"
              disabled={chosen.size === 0}
              onClick={() =>
                onApply(
                  commentary.principle_suggestions.filter((_, index) =>
                    chosen.has(index),
                  ),
                )
              }
            >
              {t("pdc.apply")}
            </button>
          </p>
        </div>
      ) : null}
      {commentary.uncertainty ? (
        <p className="workspace-note">
          {t("rp.uncertainty")}: {commentary.uncertainty}
        </p>
      ) : null}
      <p className="workspace-note">
        {t("rp.meta", {
          model: commentary.model,
          at: commentary.created_at_utc,
          input: commentary.usage.input_tokens,
          output: commentary.usage.output_tokens,
        })}{" "}
        · {t("rp.notAdvice")}
      </p>
    </article>
  );
}
