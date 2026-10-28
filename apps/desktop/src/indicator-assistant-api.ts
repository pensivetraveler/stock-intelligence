import type {
  IndicatorAssistantSettings,
  IndicatorConversation,
} from "@siw/contracts/types";

import { callForKind, type ApiTarget } from "./market-api";

export type AssistantModel = IndicatorAssistantSettings["model"];
export type AssistantEvent = IndicatorConversation["events"][number];

/** The chart and editor as they are when a request is sent (never bar values). */
export interface ChartContext {
  symbol: string;
  timeframe: string;
  bars: number;
  code: string | null;
}

/** The result of running Claude's script on the chart, posted back to Claude. */
export interface RunResult {
  ok: boolean;
  error?: string;
  summary?: string;
}

/** Claude writing Pine indicators with the investor (ADR 0042, indicators step 1). */
export interface IndicatorAssistantApi {
  settings(): Promise<IndicatorAssistantSettings>;
  setModel(model: AssistantModel): Promise<IndicatorAssistantSettings>;
  start(
    question: string,
    language: "en" | "ko",
    reference: string,
    context: ChartContext,
  ): Promise<IndicatorConversation>;
  conversation(conversationId: string): Promise<IndicatorConversation>;
  /** `model` only for a retry on another model; the request alone uses it. */
  ask(
    conversationId: string,
    question: string,
    context: ChartContext,
    model?: AssistantModel,
  ): Promise<IndicatorConversation>;
  sendRun(
    conversationId: string,
    toolUseId: string,
    result: RunResult,
  ): Promise<IndicatorConversation>;
}

export function createIndicatorAssistantApi(
  baseUrl: string,
  fetcher: typeof fetch = fetch,
  // One step may run several Claude calls, each up to 180 s at the API.
  timeoutMs = 400_000,
): IndicatorAssistantApi {
  const target: ApiTarget = { baseUrl, fetcher, timeoutMs };
  const path = (conversationId: string) =>
    `/v1/indicator-assistant/conversations/${encodeURIComponent(conversationId)}`;
  return {
    settings() {
      return callForKind<IndicatorAssistantSettings>(
        target,
        "/v1/indicator-assistant/settings",
        "indicator_assistant_settings",
      );
    },
    setModel(model) {
      return callForKind<IndicatorAssistantSettings>(
        target,
        "/v1/indicator-assistant/settings",
        "indicator_assistant_settings",
        { method: "PUT", body: { model } },
      );
    },
    start(question, language, reference, context) {
      return callForKind<IndicatorConversation>(
        target,
        "/v1/indicator-assistant/conversations",
        "indicator_conversation",
        { method: "POST", body: { question, language, reference, context } },
      );
    },
    conversation(conversationId) {
      return callForKind<IndicatorConversation>(
        target,
        path(conversationId),
        "indicator_conversation",
      );
    },
    ask(conversationId, question, context, model) {
      return callForKind<IndicatorConversation>(
        target,
        `${path(conversationId)}/messages`,
        "indicator_conversation",
        {
          method: "POST",
          body: { question, context, ...(model ? { model } : {}) },
        },
      );
    },
    sendRun(conversationId, toolUseId, result) {
      return callForKind<IndicatorConversation>(
        target,
        `${path(conversationId)}/runs`,
        "indicator_conversation",
        {
          method: "POST",
          body: {
            tool_use_id: toolUseId,
            ok: result.ok,
            error: result.error ?? null,
            summary: result.summary ?? null,
          },
        },
      );
    },
  };
}
