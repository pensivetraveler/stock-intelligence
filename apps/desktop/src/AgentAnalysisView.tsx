import type { AgentAnalysis } from "@siw/contracts/types";
import { useState } from "react";

import type { AgentApi } from "./agent-api";
import { translateDynamic, useI18n } from "./i18n";
import { MarketApiError } from "./market-api";
import { zoneOf, type MarketZone } from "./market-zone";

/** "2026-09-16 16:00 ET": the cutoff in the exchange's own date and time. */
function stamp(utc: string, zone: MarketZone): string {
  const text = new Intl.DateTimeFormat("sv-SE", {
    timeZone: zone.timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(utc));
  return `${text} ${zone.label}`;
}

type Fact = AgentAnalysis["facts"][number];

function shown(fact: Fact): string {
  const value = fact.value;
  switch (fact.unit) {
    case "percent":
      return `${Number(value) > 0 ? "+" : ""}${value}%`;
    case "ratio":
    case "price":
    case "currency":
    case "count":
      return value;
    case "days":
      return `${value}d`;
    default:
      return value;
  }
}

/**
 * One stored analysis: the locally computed facts (hindsight marked), what could not be
 * computed, the uncertainty, and any commentary Claude gave on exactly these facts.
 */
export function AgentAnalysisView({
  api,
  analysis,
  onUpdated,
  symbolOf,
}: {
  api: AgentApi;
  analysis: AgentAnalysis;
  onUpdated: (next: AgentAnalysis) => void;
  symbolOf: (instrumentId: string) => string;
}) {
  const { t, language } = useI18n();
  const [payload, setPayload] = useState<string | null>(null);
  const [state, setState] = useState<
    { busy: false; error: string | null } | { busy: true }
  >({ busy: false, error: null });
  const target = analysis.target;
  const label = (code: string) =>
    translateDynamic(t, `agent.fact.${code.split(":")[0]}`, code);
  const title =
    target.type === "chart"
      ? t("agent.target.chart", {
          symbol: symbolOf(target.instrument_id ?? ""),
          timeframe: target.timeframe ?? "",
          time: stamp(analysis.cutoff_utc, zoneOf(target.instrument_id ?? "")),
        })
      : target.type === "trades"
        ? t("agent.target.trades", {
            symbol: symbolOf(target.instrument_id ?? ""),
            n: target.execution_count ?? 0,
          })
        : target.type === "filings" || target.type === "dark_pool"
          ? t(
              target.type === "filings"
                ? "agent.target.filings"
                : "agent.target.dark_pool",
              {
                symbol: symbolOf(target.instrument_id ?? ""),
                time: stamp(
                  analysis.cutoff_utc,
                  zoneOf(target.instrument_id ?? ""),
                ),
              },
            )
          : t("agent.target.backtest", {
              symbols: (target.instrument_ids ?? []).map(symbolOf).join(", "),
              timeframe: target.timeframe ?? "",
            });

  const ask = () => {
    setState({ busy: true });
    api
      .askClaude(analysis.analysis_id, language)
      .then((next) => {
        onUpdated(next);
        setState({ busy: false, error: null });
      })
      .catch((error: unknown) =>
        setState({
          busy: false,
          error:
            error instanceof MarketApiError &&
            error.code === "agent_key_missing"
              ? t("agent.noKey")
              : error instanceof Error
                ? error.message
                : t("common.requestFailed"),
        }),
      );
  };

  return (
    <article className="agent-analysis" aria-label={title}>
      <h3>{title}</h3>
      <p className="workspace-note">
        {target.type === "filings" || target.type === "dark_pool"
          ? t("agent.factsNotePublic")
          : t("agent.factsNote")}
      </p>
      <table className="bt-table agent-facts" aria-label={t("agent.facts")}>
        <tbody>
          {analysis.facts.map((fact, index) => (
            <tr
              key={`${fact.code}:${fact.subject ?? ""}:${index}`}
              title={fact.text}
            >
              <td>
                {label(fact.code)}
                {fact.subject ? ` · ${symbolOf(fact.subject)}` : ""}
                {fact.hindsight ? (
                  <span className="agent-hindsight">
                    {t("agent.hindsight")}
                  </span>
                ) : null}
              </td>
              <td>{shown(fact)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {analysis.missing.length > 0 ? (
        <p className="workspace-note">
          {t("agent.missing", {
            items: [...new Set(analysis.missing.map(label))].join(", "),
          })}
        </p>
      ) : null}
      {analysis.uncertainty.length > 0 ? (
        <ul className="bt-warnings" aria-label={t("agent.uncertainty")}>
          {analysis.uncertainty.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : null}
      <details>
        <summary>{t("agent.sources")}</summary>
        <p className="workspace-note">
          {analysis.agent_version} · {analysis.dataset_revisions.join(", ")}
        </p>
        {analysis.warnings.length > 0 ? (
          <ul>
            {analysis.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        ) : null}
      </details>

      <div className="agent-ask">
        <button
          type="button"
          className="tb-button bt-primary"
          disabled={state.busy}
          onClick={ask}
        >
          {state.busy ? t("agent.asking") : t("agent.askClaude")}
        </button>
        <details
          onToggle={(event) => {
            if ((event.target as HTMLDetailsElement).open && payload === null) {
              api
                .payload(analysis.analysis_id)
                .then((value) => setPayload(JSON.stringify(value, null, 2)))
                .catch(() => setPayload(t("common.requestFailed")));
            }
          }}
        >
          <summary>{t("agent.payload")}</summary>
          <p className="workspace-note">{t("agent.payloadNote")}</p>
          <pre className="bt-logs">{payload ?? "…"}</pre>
        </details>
        {!state.busy && state.error ? (
          <p className="bt-problem" role="alert">
            {state.error}
          </p>
        ) : null}
      </div>

      {analysis.commentaries.map((commentary) => (
        <section
          key={commentary.created_at_utc + commentary.model}
          className="agent-commentary"
          aria-label={t("agent.commentary")}
        >
          <p className="workspace-note">
            {t("agent.commentaryBy", {
              model: commentary.model,
              time: commentary.created_at_utc.slice(0, 16).replace("T", " "),
            })}
          </p>
          <p>{commentary.summary}</p>
          {(
            [
              ["inferences", commentary.inferences],
              ["hypotheses", commentary.hypotheses],
              ["questions", commentary.questions],
            ] as const
          ).map(([kind, items]) =>
            items.length > 0 ? (
              <div key={kind}>
                <h4>{t(`agent.${kind}`)}</h4>
                <ul>
                  {items.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            ) : null,
          )}
          {commentary.uncertainty ? (
            <p className="workspace-note">
              {t("agent.commentaryUncertainty", {
                text: commentary.uncertainty,
              })}
            </p>
          ) : null}
          <p className="workspace-note">{t("agent.notAdvice")}</p>
        </section>
      ))}
    </article>
  );
}
