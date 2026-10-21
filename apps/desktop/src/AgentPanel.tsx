import type { AgentAnalysis } from "@siw/contracts/types";
import { useCallback, useEffect, useMemo, useState } from "react";

import type { AgentApi, ChartReviewRequest, ExecutionRef } from "./agent-api";
import { AgentAnalysisView } from "./AgentAnalysisView";
import type { RealFills } from "./BacktestResultsView";
import { exchangeTime } from "./chart-data";
import { useI18n } from "./i18n";
import { MarketApiError } from "./market-api";
import { zoneOf } from "./market-zone";

interface Row extends ExecutionRef {
  side: string;
  quantity: string;
  price: string;
  time: string | null;
}

/**
 * The review Agent (ADR 0022): the active chart now (or at the replay time), chosen real
 * executions, or a candle asked about from the chart; plus SEC filings and FINRA weekly
 * ATS volume known at that time (ADR 0024). Read-only: it records nothing.
 */
export function AgentPanel({
  api,
  instrumentId,
  timeframe,
  cutoffUtc,
  real,
  request,
  onRequestHandled,
  symbolOf,
}: {
  api: AgentApi;
  instrumentId: string;
  timeframe: string;
  /** "Now" for the chart review: the replay time while replaying. */
  cutoffUtc: () => string;
  real: RealFills;
  /** A candle the user asked about from the chart, handled once. */
  request: ChartReviewRequest | null;
  onRequestHandled: () => void;
  symbolOf: (instrumentId: string) => string;
}) {
  const { t } = useI18n();
  const [analyses, setAnalyses] = useState<AgentAnalysis[]>([]);
  const [current, setCurrent] = useState<AgentAnalysis | null>(null);
  const [chosen, setChosen] = useState<string[]>([]);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [withHindsight, setWithHindsight] = useState(false);
  // SEC EDGAR and FINRA cover US listings only.
  const usListing = !instrumentId.startsWith("kr:");

  const fail = useCallback(
    (error: unknown) =>
      setProblem(
        error instanceof MarketApiError &&
          (error.code === "sec_user_agent_missing" ||
            error.code === "finra_credential_missing")
          ? t(`agent.error.${error.code}`)
          : error instanceof Error
            ? error.message
            : t("common.requestFailed"),
      ),
    [t],
  );
  useEffect(() => {
    api.analyses().then(setAnalyses).catch(fail);
  }, [api, fail]);

  const show = useCallback((analysis: AgentAnalysis) => {
    setCurrent(analysis);
    setAnalyses((list) => [
      analysis,
      ...list.filter((item) => item.analysis_id !== analysis.analysis_id),
    ]);
  }, []);
  const run = useCallback(
    (work: Promise<AgentAnalysis>) => {
      setProblem(null);
      setBusy(true);
      work
        .then(show)
        .catch(fail)
        .finally(() => setBusy(false));
    },
    [show, fail],
  );

  useEffect(() => {
    if (!request) return;
    onRequestHandled();
    run(api.reviewChart(request));
  }, [request, onRequestHandled, run, api]);

  const rows = useMemo<Row[]>(() => {
    const of = (
      ledger: ExecutionRef["ledger"],
      items: {
        execution_id: string;
        instrument_id: string;
        side: string;
        quantity: string;
        price: string;
        executed_at_utc?: string | null;
      }[],
    ) =>
      items
        .filter((item) => item.instrument_id === instrumentId)
        .map((item) => ({
          ledger,
          execution_id: item.execution_id,
          side: item.side,
          quantity: item.quantity,
          price: item.price,
          time: item.executed_at_utc ?? null,
        }));
    return [
      ...of("broker", real.broker),
      ...of("imported", real.imported),
      ...of("manual", real.manual),
    ].sort((a, b) => (b.time ?? "").localeCompare(a.time ?? ""));
  }, [real, instrumentId]);
  const key = (row: ExecutionRef) => `${row.ledger}:${row.execution_id}`;
  const zone = zoneOf(instrumentId);

  return (
    <section className="quote-panel agent-panel" aria-label={t("agent.title")}>
      <h2>{t("agent.title")}</h2>
      <p className="workspace-note">{t("agent.readOnly")}</p>
      {problem ? (
        <p className="bt-problem" role="alert">
          {problem}
        </p>
      ) : null}

      <h3>
        {t("agent.chartNow", { symbol: symbolOf(instrumentId), timeframe })}
      </h3>
      <button
        type="button"
        className="tb-button"
        disabled={busy}
        onClick={() =>
          run(
            api.reviewChart({
              instrumentId,
              timeframe,
              cutoffUtc: cutoffUtc(),
            }),
          )
        }
      >
        {t("agent.reviewChart")}
      </button>

      <h3>{t("agent.trades", { symbol: symbolOf(instrumentId) })}</h3>
      {rows.length === 0 ? (
        <p className="workspace-note">{t("agent.noExecutions")}</p>
      ) : (
        <>
          <ul className="agent-executions" aria-label={t("agent.executions")}>
            {rows.slice(0, 100).map((row) => (
              <li key={key(row)}>
                <label className="bt-inline">
                  <input
                    type="checkbox"
                    checked={chosen.includes(key(row))}
                    onChange={() =>
                      setChosen((list) =>
                        list.includes(key(row))
                          ? list.filter((item) => item !== key(row))
                          : [...list, key(row)],
                      )
                    }
                  />
                  {t(`agent.ledger.${row.ledger}`)} · {row.side} {row.quantity}{" "}
                  @ {row.price} ·{" "}
                  {row.time ? exchangeTime(row.time, zone) : t("agent.noTime")}
                </label>
              </li>
            ))}
          </ul>
          <button
            type="button"
            className="tb-button"
            disabled={busy || chosen.length === 0}
            onClick={() =>
              run(
                api.reviewTrades(
                  instrumentId,
                  rows
                    .filter((row) => chosen.includes(key(row)))
                    .map(({ ledger, execution_id }) => ({
                      ledger,
                      execution_id,
                    })),
                ),
              )
            }
          >
            {t("agent.reviewTrades", { n: chosen.length })}
          </button>
        </>
      )}

      <h3>{t("agent.filings", { symbol: symbolOf(instrumentId) })}</h3>
      <p className="workspace-note">{t("agent.filingsNote")}</p>
      <label className="bt-inline">
        <input
          type="checkbox"
          checked={withHindsight}
          onChange={(event) => setWithHindsight(event.target.checked)}
        />
        {t("agent.filingsAfter")}
      </label>
      <button
        type="button"
        className="tb-button"
        disabled={busy || !usListing}
        onClick={() =>
          run(
            api.reviewFilings({
              instrumentId,
              cutoffUtc: cutoffUtc(),
              afterDays: withHindsight ? 7 : 0,
            }),
          )
        }
      >
        {t("agent.reviewFilings")}
      </button>

      <h3>{t("agent.darkPool", { symbol: symbolOf(instrumentId) })}</h3>
      <p className="workspace-note">{t("agent.darkPoolNote")}</p>
      <button
        type="button"
        className="tb-button"
        disabled={busy || !usListing}
        onClick={() =>
          run(api.reviewDarkPool({ instrumentId, cutoffUtc: cutoffUtc() }))
        }
      >
        {t("agent.reviewDarkPool")}
      </button>
      {usListing ? null : <p className="workspace-note">{t("agent.usOnly")}</p>}

      {busy ? <p className="workspace-note">{t("agent.working")}</p> : null}
      {current ? (
        <AgentAnalysisView
          api={api}
          analysis={current}
          onUpdated={show}
          symbolOf={symbolOf}
        />
      ) : null}

      {analyses.length > 0 ? (
        <details>
          <summary>{t("agent.recent", { n: analyses.length })}</summary>
          <ul className="agent-recent">
            {analyses.map((item) => (
              <li key={item.analysis_id}>
                <button
                  type="button"
                  className="tb-button"
                  onClick={() => setCurrent(item)}
                >
                  {item.created_at_utc.slice(0, 16).replace("T", " ")} ·{" "}
                  {t(`agent.type.${item.target.type}`)}
                  {item.target.instrument_id
                    ? ` · ${symbolOf(item.target.instrument_id)}`
                    : ""}
                  {item.commentaries.length > 0 ? " · Claude" : ""}
                </button>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}
