import type { ReviewConversation } from "@siw/contracts/types";
import { useEffect, useState } from "react";

import { answerText } from "./answer-text";
import { renderChartImage } from "./chart-image";
import { useI18n } from "./i18n";
import type { ReviewApi } from "./review-api";

type Event = ReviewConversation["events"][number];

/**
 * Follow-up questions on one report (ADR 0042, step 2b). Claude may ask the app for more
 * data; each request is listed. When it asks for a chart, the app draws the bars the API
 * cut and sends the picture back, then the answer continues.
 */
export function ReportConversation({
  api,
  reportId,
  render = renderChartImage,
  periodReviewId,
}: {
  api: ReviewApi;
  reportId: string;
  render?: typeof renderChartImage;
  /** Talk about a period review instead of a trade report (step 4b). */
  periodReviewId?: string;
}) {
  const { t, language } = useI18n();
  const [conversation, setConversation] = useState<ReviewConversation | null>(
    null,
  );
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState<"asking" | "drawing" | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const fail = (error: unknown) =>
    setProblem(
      error instanceof Error ? error.message : t("common.requestFailed"),
    );

  useEffect(() => {
    let current = true;
    (periodReviewId
      ? api.periodConversation(periodReviewId)
      : api.conversation(reportId)
    )
      .then((loaded) => current && setConversation(loaded))
      .catch((error: unknown) => current && fail(error));
    return () => {
      current = false;
    };
    // `fail` only formats the error; reloading on its identity would loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, reportId, periodReviewId]);

  /** Draws every awaited chart and sends them until the answer no longer waits. */
  const settle = async (state: ReviewConversation) => {
    let next = state;
    while (
      next.status === "needs_images" &&
      next.conversation_id &&
      next.pending_images.length > 0
    ) {
      setBusy("drawing");
      const drawn = [];
      for (const image of next.pending_images) {
        drawn.push({
          tool_use_id: image.tool_use_id,
          png_base64: await render(image),
        });
      }
      setBusy("asking");
      next = await api.sendChartImages(next.conversation_id, drawn);
      setConversation(next);
    }
  };

  const ask = async () => {
    const text = question.trim();
    if (!text) return;
    setBusy("asking");
    setProblem(null);
    try {
      const lang = language === "ko" ? "ko" : "en";
      const answered = periodReviewId
        ? await api.askPeriodQuestion(periodReviewId, text, lang)
        : await api.askQuestion(reportId, text, lang);
      setConversation(answered);
      setQuestion("");
      await settle(answered);
    } catch (error) {
      fail(error);
    } finally {
      setBusy(null);
    }
  };

  const waiting = conversation?.status === "needs_images";
  return (
    <section className="rv-conversation" aria-label={t("rc.title")}>
      <h5>{t("rc.title")}</h5>
      {(conversation?.events ?? []).map((event, index) => (
        <ConversationEvent key={index} event={event} />
      ))}
      {problem ? (
        <p className="bt-problem" role="alert">
          {problem}
        </p>
      ) : null}
      {waiting && busy === null ? (
        <p className="bt-actions">
          <button
            type="button"
            className="tb-button"
            onClick={() => {
              if (!conversation) return;
              setProblem(null);
              settle(conversation)
                .catch(fail)
                .finally(() => setBusy(null));
            }}
          >
            {t("rc.drawPending")}
          </button>
        </p>
      ) : null}
      <label className="rv-question">
        {t("rc.question")}
        <textarea
          value={question}
          maxLength={4000}
          placeholder={t("rc.placeholder")}
          onChange={(event) => setQuestion(event.target.value)}
        />
      </label>
      <p className="bt-actions">
        <button
          type="button"
          className="tb-button"
          disabled={busy !== null || waiting || !question.trim()}
          onClick={() => void ask()}
        >
          {busy === "drawing"
            ? t("rc.drawing")
            : busy === "asking"
              ? t("rp.sending")
              : t("rc.ask")}
        </button>
        <span className="workspace-note">{t("rc.note")}</span>
      </p>
      {conversation && conversation.usage.output_tokens > 0 ? (
        <p className="workspace-note">
          {t("rc.usage", {
            model: conversation.model ?? "",
            input: conversation.usage.input_tokens,
            output: conversation.usage.output_tokens,
            read: conversation.usage.cache_read_tokens,
            write: conversation.usage.cache_write_tokens,
          })}
        </p>
      ) : null}
    </section>
  );
}

function ConversationEvent({ event }: { event: Event }) {
  const { t } = useI18n();
  if (event.type === "question") {
    return <p className="rv-bubble rv-asked">{event.text}</p>;
  }
  if (event.type === "answer") {
    return (
      <div className="rv-bubble rv-answered">
        {answerText(event.text ?? "")}
      </div>
    );
  }
  if (event.type === "error") {
    return (
      <p className="bt-problem" role="alert">
        {event.text}
      </p>
    );
  }
  return (
    <p className={`rv-request${event.error ? " rv-request-error" : ""}`}>
      {t("rc.request", { tool: event.tool ?? "" })} {event.summary}
      {event.hindsight ? ` ${t("rp.hindsight")}` : ""}
    </p>
  );
}
