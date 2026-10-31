import type { IndicatorConversation } from "@siw/contracts/types";
import { useEffect, useMemo, useState } from "react";

import { answerText } from "./answer-text";
import { useI18n } from "./i18n";
import {
  type AssistantEvent,
  type AssistantOrigin,
  type ChartContext,
  type ChartImage,
  type IndicatorAssistantApi,
  MAX_IMAGES,
  type RetryStep,
  nextRetry,
} from "./indicator-assistant-api";
import {
  INDICATORS,
  INDICATOR_BY_ID,
  type OhlcvBar,
  type PriceSource,
} from "./indicators";
import { MAX_PINE_LENGTH, type PineContext } from "./pine";
import {
  builtinDetail,
  pineReference,
  runForAssistant,
} from "./pine-assistant";

/** The chart a script is tried on: its bars and what Pine needs to know about it. */
export interface AssistantChart {
  symbol: string;
  timeframe: string;
  bars: OhlcvBar[];
  context: PineContext;
}

/** A Pine indicator already on a chart, offered as a starting point. */
export interface SavedScript {
  key: string;
  title: string;
  code: string;
  /** On the chart the assistant works on, so applying updates it. */
  sameChart: boolean;
}

/** Where a new conversation starts. */
export type AssistantStart =
  | { kind: "new" }
  | { kind: "saved"; key: string }
  | { kind: "tradingview" }
  | {
      kind: "builtin";
      id: string;
      params?: Record<string, number>;
      source?: PriceSource;
    };

/**
 * Claude writes a Pine indicator with the investor (ADR 0042, indicators steps 1-2). It
 * can start from a saved indicator, TradingView code or a built-in indicator. Each script
 * Claude wants to try is run here on the chart's bars; the error or a summary of the
 * values goes back to Claude, which fixes and runs again (at most five fixes). The last
 * script that ran is drawn on the chart as a preview; only "Apply" saves it. A picture of
 * the bars in view goes along only when the investor attaches one.
 */
