import type { IndicatorConversation } from "@siw/contracts/types";
import { useEffect, useMemo, useState } from "react";

import { answerText } from "./answer-text";
import { useI18n } from "./i18n";
import type {
  AssistantEvent,
  AssistantModel,
  ChartContext,
  IndicatorAssistantApi,
} from "./indicator-assistant-api";
import type { OhlcvBar } from "./indicators";
import { MAX_PINE_LENGTH, type PineContext } from "./pine";
import { pineReference, runForAssistant } from "./pine-assistant";

const RETRY_MODEL: AssistantModel = "claude-opus-5-5";

/** The chart a script is tried on: its bars and what Pine needs to know about it. */
export interface AssistantChart {
  symbol: string;
  timeframe: string;
  bars: OhlcvBar[];
  context: PineContext;
}

/**
 * Claude writes a Pine indicator with the investor (ADR 0042, indicators step 1). Each
 * script Claude wants to try is run here on the chart's bars; the error or a summary of
 * the values goes back to Claude, which fixes and runs again (at most five fixes). The
 * last script that ran is drawn on the chart as a preview; only "Apply" saves it.
 */
export function IndicatorAssistant({
  api,
  conversationId,
  initialCode,
  applied,
  chart,
  onPreview,
  onApply,
}: {
  api: IndicatorAssistantApi;
  /** A saved conversation to continue (an indicator made here earlier). */
  conversationId: string | null;
  initialCode: string;
  /** The script is already an indicator on the chart (apply updates it). */
  applied: boolean;
  chart: () => AssistantChart | null;
  onPreview: (source: string | null) => void;
  onApply: (source: string, conversationId: string | null) => void;
}) {
  const { t, language } = useI18n();
  const [conversation, setConversation] =
    useState<IndicatorConversation | null>(null);
  const [question, setQuestion] = useState("");
  const [code, setCode] = useState(initialCode);
  const [busy, setBusy] = useState<"asking" | "running" | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const fail = (error: unknown) =>
    setProblem(
      error instanceof Error ? error.message : t("common.requestFailed"),
    );

  useEffect(() => {
    if (!conversationId) return;
    let current = true;
    api
      .conversation(conversationId)
      .then((loaded) => current && setConversation(loaded))
      .catch((error: unknown) => current && fail(error));
    return () => {
      current = false;
    };
    // `fail` only formats the error; reloading on its identity would loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, conversationId]);

  // The editor's script, checked on the chart as it is typed or loaded.
  const checked = useMemo(() => {
    const current = chart();
    if (!code.trim() || !current) return null;
    return runForAssistant(code, current.bars, current.context);
    // `chart` reads the chart's latest bars; rerun only when the code changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code]);

  const contextNow = (current: AssistantChart): ChartContext => ({
    symbol: current.symbol,
    timeframe: current.timeframe,
    bars: current.bars.length,
    code: code.trim() ? code : null,
  });

  /** Runs every script Claude asks for and sends the result, until it answers. */
  const settle = async (state: IndicatorConversation) => {
    let next = state;
    while (next.status === "needs_run" && next.pending_run) {
      const current = chart();
      if (!current) throw new Error(t("ia.noChart"));
      setBusy("running");
      const pending = next.pending_run;
      const run = runForAssistant(pending.code, current.bars, current.context);
      if (run.ok) {
        setCode(pending.code);
        onPreview(pending.code);
      }
      setBusy("asking");
      next = await api.sendRun(
        next.conversation_id,
        pending.tool_use_id,
        run.ok
          ? { ok: true, summary: run.summary }
          : { ok: false, error: run.error },
      );
      setConversation(next);
    }
  };

  const send = async (text: string, model?: AssistantModel) => {
    const current = chart();
    if (!current) {
      setProblem(t("ia.noChart"));
      return;
    }
    setBusy("asking");
    setProblem(null);
    try {
      const answered = conversation
        ? await api.ask(
            conversation.conversation_id,
            text,
            contextNow(current),
            model,
          )
        : await api.start(
            text,
            language === "ko" ? "ko" : "en",
            pineReference(),
            contextNow(current),
          );
      setConversation(answered);
      setQuestion("");
      await settle(answered);
    } catch (error) {
      fail(error);
    } finally {
      setBusy(null);
    }
  };

  const events = conversation?.events ?? [];
  // Scripts that ran on the chart, newest last: any can be loaded again.
  const versions = events.flatMap((event, index) =>
    event.type === "code" &&
    events
      .slice(index + 1)
      .some(
        (later) =>
          later.type === "run" &&
          later.tool_use_id === event.tool_use_id &&
          later.ok,
      )
      ? [event]
      : [],
  );
  const stopped = conversation?.status === "stopped";
  const waiting = conversation?.status === "needs_run";
  const runnable = checked?.ok === true;

  return (
    <section className="ia-panel" aria-label={t("ia.title")}>
      <p className="workspace-note">{t("ia.note")}</p>
      <div className="ia-events">
        {events.map((event, index) => (
          <AssistantEventView
            key={index}
            event={event}
            version={versions.indexOf(event) + 1}
          />
        ))}
        {busy !== null ? (
          <p className="workspace-note" role="status">
            {busy === "running"
              ? t("ia.running")
              : t("ia.thinking", {
                  runs: conversation?.runs ?? 0,
                  max: conversation?.max_runs ?? 6,
                })}
          </p>
        ) : null}
      </div>
      {problem ? (
        <p className="bt-problem" role="alert">
          {problem}
        </p>
      ) : null}
      {stopped && busy === null ? (
        <p className="bt-actions">
          <button
            type="button"
            className="tb-button"
            onClick={() => void send(t("ia.retryQuestion"), RETRY_MODEL)}
          >
            {t("ia.retryOpus")}
          </button>
          <span className="workspace-note">{t("ia.retryNote")}</span>
        </p>
      ) : null}
      {waiting && busy === null && conversation ? (
        <p className="bt-actions">
          <button
            type="button"
            className="tb-button"
            onClick={() => {
              setProblem(null);
              settle(conversation)
                .catch(fail)
                .finally(() => setBusy(null));
            }}
          >
            {t("ia.runPending")}
          </button>
        </p>
      ) : null}
      <label className="rv-question">
        {t("ia.request")}
        <textarea
          value={question}
          maxLength={4000}
          placeholder={t("ia.placeholder")}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            if (
              event.key === "Enter" &&
              (event.metaKey || event.ctrlKey) &&
              question.trim() &&
              busy === null
            ) {
              void send(question.trim());
            }
          }}
        />
      </label>
      <p className="bt-actions">
        <button
          type="button"
          className="tb-button bt-primary"
          disabled={busy !== null || !question.trim()}
          onClick={() => void send(question.trim())}
        >
          {busy !== null ? t("rp.sending") : t("ia.send")}
        </button>
      </p>
      {conversation && conversation.usage.output_tokens > 0 ? (
        <p className="workspace-note">
          {t("rc.usage", {
            model: conversation.model,
            input: conversation.usage.input_tokens,
            output: conversation.usage.output_tokens,
            read: conversation.usage.cache_read_tokens,
            write: conversation.usage.cache_write_tokens,
          })}
        </p>
      ) : null}
      <label>
        {t("pine.code")}
        <textarea
          className="formula-text pine-text"
          spellCheck={false}
          rows={10}
          maxLength={MAX_PINE_LENGTH}
          value={code}
          aria-label={t("pine.code")}
          onChange={(event) => setCode(event.target.value)}
        />
      </label>
      {checked && !checked.ok ? (
        <p className="chart-error" role="alert">
          {checked.error}
        </p>
      ) : null}
      {versions.length > 1 ? (
        <label className="ia-versions">
          {t("ia.versions")}
          <select
            value=""
            onChange={(event) => {
              const picked = versions[Number(event.target.value)];
              if (!picked?.code) return;
              setCode(picked.code);
              onPreview(picked.code);
            }}
          >
            <option value="" disabled>
              {t("ia.pickVersion")}
            </option>
            {versions.map((version, index) => (
              <option key={version.tool_use_id} value={index}>
                {`v${index + 1} · ${version.text ?? ""}`.slice(0, 80)}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      <p className="bt-actions">
        <button
          type="button"
          className="tb-button"
          disabled={!runnable}
          onClick={() => onPreview(code)}
        >
          {t("ia.preview")}
        </button>
        <button
          type="button"
          className="tb-button"
          onClick={() => onPreview(null)}
        >
          {t("ia.clearPreview")}
        </button>
        <button
          type="button"
          className="tb-button bt-primary"
          disabled={!runnable}
          onClick={() =>
            onApply(code, conversation?.conversation_id ?? conversationId)
          }
        >
          {applied ? t("ia.applyUpdate") : t("ia.apply")}
        </button>
      </p>
    </section>
  );
}

function AssistantEventView({
  event,
  version,
}: {
  event: AssistantEvent;
  version: number;
}) {
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
  if (event.type === "code") {
    return (
      <details className="ia-code">
        <summary>
          {version > 0 ? `v${version} · ` : ""}
          {t("ia.script")} {event.text}
        </summary>
        <pre className="bt-logs">{event.code}</pre>
      </details>
    );
  }
  if (event.type === "run") {
    return event.ok ? (
      <details className="rv-request">
        <summary>{t("ia.ranOk")}</summary>
        <pre className="bt-logs">{event.text}</pre>
      </details>
    ) : (
      <p className="rv-request rv-request-error">
        {t("ia.ranError")} {event.text}
      </p>
    );
  }
  return (
    <p className="bt-problem" role="alert">
      {event.text}
    </p>
  );
}
