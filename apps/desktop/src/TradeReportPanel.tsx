import type {
  ReviewReportDraft,
  ReviewTradeReport,
} from "@siw/contracts/types";
import { useEffect, useState } from "react";

import { renderChartImage } from "./chart-image";
import { ReportConversation } from "./ReportConversation";
import { useI18n } from "./i18n";
import type { ReviewApi } from "./review-api";

type Section = ReviewTradeReport["sections"][number];
type Rendered = {
  kind: ReviewReportDraft["images"][number]["kind"];
  png: string;
};

const LISTS = ["evidence", "went_well", "to_improve", "check_next"] as const;

/**
 * Claude's report on one trade (ADR 0042, step 2a). Nothing is sent until the user has
 * seen the facts, the note and the images and pressed send.
 */
export function TradeReportPanel({
  api,
  tradeId,
  render = renderChartImage,
}: {
  api: ReviewApi;
  tradeId: string;
  /** Draws one image to base64 PNG; replaced in tests (jsdom has no canvas). */
  render?: typeof renderChartImage;
}) {
  const { t, language } = useI18n();
  const [reports, setReports] = useState<ReviewTradeReport[]>([]);
  const [draft, setDraft] = useState<ReviewReportDraft | null>(null);
  const [images, setImages] = useState<Rendered[] | null>(null);
  const [includeNote, setIncludeNote] = useState(true);
  const [answerLanguage, setAnswerLanguage] = useState<"en" | "ko">(
    language === "ko" ? "ko" : "en",
  );
  const [busy, setBusy] = useState<"draft" | "send" | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const fail = (error: unknown) =>
    setProblem(
      error instanceof Error ? error.message : t("common.requestFailed"),
    );

  useEffect(() => {
    let current = true;
    api
      .reports(tradeId)
      .then((loaded) => current && setReports(loaded))
      .catch((error: unknown) => current && fail(error));
    return () => {
      current = false;
    };
    // `fail` only formats the error; reloading on its identity would loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, tradeId]);

  const prepare = async (withNote: boolean) => {
    setBusy("draft");
    setProblem(null);
    setImages(null);
    try {
      const made = await api.draftReport(tradeId, withNote);
      setDraft(made);
      const drawn: Rendered[] = [];
      for (const image of made.images) {
        drawn.push({ kind: image.kind, png: await render(image) });
      }
      setImages(drawn);
    } catch (error) {
      fail(error);
    } finally {
      setBusy(null);
    }
  };

  const send = async () => {
    if (!draft || !images) return;
    setBusy("send");
    setProblem(null);
    try {
      const report = await api.sendReport(
        draft.draft_id,
        answerLanguage,
        images.map((image) => ({ kind: image.kind, png_base64: image.png })),
      );
      setReports([report, ...reports]);
      setDraft(null);
      setImages(null);
    } catch (error) {
      fail(error);
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="rv-report" aria-label={t("rp.title")}>
      <h4>{t("rp.title")}</h4>
      {problem ? (
        <p className="bt-problem" role="alert">
          {problem}
        </p>
      ) : null}
      {!draft ? (
        <p className="bt-actions">
          <button
            type="button"
            className="tb-button"
            disabled={busy !== null}
            onClick={() => void prepare(includeNote)}
          >
            {busy === "draft" ? t("rp.preparing") : t("rp.prepare")}
          </button>
          <span className="workspace-note">{t("rp.prepareNote")}</span>
        </p>
      ) : (
        <div className="rv-draft" aria-label={t("rp.preview")}>
          <p className="workspace-note">
            {t("rp.previewNote", { model: draft.model })}
          </p>
          <ul className="rv-facts-list">
            {draft.facts.map((fact, index) => (
              <li key={`${fact.code}-${index}`}>
                {fact.text}
                {fact.hindsight ? (
                  <span className="rv-hindsight"> {t("rp.hindsight")}</span>
                ) : null}
              </li>
            ))}
          </ul>
          {draft.principles ? (
            <div aria-label={t("pc.title")}>
              <p className="workspace-note">
                {t("pc.title")} ·{" "}
                {t("pc.version", { version: draft.principles.version })}
                {draft.principles.after_the_fact
                  ? ` · ${t("pc.afterTheFact")}`
                  : ""}
              </p>
              <ul className="rv-facts-list">
                {draft.principles.results.map((result) => (
                  <li key={result.item_id}>
                    {t(`pc.status.${result.status}`)}:{" "}
                    {result.text || t(`pr.kind.${result.kind}`)}
                    {result.value !== null ? ` (${result.value})` : ""}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {draft.uncertainty.length > 0 ? (
            <p className="workspace-note">{draft.uncertainty.join(" ")}</p>
          ) : null}
          <label className="bt-inline">
            <input
              type="checkbox"
              checked={includeNote}
              disabled={busy !== null}
              onChange={(event) => {
                setIncludeNote(event.target.checked);
                void prepare(event.target.checked);
              }}
            />
            {t("rp.includeNote")}
          </label>
          {draft.note ? (
            <blockquote className="rv-sent-note">
              {draft.note.text}
              {draft.note.tags.length ? ` · ${draft.note.tags.join(", ")}` : ""}
            </blockquote>
          ) : null}
          <div className="rv-images">
            {draft.images.map((image) => {
              const drawn = images?.find((item) => item.kind === image.kind);
              return (
                <figure key={image.kind}>
                  {drawn ? (
                    <img
                      src={`data:image/png;base64,${drawn.png}`}
                      alt={image.title}
                    />
                  ) : (
                    <p className="workspace-note">{t("rv.chartLoading")}</p>
                  )}
                  <figcaption>
                    {t(`rp.image.${image.kind}`)} · {image.title}
                  </figcaption>
                </figure>
              );
            })}
          </div>
          <p className="bt-actions">
            <label className="bt-inline">
              {t("rp.language")}
              <select
                value={answerLanguage}
                onChange={(event) =>
                  setAnswerLanguage(event.target.value as "en" | "ko")
                }
              >
                <option value="ko">한국어</option>
                <option value="en">English</option>
              </select>
            </label>
            <button
              type="button"
              className="tb-button"
              disabled={busy !== null || images === null}
              onClick={() => void send()}
            >
              {busy === "send" ? t("rp.sending") : t("rp.send")}
            </button>
            <button
              type="button"
              className="tb-button"
              disabled={busy !== null}
              onClick={() => {
                setDraft(null);
                setImages(null);
              }}
            >
              {t("rp.cancel")}
            </button>
          </p>
        </div>
      )}
      {reports.map((report) => (
        <ReportView
          key={report.report_id}
          report={report}
          api={api}
          render={render}
        />
      ))}
    </section>
  );
}

function ReportView({
  report,
  api,
  render,
}: {
  report: ReviewTradeReport;
  api: ReviewApi;
  render: typeof renderChartImage;
}) {
  const { t } = useI18n();
  return (
    <article className="rv-report-view" aria-label={t("rp.report")}>
      <p className="rv-summary">{report.summary}</p>
      {report.sections.map((section: Section) => (
        <section key={section.key}>
          <h5>{t(`rp.section.${section.key}`)}</h5>
          {LISTS.map((list) =>
            section[list].length > 0 ? (
              <div key={list} className={`rv-list-${list}`}>
                <span className="workspace-note">{t(`rp.list.${list}`)}</span>
                <ul>
                  {section[list].map((item, index) => (
                    <li key={index}>{item}</li>
                  ))}
                </ul>
              </div>
            ) : null,
          )}
        </section>
      ))}
      {report.uncertainty ? (
        <p className="workspace-note">
          {t("rp.uncertainty")}: {report.uncertainty}
        </p>
      ) : null}
      <p className="workspace-note">
        {t("rp.meta", {
          model: report.model,
          at: report.created_at_utc,
          input: report.usage.input_tokens,
          output: report.usage.output_tokens,
        })}{" "}
        · {t("rp.notAdvice")}
      </p>
      <ReportConversation
        api={api}
        reportId={report.report_id}
        render={render}
      />
    </article>
  );
}