export function IndicatorAssistant({
  api,
  conversationId,
  initialCode,
  applied,
  chart,
  onPreview,
  onApply,
  initialStart = { kind: "new" },
  scripts = [],
  onTarget,
  captureImage,
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
  initialStart?: AssistantStart;
  scripts?: SavedScript[];
  /** The saved indicator that applying should update, if any. */
  onTarget?: (instanceKey: string | null) => void;
  /** Draws the bars in view; null when the chart has none. */
  captureImage?: () => Promise<ChartImage | null>;
}) {
  const { t, language } = useI18n();
  const [conversation, setConversation] =
    useState<IndicatorConversation | null>(null);
  const [question, setQuestion] = useState("");
  const [code, setCode] = useState(initialCode);
  const [start, setStart] = useState<AssistantStart>(initialStart);
  const [image, setImage] = useState<ChartImage | null>(null);
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

  const chooseStart = (next: AssistantStart) => {
    setStart(next);
    const saved =
      next.kind === "saved"
        ? scripts.find((script) => script.key === next.key)
        : undefined;
    setCode(saved?.code ?? "");
    onTarget?.(saved?.sameChart ? saved.key : null);
    onPreview(null);
  };

  /** What the first request tells Claude about its starting point. */
  const originNow = (current: AssistantChart): AssistantOrigin | undefined => {
    if (start.kind === "saved") return { kind: "saved" };
    if (start.kind === "tradingview") {
      return {
        kind: "tradingview",
        detail:
          checked && !checked.ok
            ? checked.error.slice(0, 4000)
            : "It runs in the app as it is.",
      };
    }
    if (start.kind === "builtin") {
      const definition = INDICATOR_BY_ID.get(start.id);
      if (!definition) return undefined;
      return {
        kind: "builtin",
        detail: builtinDetail(
          definition,
          start.params ?? {},
          start.source,
          current.bars,
          current.context,
        ),
      };
    }
    return undefined;
  };

  const attachImage = async () => {
    setProblem(null);
    try {
      const drawn = await captureImage?.();
      if (drawn) setImage(drawn);
      else setProblem(t("ia.noChart"));
    } catch (error) {
      fail(error);
    }
  };

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

  const send = async (text: string, retry?: RetryStep) => {
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
            retry,
            retry ? undefined : (image ?? undefined),
          )
        : await api.start(
            text,
            language === "ko" ? "ko" : "en",
            pineReference(),
            contextNow(current),
            {
              ...(image ? { image } : {}),
              ...(originNow(current) ? { origin: originNow(current) } : {}),
            },
          );
      setConversation(answered);
      setQuestion("");
      if (!retry) setImage(null);
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
  const retry = conversation?.status === "stopped" ? nextRetry(events) : null;
  const waiting = conversation?.status === "needs_run";
  const runnable = checked?.ok === true;
  // Nothing sent yet, and not reopening a saved conversation: the start can still change.
  const choosing = !conversation && !conversationId;
  const imagesUsed = events.filter((event) => event.has_image).length;
  // Moving code or a built-in over needs no words; everything else does.
  const fallbackQuestion =
    choosing && start.kind === "tradingview" && code.trim()
      ? t("ia.portQuestion")
      : choosing && start.kind === "builtin"
        ? t("ia.builtinQuestion")
        : "";
  const request =
    choosing && start.kind === "tradingview" && !code.trim()
      ? ""
      : question.trim() || fallbackQuestion;

  return (
    <section className="ia-panel" aria-label={t("ia.title")}>
      <p className="workspace-note">{t("ia.note")}</p>
      {choosing ? (
        <div className="ia-start">
          <label>
            {t("ia.start")}
            <select
              aria-label={t("ia.start")}
              value={start.kind}
              onChange={(event) => {
                const kind = event.target.value as AssistantStart["kind"];
                if (kind === "saved") {
                  chooseStart({ kind, key: scripts[0]?.key ?? "" });
                } else if (kind === "builtin") {
                  chooseStart({ kind, id: INDICATORS[0].id });
                } else {
                  chooseStart({ kind });
                }
              }}
            >
              <option value="new">{t("ia.startNew")}</option>
              <option value="saved" disabled={scripts.length === 0}>
                {t("ia.startSaved")}
              </option>
              <option value="tradingview">{t("ia.startTradingView")}</option>
              <option value="builtin">{t("ia.startBuiltin")}</option>
            </select>
          </label>
          {start.kind === "saved" ? (
            <label>
              {t("ia.savedPick")}
              <select
                value={start.key}
                onChange={(event) =>
                  chooseStart({ kind: "saved", key: event.target.value })
                }
              >
                {scripts.map((script) => (
                  <option key={script.key} value={script.key}>
                    {script.sameChart
                      ? script.title
                      : `${script.title} (${t("ia.otherChart")})`}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          {start.kind === "builtin" ? (
            <label>
              {t("ia.builtinPick")}
              <select
                value={start.id}
                onChange={(event) =>
                  chooseStart({ kind: "builtin", id: event.target.value })
                }
              >
                {INDICATORS.map((item) => (
                  <option key={item.id} value={item.id}>
                    {language === "ko" ? item.name.ko : item.name.en}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          {start.kind === "tradingview" ? (
            <p className="workspace-note">{t("ia.tradingViewNote")}</p>
          ) : null}
          {start.kind === "builtin" ? (
            <p className="workspace-note">{t("ia.builtinNote")}</p>
          ) : null}
        </div>
      ) : null}
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
      {retry && busy === null ? (
        <p className="bt-actions">
          <button
            type="button"
            className="tb-button"
            onClick={() => void send(t("ia.retryQuestion"), retry)}
          >
            {retry.model === "claude-opus-5-5"
              ? t("ia.retryOpus")
              : t("ia.retryHigh")}
          </button>
          <span className="workspace-note">
            {retry.model === "claude-opus-5-5"
              ? t("ia.retryNote")
              : t("ia.retryHighNote")}
          </span>
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
              request &&
              busy === null
            ) {
              void send(request);
            }
          }}
        />
      </label>
      {image ? (
        <figure className="ia-image">
          <img
            src={`data:image/png;base64,${image.png_base64}`}
            alt={t("ia.imageAlt", { bars: image.bars })}
          />
          <figcaption className="workspace-note">
            {t("ia.imageNote", { bars: image.bars })}{" "}
            <button
              type="button"
              className="tb-button"
              onClick={() => setImage(null)}
            >
              {t("ia.imageRemove")}
            </button>
          </figcaption>
        </figure>
      ) : null}
      <p className="bt-actions">
        <button
          type="button"
          className="tb-button bt-primary"
          disabled={busy !== null || !request}
          onClick={() => void send(request)}
        >
          {busy !== null ? t("rp.sending") : t("ia.send")}
        </button>
        {captureImage && !image ? (
          <button
            type="button"
            className="tb-button"
            disabled={busy !== null || imagesUsed >= MAX_IMAGES}
            title={
              imagesUsed >= MAX_IMAGES
                ? t("ia.imageLimit", { max: MAX_IMAGES })
                : undefined
            }
            onClick={() => void attachImage()}
          >
            {t("ia.imageAttach")}
          </button>
        ) : null}
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
    return (
      <p className="rv-bubble rv-asked">
        {event.text}
        {event.has_image ? (
          <small className="ia-attached">{t("ia.imageSent")}</small>
        ) : null}
      </p>
    );
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
