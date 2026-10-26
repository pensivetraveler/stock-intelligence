import type {
  ReviewAnnotation,
  ReviewAssignmentResult,
  ReviewGroup,
  ReviewStrategyRule,
  ReviewTradeListResponse,
} from "@siw/contracts/types";

import type { ExecutionRef } from "./agent-api";
import { callApi, callForKind, type ApiTarget } from "./market-api";

export type ReviewTrade = ReviewTradeListResponse["trades"][number];
export type ReviewRule = ReviewStrategyRule["rule"];
export type ReviewLedger = ReviewTradeListResponse["ledger"];

export interface TradeFilter {
  ledger: ReviewLedger;
  instrumentId?: string;
  /** A strategy id, or "none" for trades without one. */
  strategyId?: string;
  status?: ReviewTrade["status"];
  reviewed?: boolean;
  tag?: string;
  limit?: number;
  offset?: number;
}

export interface AnnotationRequest {
  note: string;
  tags: string[];
  reviewed: boolean;
}

/**
 * Trade review client (ADR 0042). Trades are grouped by the API from the journal; this
 * client only reads them and writes labels (strategy, rule, groups, notes).
 */
export interface ReviewApi {
  trades(filter: TradeFilter): Promise<ReviewTradeListResponse>;
  assign(
    executions: ExecutionRef[],
    strategyId: string | null,
  ): Promise<ReviewAssignmentResult>;
  setRule(strategyId: string, rule: ReviewRule): Promise<ReviewStrategyRule>;
  makeGroup(executions: ExecutionRef[]): Promise<ReviewGroup>;
  removeGroup(groupId: string): Promise<void>;
  annotate(
    tradeId: string,
    annotation: AnnotationRequest,
  ): Promise<ReviewAnnotation>;
}

export function createReviewApi(
  baseUrl: string,
  fetcher: typeof fetch = fetch,
  timeoutMs = 60_000,
): ReviewApi {
  const target: ApiTarget = { baseUrl, fetcher, timeoutMs };
  return {
    trades(filter) {
      const query = new URLSearchParams({ ledger: filter.ledger });
      if (filter.instrumentId) query.set("instrument_id", filter.instrumentId);
      if (filter.strategyId) query.set("strategy_id", filter.strategyId);
      if (filter.status) query.set("status", filter.status);
      if (filter.reviewed !== undefined) {
        query.set("reviewed", String(filter.reviewed));
      }
      if (filter.tag) query.set("tag", filter.tag);
      if (filter.limit) query.set("limit", String(filter.limit));
      if (filter.offset) query.set("offset", String(filter.offset));
      return callForKind<ReviewTradeListResponse>(
        target,
        `/v1/review/trades?${query.toString()}`,
        "review_trade_list",
      );
    },
    assign(executions, strategyId) {
      return callForKind<ReviewAssignmentResult>(
        target,
        "/v1/review/assignments",
        "review_assignment_result",
        { method: "PUT", body: { executions, strategy_id: strategyId } },
      );
    },
    setRule(strategyId, rule) {
      return callForKind<ReviewStrategyRule>(
        target,
        `/v1/review/strategy-rules/${encodeURIComponent(strategyId)}`,
        "review_strategy_rule",
        { method: "PUT", body: { rule } },
      );
    },
    makeGroup(executions) {
      return callForKind<ReviewGroup>(
        target,
        "/v1/review/groups",
        "review_group",
        { method: "POST", body: { executions } },
      );
    },
    async removeGroup(groupId) {
      await callApi(
        target,
        `/v1/review/groups/${encodeURIComponent(groupId)}`,
        {
          method: "DELETE",
        },
      );
    },
    annotate(tradeId, annotation) {
      // Trade ids contain ":" ("flat:broker:<id>"); the route takes them as a path.
      const path = tradeId.split("/").map(encodeURIComponent).join("/");
      return callForKind<ReviewAnnotation>(
        target,
        `/v1/review/trades/${path}/annotation`,
        "review_annotation",
        { method: "PUT", body: annotation },
      );
    },
  };
}

/** The refs of a trade's executions, each once. */
export function executionRefs(trade: ReviewTrade): ExecutionRef[] {
  const seen = new Set<string>();
  const refs: ExecutionRef[] = [];
  for (const item of trade.executions) {
    const key = `${item.ledger}:${item.execution_id}`;
    if (seen.has(key)) continue;
    seen.add(key);
    refs.push({ ledger: item.ledger, execution_id: item.execution_id });
  }
  return refs;
}

/** The manual group a trade was made from, if any ("manual:group-<id>"). */
export function manualGroupOf(trade: ReviewTrade): string | null {
  const prefix = "manual:group-";
  return trade.trade_id.startsWith(prefix)
    ? trade.trade_id.slice(prefix.length)
    : null;
}
