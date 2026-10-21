import type {
  AgentAnalysis,
  AgentAnalysisListResponse,
} from "@siw/contracts/types";

import { callApi, callForKind, type ApiTarget } from "./market-api";

export interface ExecutionRef {
  ledger: "manual" | "imported" | "broker";
  execution_id: string;
}

export interface ChartReviewRequest {
  instrumentId: string;
  timeframe: string;
  /** End of the clicked candle: nothing later is read. */
  cutoffUtc: string;
}

export interface PublicSourceReviewRequest {
  instrumentId: string;
  /** Nothing published after this is used (except flagged hindsight filings). */
  cutoffUtc: string;
}

/**
 * Review Agent client (ADR 0022, 0024). Evidence is computed locally by the API; Claude is
 * asked only by `askClaude`, with exactly what `payload` returns.
 */
export interface AgentApi {
  reviewChart(request: ChartReviewRequest): Promise<AgentAnalysis>;
  reviewTrades(
    instrumentId: string,
    executions: ExecutionRef[],
  ): Promise<AgentAnalysis>;
  reviewBacktest(runId: string): Promise<AgentAnalysis>;
  /** SEC filings known at the cutoff (ADR 0024). */
  reviewFilings(
    request: PublicSourceReviewRequest & {
      windowDays?: number;
      afterDays?: number;
    },
  ): Promise<AgentAnalysis>;
  /** FINRA weekly ATS volume published before the cutoff (ADR 0024). */
  reviewDarkPool(
    request: PublicSourceReviewRequest & { weeks?: number },
  ): Promise<AgentAnalysis>;
  analyses(): Promise<AgentAnalysis[]>;
  payload(analysisId: string): Promise<unknown>;
  askClaude(analysisId: string, language: "en" | "ko"): Promise<AgentAnalysis>;
}

export function createAgentApi(
  baseUrl: string,
  fetcher: typeof fetch = fetch,
  timeoutMs = 70_000,
): AgentApi {
  const target: ApiTarget = { baseUrl, fetcher, timeoutMs };
  const analysis = (path: string, body: unknown) =>
    callForKind<AgentAnalysis>(target, path, "agent_analysis", { body });
  const path = (analysisId: string) =>
    `/v1/agents/analyses/${encodeURIComponent(analysisId)}`;
  return {
    reviewChart({ instrumentId, timeframe, cutoffUtc }) {
      return analysis("/v1/agents/review/chart", {
        instrument_id: instrumentId,
        timeframe,
        cutoff_utc: cutoffUtc,
      });
    },
    reviewTrades(instrumentId, executions) {
      return analysis("/v1/agents/review/trades", {
        instrument_id: instrumentId,
        executions,
      });
    },
    reviewBacktest(runId) {
      return analysis("/v1/agents/review/backtest", { run_id: runId });
    },
    reviewFilings({ instrumentId, cutoffUtc, windowDays = 30, afterDays = 0 }) {
      return analysis("/v1/agents/review/filings", {
        instrument_id: instrumentId,
        cutoff_utc: cutoffUtc,
        window_days: windowDays,
        after_days: afterDays,
      });
    },
    reviewDarkPool({ instrumentId, cutoffUtc, weeks = 26 }) {
      return analysis("/v1/agents/review/dark-pool", {
        instrument_id: instrumentId,
        cutoff_utc: cutoffUtc,
        weeks,
      });
    },
    async analyses() {
      const response = await callForKind<AgentAnalysisListResponse>(
        target,
        "/v1/agents/analyses",
        "agent_analysis_list_response",
      );
      return response.analyses;
    },
    payload(analysisId) {
      return callApi(target, `${path(analysisId)}/payload`);
    },
    askClaude(analysisId, language) {
      return analysis(`${path(analysisId)}/commentary`, { language });
    },
  };
}
