import type { StrategyConversation } from "@siw/contracts/types";
import { useEffect, useMemo, useState } from "react";

import { type AssistantChart, AssistantEventView } from "./IndicatorAssistant";
import { useI18n, type MessageKey } from "./i18n";
import type { ChartContext } from "./indicator-assistant-api";
import { MAX_PINE_LENGTH } from "./pine";
import { compilePine } from "./pine";
import { pineReference, type SavedScript } from "./pine-assistant";
import {
  comparisonRequest,
  pineConditions,
  resultsOf,
  runStrategy,
} from "./strategy-research";
import type {
  Comparison,
  Conversion,
  ResearchOrigin,
  StrategyResearchApi,
  StrategyResults,
} from "./strategy-research-api";

type Start =
  { kind: "new" } | { kind: "saved"; key: string } | { kind: "tradingview" };

/**
 * Claude writes a Pine strategy with the investor (ADR 0042, strategy research step 2). Each
 * script Claude tries runs here on the chart; an error goes back so Claude can fix it, but
 * profit does not. "Send results" shows the figures and trades first, and only then sends
 * them. The last script that ran is drawn on the chart as a preview, with its backtest
 * below the chart; only "Add to chart" saves it.
 */
export function StrategyResearch({
  api,
  conversationId,
  initialCode,
  applied,
  chart,
  scripts,
  initialStart = { kind: "new" },
  onTarget,
  onPreview,
  onApply,
}: {
  api: StrategyResearchApi;
  /** A saved conversation to continue. */
  conversationId: string | null;
  initialCode: string;
  /** The script is already on the chart (adding updates it). */
  applied: boolean;
  chart: () => AssistantChart | null;
  scripts: SavedScript[];
  initialStart?: Start;
  onTarget: (instanceKey: string | null) => void;
  onPreview: (source: string | null) => void;
  onApply: (source: string, conversationId: string | null) => void;
}) {
  const { t, language } = useI18n();
  const [conversation, setConversation] = useState<StrategyConversation | null>(
    null,
  );
  const [question, setQuestion] = useState("");
  const [code, setCode] = useState(initialCode);
  const [start, setStart] = useState<Start>(initialStart);
  const [busy, setBusy] = useState<"asking" | "running" | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  // Results shown for checking, before they are sent.
  const [draft, setDraft] = useState<StrategyResults | null>(null);
  const [ruleName, setRuleName] = useState("");

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
    return runStrategy(code, current.bars, current.context);
    // `chart` reads the chart's latest bars; rerun only when the code changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code]);

  const contextNow = (current: AssistantChart): ChartContext => ({
    symbol: current.symbol,
    timeframe: current.timeframe,
    bars: current.bars.length,
    code: code.trim() ? code : null,
    ...(current.context.timeZone
      ? { time_zone: current.context.timeZone }
      : {}),
  });

  const chooseStart = (next: Start) => {
    setStart(next);
    const saved =
      next.kind === "saved"
        ? scripts.find((script) => script.key === next.key)
        : undefined;
    setCode(saved?.code ?? "");
    onTarget(saved?.sameChart ? saved.key : null);
    onPreview(null);
  };

  const originNow = (): ResearchOrigin | undefined => {
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
    return undefined;
  };

  const events = conversation?.events ?? [];
  // Scripts that ran on the chart, newest last: any can be loaded again.
  const versions = events.flatMap((event, index) =>
    event.type === "code" &&
    !event.diagnostic &&
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
  const results = versions.map(
    (version) =>
      events.find(
        (event) =>
          event.type === "run" && event.tool_use_id === version.tool_use_id,
      )?.results,
  );

  /** Runs every script Claude asks for and sends the result, until it answers. */
  const settle = async (state: StrategyConversation) => {
    let next = state;
    let tested = versions.length;
    while (next.status === "needs_run" && next.pending_run) {
      const current = chart();
      if (!current) throw new Error(t("ia.noChart"));
      setBusy("running");
      const pending = next.pending_run;
      const run = runStrategy(pending.code, current.bars, current.context);
      // A diagnostic only finds a cause: it is not a version and is not drawn.
      const keep = run.ok && !pending.diagnostic;
      if (keep) {
        tested += 1;
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
        keep ? resultsOf(run.report, current, tested) : undefined,
      );
      setConversation(next);
    }
  };

  const send = async (text: string, retry = false) => {
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
          )
        : await api.start(
            text,
            language === "ko" ? "ko" : "en",
            pineReference("strategy"),
            contextNow(current),
            originNow(),
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

  /** Shows what would be sent: the editor's script, backtested on the chart as it is now. */
  const prepareResults = () => {
    const current = chart();
    if (!current) {
      setProblem(t("ia.noChart"));
      return;
    }
    const run = runStrategy(code, current.bars, current.context);
    if (!run.ok) {
      setProblem(run.error);
      return;
    }
    setProblem(null);
    const at = versions.findIndex((version) => version.code === code);
    setDraft(resultsOf(run.report, current, at >= 0 ? at + 1 : null));
  };

  const sendResults = async () => {
    const current = chart();
    if (!current || !draft || !conversation) return;
    setBusy("asking");
    setProblem(null);
    try {
      const answered = await api.sendResults(
        conversation.conversation_id,
        question.trim() || t("sr.reviewQuestion"),
        contextNow(current),
        draft,
      );
      setConversation(answered);
      setQuestion("");
      setDraft(null);
      await settle(answered);
    } catch (error) {
      fail(error);
    } finally {
      setBusy(null);
    }
  };

  /** The latest conversion's rules, run by the API on these bars beside the Pine script. */
  const compare = async (conversationId: string) => {
    const current = chart();
    const run = current && runStrategy(code, current.bars, current.context);
    if (!current || !run?.ok) {
      setProblem(run && !run.ok ? run.error : t("ia.noChart"));
      return;
    }
    const settings = compilePine(code).strategy;
    if (!settings) return;
    setConversation(
      await api.compare(
        conversationId,
        comparisonRequest(code, run.report, settings, current),
      ),
    );
  };

  const convert = async () => {
    if (!conversation) return;
    setBusy("asking");
    setProblem(null);
    try {
      const converted = await api.convert(
        conversation.conversation_id,
        code,
        pineConditions(code),
        language === "ko" ? "ko" : "en",
      );
      setConversation(converted);
      const latest = [...converted.events]
        .reverse()
        .find((event) => event.type === "conversion")?.conversion;
      if (latest?.definition) await compare(converted.conversation_id);
    } catch (error) {
      fail(error);
    } finally {
      setBusy(null);
    }
  };

  const saveRules = async () => {
    if (!conversation) return;
    setBusy("asking");
    setProblem(null);
    try {
      setConversation(
        await api.saveRules(
          conversation.conversation_id,
          code,
          ruleName.trim() || compilePine(code).title,
        ),
      );
    } catch (error) {
      fail(error);
    } finally {
      setBusy(null);
    }
  };

  const latestEvent = (type: string) =>
    [...events].reverse().find((event) => event.type === type);
  const conversion = latestEvent("conversion")?.conversion;
  const comparison = latestEvent("comparison")?.comparison ?? null;
  const saved = latestEvent("conversion_saved");

  const retry =
    conversation?.status === "stopped" &&
    !events.some(
      (event) => event.type === "question" && event.model?.includes("opus"),
    );
  const waiting = conversation?.status === "needs_run";
  const runnable = checked?.ok === true;
  const choosing = !conversation && !conversationId;
  const fallbackQuestion =
    choosing && start.kind === "tradingview" && code.trim()
      ? t("sr.portQuestion")
      : "";
  const request =
    choosing && start.kind === "tradingview" && !code.trim()
      ? ""
      : question.trim() || fallbackQuestion;

  return (
    <section className="ia-panel" aria-label={t("sr.title")}>
      <p className="workspace-note">{t("sr.note")}</p>
      {choosing ? (
        <div className="ia-start">
          <label>
            {t("ia.start")}
            <select
              aria-label={t("ia.start")}
              value={start.kind}
              onChange={(event) => {
                const kind = event.target.value as Start["kind"];
                if (kind === "saved") {
                  chooseStart({ kind, key: scripts[0]?.key ?? "" });
                } else {
                  chooseStart({ kind });
                }
              }}
            >
              <option value="new">{t("sr.startNew")}</option>
              <option value="saved" disabled={scripts.length === 0}>
                {t("sr.startSaved")}
              </option>
              <option value="tradingview">{t("ia.startTradingView")}</option>
            </select>
          </label>
          {start.kind === "saved" ? (
            <label>
              {t("sr.savedPick")}
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
          {start.kind === "tradingview" ? (
            <p className="workspace-note">{t("ia.tradingViewNote")}</p>
          ) : null}
        </div>
      ) : null}
      <div className="ia-events">
        {events.flatMap((event, index) =>
          ["conversion", "comparison", "conversion_saved"].includes(event.type)
            ? []
            : [
                <div key={index}>
                  <AssistantEventView
                    event={event}
                    version={versions.indexOf(event) + 1}
                  />
                  {event.type === "question" && event.results ? (
                    <small className="ia-attached">
                      {t("sr.resultsSent", {
                        trades: event.results.closed_trades,
                      })}
                    </small>
                  ) : null}
                </div>,
              ],
        )}
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
            onClick={() => void send(t("ia.retryQuestion"), true)}
          >
            {t("ia.retryOpus")}
          </button>
          <span className="workspace-note">{t("sr.retryNote")}</span>
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
      {draft ? (
        <section className="sr-draft" aria-label={t("sr.draftTitle")}>
          <strong>{t("sr.draftTitle")}</strong>
          <p className="workspace-note">
            {t("sr.draftNote", {
              trades: draft.trades.length,
              total: draft.closed_trades,
            })}
          </p>
          <pre className="bt-logs">{JSON.stringify(draft, null, 2)}</pre>
          <p className="bt-actions">
            <button
              type="button"
              className="tb-button bt-primary"
              disabled={busy !== null}
              onClick={() => void sendResults()}
            >
              {t("sr.sendDraft")}
            </button>
            <button
              type="button"
              className="tb-button"
              onClick={() => setDraft(null)}
            >
              {t("sr.cancelDraft")}
            </button>
          </p>
        </section>
      ) : null}
      <label className="rv-question">
        {t("ia.request")}
        <textarea
          value={question}
          maxLength={4000}
          placeholder={t(draft ? "sr.reviewQuestion" : "sr.placeholder")}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            if (
              event.key === "Enter" &&
              (event.metaKey || event.ctrlKey) &&
              request &&
              busy === null &&
              !draft
            ) {
              void send(request);
            }
          }}
        />
      </label>
      <p className="bt-actions">
        <button
          type="button"
          className="tb-button bt-primary"
          disabled={busy !== null || !request || draft !== null}
          onClick={() => void send(request)}
        >
          {busy !== null ? t("rp.sending") : t("ia.send")}
        </button>
        <button
          type="button"
          className="tb-button"
          disabled={
            busy !== null || !conversation || !runnable || draft !== null
          }
          title={conversation ? undefined : t("sr.resultsNeedChat")}
          onClick={prepareResults}
        >
          {t("sr.sendResults")}
        </button>
        <button
          type="button"
          className="tb-button"
          disabled={busy !== null || !conversation || !runnable}
          title={t("sr.convertNote")}
          onClick={() => void convert()}
        >
          {t("sr.convert")}
        </button>
      </p>
      {conversion ? (
        <ConversionView
          conversion={conversion}
          comparison={comparison}
          saved={saved?.strategy_id ? (saved.strategy_version ?? 1) : null}
          name={ruleName}
          onName={setRuleName}
          busy={busy !== null}
          onCompare={
            conversion.definition && conversation
              ? () => {
                  setProblem(null);
                  void compare(conversation.conversation_id).catch(fail);
                }
              : null
          }
          onSave={() => void saveRules()}
        />
      ) : null}
      {results.some(Boolean) ? (
        <table className="bt-table sr-versions" aria-label={t("sr.versions")}>
          <thead>
            <tr>
              <th>{t("sr.version")}</th>
              <th>{t("pine.strategy.netProfit")}</th>
              <th>{t("pine.strategy.closed")}</th>
              <th>{t("pine.strategy.winRate")}</th>
              <th>{t("pine.strategy.profitFactor")}</th>
            </tr>
          </thead>
          <tbody>
            {results.map((item, index) =>
              item ? (
                <tr key={index}>
                  <td>v{index + 1}</td>
                  <td>
                    {item.net_profit} ({item.net_profit_percent}%)
                  </td>
                  <td>{item.closed_trades}</td>
                  <td>{item.win_rate ?? "—"}</td>
                  <td>{item.profit_factor ?? "—"}</td>
                </tr>
              ) : null,
            )}
          </tbody>
        </table>
      ) : null}
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
          {applied ? t("sr.applyUpdate") : t("sr.apply")}
        </button>
      </p>
    </section>
  );
}

/** Where each Pine condition went, what stops the conversion, and how the rules behaved. */
function ConversionView({
  conversion,
  comparison,
  saved,
  name,
  onName,
  busy,
  onCompare,
  onSave,
}: {
  conversion: Conversion;
  comparison: Comparison | null;
  saved: number | null;
  name: string;
  onName: (name: string) => void;
  busy: boolean;
  onCompare: (() => void) | null;
  onSave: () => void;
}) {
  const { t } = useI18n();
  // The API decides on saving; this only spares a request it would refuse.
  const savable =
    conversion.complete &&
    !conversion.definition?.entry_order &&
    !conversion.definition?.exit_order &&
    comparison !== null &&
    comparison.blocks.length === 0;
  return (
    <section className="sr-draft" aria-label={t("sr.conversion")}>
      <strong>{t("sr.conversion")}</strong>
      <p className={conversion.complete ? "workspace-note" : "bt-problem"}>
        {t(conversion.complete ? "sr.conversionComplete" : "sr.conversionOpen")}
      </p>
      <table className="bt-table sr-versions">
        <thead>
          <tr>
            <th>{t("sr.condition")}</th>
            <th>{t("sr.conditionWhere")}</th>
          </tr>
        </thead>
        <tbody>
          {conversion.mapping.map((item) => (
            <tr key={item.index}>
              <td>{item.condition}</td>
              <td>
                {item.status === "mapped"
                  ? item.rule_paths.join(", ")
                  : `${t("sr.unconvertible")}: ${item.reason}`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {conversion.problems.length > 0 ? (
        <ul className="sr-problems" aria-label={t("sr.problems")}>
          {conversion.problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      ) : null}
      {conversion.notes ? (
        <p className="workspace-note">{conversion.notes}</p>
      ) : null}
      {conversion.definition ? (
        <details>
          <summary>{t("sr.ruleJson")}</summary>
          <pre className="bt-logs">
            {JSON.stringify(conversion.definition, null, 2)}
          </pre>
        </details>
      ) : null}
      {comparison ? (
        <table className="bt-table sr-versions" aria-label={t("sr.compare")}>
          <thead>
            <tr>
              <th />
              <th>Pine</th>
              <th>{t("sr.rules")}</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>{t("pine.strategy.closed")}</td>
              <td>{comparison.pine_trades}</td>
              <td>{comparison.rule_trades}</td>
            </tr>
            <tr>
              <td>{t("pine.strategy.netProfit")}</td>
              <td>{comparison.pine_net.toFixed(2)}</td>
              <td>{comparison.rule_net.toFixed(2)}</td>
            </tr>
            <tr>
              <td>{t("sr.entryMatch")}</td>
              <td colSpan={2}>{comparison.entry_match.toFixed(0)}%</td>
            </tr>
          </tbody>
        </table>
      ) : null}
      {comparison?.blocks.map((block) => (
        <p key={block} className="bt-problem" role="alert">
          {t(`sr.block.${block}` as MessageKey)}
        </p>
      ))}
      {savable ? <p className="workspace-note">{t("sr.sameOnBars")}</p> : null}
      <p className="bt-actions">
        {onCompare ? (
          <button
            type="button"
            className="tb-button"
            disabled={busy}
            onClick={onCompare}
          >
            {t("sr.compareRun")}
          </button>
        ) : null}
      </p>
      <label>
        {t("sr.ruleName")}
        <input
          value={name}
          maxLength={200}
          disabled={!savable}
          onChange={(event) => onName(event.target.value)}
        />
      </label>
      <p className="bt-actions">
        <button
          type="button"
          className="tb-button bt-primary"
          disabled={!savable || busy}
          onClick={onSave}
        >
          {t("sr.saveRules")}
        </button>
        <span className="workspace-note">
          {saved !== null
            ? t("sr.saved", { version: saved })
            : t(savable ? "sr.saveNote" : "sr.saveBlocked")}
        </span>
      </p>
    </section>
  );
}
