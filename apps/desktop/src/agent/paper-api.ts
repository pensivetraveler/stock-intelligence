import type {
  BacktestStrategyListResponse,
  CredentialListResponse,
  PaperApproval,
  PaperApprovalListResponse,
  PaperExecutionListResponse,
  PaperIntentListResponse,
  PaperRunnerStatus,
  SymbolSearchResponse,
} from "@siw/contracts/types";

import { callForKind, type ApiTarget } from "../market-api";

/** What the SIW Agent screen sends to approve a paper-trading plan (ADR 0037). */
export interface ApprovalRequest {
  strategy_id: string;
  strategy_version: number;
  instruments: { instrument_id: string; symbol: string }[];
  credential_id: string;
  max_order_amount_krw: string | null;
  market_buffer_bps: string;
  krw_per_usd: string | null;
  fx_source: string | null;
  /** fixed: krw_per_usd; bok_ecos: the runner reads the Bank of Korea rate each day. */
  fx_mode: "fixed" | "bok_ecos";
  equity_basis: string | null;
  max_signal_age_seconds: number;
  /** Seconds after a bar's end before it counts as final; null keeps the source's own. */
  bar_settle_seconds: number | null;
}

/**
 * Paper-trading client for SIW Agent. It can approve, arm and stop; it cannot place
 * orders: only the resident runner does that.
 */
export interface PaperApi {
  status(): Promise<PaperRunnerStatus>;
  approvals(): Promise<PaperApprovalListResponse>;
  intents(limit?: number): Promise<PaperIntentListResponse>;
  executions(limit?: number): Promise<PaperExecutionListResponse>;
  strategies(): Promise<BacktestStrategyListResponse>;
  credentials(): Promise<CredentialListResponse>;
  /** Listings by ticker or name, for choosing a plan's instruments (read-only). */
  searchSymbols(query: string): Promise<SymbolSearchResponse>;
  approve(request: ApprovalRequest): Promise<PaperApproval>;
  arm(approvalId: string): Promise<PaperRunnerStatus>;
  disarm(): Promise<PaperRunnerStatus>;
  kill(reason: string): Promise<PaperRunnerStatus>;
  release(): Promise<PaperRunnerStatus>;
}

export function createPaperApi(
  baseUrl: string,
  fetcher: typeof fetch = fetch,
  timeoutMs = 10_000,
): PaperApi {
  const target: ApiTarget = { baseUrl, fetcher, timeoutMs };
  const status = (path: string, body: unknown = {}) =>
    callForKind<PaperRunnerStatus>(target, path, "paper_runner_status", {
      method: "POST",
      body,
    });
  return {
    status: () =>
      callForKind<PaperRunnerStatus>(
        target,
        "/v1/paper/status",
        "paper_runner_status",
      ),
    approvals: () =>
      callForKind<PaperApprovalListResponse>(
        target,
        "/v1/paper/approvals",
        "paper_approval_list_response",
      ),
    intents: (limit = 100) =>
      callForKind<PaperIntentListResponse>(
        target,
        `/v1/paper/intents?limit=${limit}`,
        "paper_intent_list_response",
      ),
    executions: (limit = 200) =>
      callForKind<PaperExecutionListResponse>(
        target,
        `/v1/paper/executions?limit=${limit}`,
        "paper_execution_list_response",
      ),
    strategies: () =>
      callForKind<BacktestStrategyListResponse>(
        target,
        "/v1/backtests/strategies",
        "backtest_strategy_list_response",
      ),
    credentials: () =>
      callForKind<CredentialListResponse>(
        target,
        "/v1/credentials",
        "credential_list_response",
      ),
    searchSymbols: (query) =>
      callForKind<SymbolSearchResponse>(
        target,
        `/v1/market/symbols?${new URLSearchParams({ q: query, limit: "10" }).toString()}`,
        "symbol_search_response",
      ),
    approve: (request) =>
      callForKind<PaperApproval>(
        target,
        "/v1/paper/approvals",
        "paper_approval",
        {
          method: "POST",
          body: request,
        },
      ),
    arm: (approvalId) => status("/v1/paper/arm", { approval_id: approvalId }),
    disarm: () => status("/v1/paper/disarm"),
    kill: (reason) => status("/v1/paper/kill", { reason }),
    release: () => status("/v1/paper/release"),
  };
}
