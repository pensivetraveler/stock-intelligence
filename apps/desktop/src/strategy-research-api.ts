import type {
  StrategyBacktestPreview,
  StrategyConversation,
} from "@siw/contracts/types";

import type { ChartContext, RunResult } from "./indicator-assistant-api";
import { callForKind, type ApiTarget } from "./market-api";

export type ResearchEvent = StrategyConversation["events"][number];
export type StrategyResults = NonNullable<ResearchEvent["results"]>;

export type Conversion = NonNullable<ResearchEvent["conversion"]>;

/** A rules strategy backtested on the chart's own bars; nothing is stored. */
export interface PreviewRequest {
  definition: object;
  bars: {
    time: number;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
  }[];
  time_zone: string;
  currency: string;
  initial_capital: number;
  fee_rate: number;
  fee_per_order: number;
  slippage_bps: number;
}

/** What a first request starts from; the code itself goes as the editor's code. */
export interface ResearchOrigin {
  kind: NonNullable<ResearchEvent["origin"]>;
  detail?: string;
}

/** Claude researching Pine strategies with the investor (ADR 0042, strategy step 2). */
export interface StrategyResearchApi {
  start(
    question: string,
    language: "en" | "ko",
    reference: string,
    context: ChartContext,
    origin?: ResearchOrigin,
  ): Promise<StrategyConversation>;
  conversation(conversationId: string): Promise<StrategyConversation>;
  /** `retry`: this request alone goes to Opus after five failed fixes. */
  ask(
    conversationId: string,
    question: string,
    context: ChartContext,
    retry?: boolean,
  ): Promise<StrategyConversation>;
  /** The only way profit and trades reach Claude; the investor presses it. */
  sendResults(
    conversationId: string,
    question: string,
    context: ChartContext,
    results: StrategyResults,
  ): Promise<StrategyConversation>;
  sendRun(
    conversationId: string,
    toolUseId: string,
    result: RunResult,
    results?: StrategyResults,
  ): Promise<StrategyConversation>;
  /** `conditions` are the app parser's, so Claude cannot leave one out unnoticed. */
  convert(
    conversationId: string,
    code: string,
    conditions: string[],
    language: "en" | "ko",
  ): Promise<StrategyConversation>;
  previewBacktest(request: PreviewRequest): Promise<StrategyBacktestPreview>;
}

export function createStrategyResearchApi(
  baseUrl: string,
  fetcher: typeof fetch = fetch,
  // One step may run several Claude calls, each up to 180 s at the API.
  timeoutMs = 400_000,
): StrategyResearchApi {
  const target: ApiTarget = { baseUrl, fetcher, timeoutMs };
  const KIND = "strategy_conversation";
  const path = (conversationId: string) =>
    `/v1/strategy-research/conversations/${encodeURIComponent(conversationId)}`;
  const post = (url: string, body: Record<string, unknown>) =>
    callForKind<StrategyConversation>(target, url, KIND, {
      method: "POST",
      body,
    });
  return {
    start(question, language, reference, context, origin) {
      return post("/v1/strategy-research/conversations", {
        question,
        language,
        reference,
        context,
        ...(origin ? { origin } : {}),
      });
    },
    conversation(conversationId) {
      return callForKind<StrategyConversation>(
        target,
        path(conversationId),
        KIND,
      );
    },
    ask(conversationId, question, context, retry) {
      return post(`${path(conversationId)}/messages`, {
        question,
        context,
        ...(retry ? { retry } : {}),
      });
    },
    sendResults(conversationId, question, context, results) {
      return post(`${path(conversationId)}/results`, {
        question,
        context,
        results,
      });
    },
    convert(conversationId, code, conditions, language) {
      return post(`${path(conversationId)}/convert`, {
        code,
        conditions,
        language,
      });
    },
    previewBacktest(request) {
      return callForKind<StrategyBacktestPreview>(
        target,
        "/v1/strategy-research/backtest-preview",
        "strategy_backtest_preview",
        { method: "POST", body: request },
      );
    },
    sendRun(conversationId, toolUseId, result, results) {
      return post(`${path(conversationId)}/runs`, {
        tool_use_id: toolUseId,
        ok: result.ok,
        error: result.error ?? null,
        summary: result.summary ?? null,
        ...(results ? { results } : {}),
      });
    },
  };
}
