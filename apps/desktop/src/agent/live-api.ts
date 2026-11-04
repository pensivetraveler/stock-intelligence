import type {
  LiveConfirmationListResponse,
  PaperApproval,
  PaperApprovalListResponse,
  PaperExecutionListResponse,
  PaperIntentListResponse,
  PaperRunnerStatus,
} from "@siw/contracts/types";

import { callForKind, type ApiTarget } from "../market-api";

/** What the SIW Agent screen sends to approve a live-account plan (ADR 0047). */
export interface LiveApprovalRequest {
  strategy_id: string;
  strategy_version: number;
  instruments: { instrument_id: string; symbol: string }[];
  credential_id: string;
  max_order_amount_usd: string;
  max_daily_orders: number;
  market_buffer_bps: string;
  max_signal_age_seconds: number;
  bar_settle_seconds: number | null;
  unfilled_cancel_seconds: number | null;
  confirm_seconds: number;
}

/**
 * Live-account client for SIW Agent. It approves plans, arms, stops and answers each
 * order the runner asks about; it cannot place orders: only the live runner does that,
 * after the user confirmed the order here.
 */
export interface LiveApi {
  status(): Promise<PaperRunnerStatus>;
  approvals(): Promise<PaperApprovalListResponse>;
  intents(limit?: number): Promise<PaperIntentListResponse>;
  executions(limit?: number): Promise<PaperExecutionListResponse>;
  confirmations(): Promise<LiveConfirmationListResponse>;
  approve(request: LiveApprovalRequest): Promise<PaperApproval>;
  arm(approvalId: string): Promise<PaperRunnerStatus>;
  disarm(): Promise<PaperRunnerStatus>;
  kill(reason: string): Promise<PaperRunnerStatus>;
  release(): Promise<PaperRunnerStatus>;
  confirm(intentId: string): Promise<LiveConfirmationListResponse>;
  decline(intentId: string): Promise<LiveConfirmationListResponse>;
}

export function createLiveApi(
  baseUrl: string,
  fetcher: typeof fetch = fetch,
  timeoutMs = 10_000,
): LiveApi {
  const target: ApiTarget = { baseUrl, fetcher, timeoutMs };
  const status = (path: string, body: unknown = {}) =>
    callForKind<PaperRunnerStatus>(target, path, "paper_runner_status", {
      method: "POST",
      body,
    });
  const answer = (intentId: string, decision: "confirm" | "decline") =>
    callForKind<LiveConfirmationListResponse>(
      target,
      `/v1/live/intents/${encodeURIComponent(intentId)}/${decision}`,
      "live_confirmation_list_response",
      { method: "POST", body: {} },
    );
  return {
    status: () =>
      callForKind<PaperRunnerStatus>(
        target,
        "/v1/live/status",
        "paper_runner_status",
      ),
    approvals: () =>
      callForKind<PaperApprovalListResponse>(
        target,
        "/v1/live/approvals",
        "paper_approval_list_response",
      ),
    intents: (limit = 100) =>
      callForKind<PaperIntentListResponse>(
        target,
        `/v1/live/intents?limit=${limit}`,
        "paper_intent_list_response",
      ),
    executions: (limit = 200) =>
      callForKind<PaperExecutionListResponse>(
        target,
        `/v1/live/executions?limit=${limit}`,
        "paper_execution_list_response",
      ),
    confirmations: () =>
      callForKind<LiveConfirmationListResponse>(
        target,
        "/v1/live/confirmations",
        "live_confirmation_list_response",
      ),
    approve: (request) =>
      callForKind<PaperApproval>(
        target,
        "/v1/live/approvals",
        "paper_approval",
        {
          method: "POST",
          body: request,
        },
      ),
    arm: (approvalId) => status("/v1/live/arm", { approval_id: approvalId }),
    disarm: () => status("/v1/live/disarm"),
    kill: (reason) => status("/v1/live/kill", { reason }),
    release: () => status("/v1/live/release"),
    confirm: (intentId) => answer(intentId, "confirm"),
    decline: (intentId) => answer(intentId, "decline"),
  };
}
