import type {
  ReviewAccountCapitalList,
  ReviewAnnotation,
  ReviewAssignmentResult,
  ReviewConversation,
  ReviewGroup,
  ReviewPeriod,
  ReviewPeriodCommentary,
  ReviewPeriodCommentaryList,
  ReviewPeriodList,
  ReviewPeriodPayload,
  ReviewCheckStatus,
  ReviewPrincipleCheck,
  ReviewPrincipleDraft,
  ReviewPrinciples,
  ReviewReportDraft,
  ReviewSettings,
  ReviewStrategySuggestions,
  ReviewWhatIf,
  ReviewStrategyRule,
  ReviewTradeListResponse,
  ReviewTradeReport,
  ReviewTradeReportList,
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
  /** Stored principle checks: any broken, none broken, or not checked yet. */
  principles?: "broken" | "kept" | "unchecked";
  limit?: number;
  offset?: number;
}

export type ReportModel = ReviewSettings["report_model"];
export type ReportImage = ReviewReportDraft["images"][number];
export type ToolImage = ReviewConversation["pending_images"][number];
export type PrincipleKind = ReviewPrincipleCheck["results"][number]["kind"];

/** One principle as edited before saving (item ids are given by the API). */
export interface PrincipleInput {
  scope: string;
  kind: string;
  value: string | null;
  text: string;
}

export interface CapitalRequest {
  accountRef: string;
  currency: "USD" | "KRW";
  /** YYYY-MM-DD: the capital applies to trades opened from this day. */
  effectiveFrom: string;
  amount: string;
}

export interface PeriodRequest {
  fromDate: string;
  toDate: string;
  ledger: ReviewLedger;
  accountRef?: string;
  /** A strategy id, or "none" for trades without one. */
  strategyId?: string;
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
  settings(): Promise<ReviewSettings>;
  setReportModel(model: ReportModel): Promise<ReviewSettings>;
  capital(): Promise<ReviewAccountCapitalList>;
  addCapital(request: CapitalRequest): Promise<ReviewAccountCapitalList>;
  removeCapital(entryId: string): Promise<void>;
  /** Facts and chart bars for a report; nothing is sent to Claude yet. */
  draftReport(
    tradeId: string,
    includeNote: boolean,
  ): Promise<ReviewReportDraft>;
  /** Sends the draft and its rendered images (base64 PNG, in the draft's order). */
  sendReport(
    draftId: string,
    language: "en" | "ko",
    images: { kind: ReportImage["kind"]; png_base64: string }[],
  ): Promise<ReviewTradeReport>;
  reports(tradeId: string): Promise<ReviewTradeReport[]>;
  /** The follow-up conversation of a report (empty before the first question). */
  conversation(reportId: string): Promise<ReviewConversation>;
  /** Asks a question; may come back waiting for chart images the app must draw. */
  askQuestion(
    reportId: string,
    question: string,
    language: "en" | "ko",
  ): Promise<ReviewConversation>;
  sendChartImages(
    conversationId: string,
    images: { tool_use_id: string; png_base64: string }[],
  ): Promise<ReviewConversation>;
  principles(): Promise<ReviewPrinciples>;
  /** Claude's proposed items for the investor's text; nothing is saved. */
  draftPrinciples(
    text: string,
    language: "en" | "ko",
  ): Promise<ReviewPrincipleDraft>;
  /** Saves the whole set as a new version. */
  savePrinciples(
    items: PrincipleInput[],
    sourceText: string,
  ): Promise<ReviewPrinciples>;
  principleCheck(tradeId: string): Promise<ReviewPrincipleCheck>;
  /** Hindsight replays: by the principles' stop/target and by a strategy. */
  whatIf(tradeId: string, strategyId?: string): Promise<ReviewWhatIf>;
  strategySuggestions(tradeId: string): Promise<ReviewStrategySuggestions>;
  checkStatus(): Promise<ReviewCheckStatus>;
  /** Queues principle checks of trades opened between the dates. */
  runChecks(fromDate: string, toDate: string): Promise<ReviewCheckStatus>;
  /** Computes and saves a period review (statistics only; no Claude). */
  makePeriodReview(request: PeriodRequest): Promise<ReviewPeriod>;
  periodReviews(): Promise<ReviewPeriodList>;
  periodReview(reviewId: string): Promise<ReviewPeriod>;
  /** Exactly what a commentary request would send; nothing is sent. */
  periodPayload(reviewId: string): Promise<ReviewPeriodPayload>;
  makePeriodCommentary(
    reviewId: string,
    language: "en" | "ko",
  ): Promise<ReviewPeriodCommentary>;
  periodCommentaries(reviewId: string): Promise<ReviewPeriodCommentaryList>;
  periodConversation(reviewId: string): Promise<ReviewConversation>;
  askPeriodQuestion(
    reviewId: string,
    question: string,
    language: "en" | "ko",
  ): Promise<ReviewConversation>;
}

// Trade ids contain ":" ("flat:broker:<id>"); the routes take them as a path.
function tradePath(tradeId: string): string {
  return tradeId.split("/").map(encodeURIComponent).join("/");
}

export function createReviewApi(
  baseUrl: string,
  fetcher: typeof fetch = fetch,
  // A report can take Claude a few minutes (the API waits up to 180 s).
  timeoutMs = 200_000,
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
      if (filter.principles) query.set("principles", filter.principles);
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
      return callForKind<ReviewAnnotation>(
        target,
        `/v1/review/trades/${tradePath(tradeId)}/annotation`,
        "review_annotation",
        { method: "PUT", body: annotation },
      );
    },
    settings() {
      return callForKind<ReviewSettings>(
        target,
        "/v1/review/settings",
        "review_settings",
      );
    },
    setReportModel(model) {
      return callForKind<ReviewSettings>(
        target,
        "/v1/review/settings",
        "review_settings",
        { method: "PUT", body: { report_model: model } },
      );
    },
    capital() {
      return callForKind<ReviewAccountCapitalList>(
        target,
        "/v1/review/account-capital",
        "review_account_capital_list",
      );
    },
    addCapital({ accountRef, currency, effectiveFrom, amount }) {
      return callForKind<ReviewAccountCapitalList>(
        target,
        "/v1/review/account-capital",
        "review_account_capital_list",
        {
          method: "POST",
          body: {
            account_ref: accountRef,
            currency,
            effective_from: effectiveFrom,
            amount,
          },
        },
      );
    },
    async removeCapital(entryId) {
      await callApi(
        target,
        `/v1/review/account-capital/${encodeURIComponent(entryId)}`,
        { method: "DELETE" },
      );
    },
    draftReport(tradeId, includeNote) {
      return callForKind<ReviewReportDraft>(
        target,
        `/v1/review/trades/${tradePath(tradeId)}/report-drafts`,
        "review_report_draft",
        { method: "POST", body: { include_note: includeNote } },
      );
    },
    sendReport(draftId, language, images) {
      return callForKind<ReviewTradeReport>(
        target,
        `/v1/review/report-drafts/${encodeURIComponent(draftId)}/report`,
        "review_trade_report",
        { method: "POST", body: { language, images } },
      );
    },
    conversation(reportId) {
      return callForKind<ReviewConversation>(
        target,
        `/v1/review/reports/${encodeURIComponent(reportId)}/conversation`,
        "review_conversation",
      );
    },
    askQuestion(reportId, question, language) {
      return callForKind<ReviewConversation>(
        target,
        `/v1/review/reports/${encodeURIComponent(reportId)}/conversation/messages`,
        "review_conversation",
        { method: "POST", body: { question, language } },
      );
    },
    sendChartImages(conversationId, images) {
      return callForKind<ReviewConversation>(
        target,
        `/v1/review/conversations/${encodeURIComponent(conversationId)}/images`,
        "review_conversation",
        { method: "POST", body: { images } },
      );
    },
    principles() {
      return callForKind<ReviewPrinciples>(
        target,
        "/v1/review/principles",
        "review_principles",
      );
    },
    draftPrinciples(text, language) {
      return callForKind<ReviewPrincipleDraft>(
        target,
        "/v1/review/principles/draft",
        "review_principle_draft",
        { method: "POST", body: { text, language } },
      );
    },
    savePrinciples(items, sourceText) {
      return callForKind<ReviewPrinciples>(
        target,
        "/v1/review/principles",
        "review_principles",
        { method: "PUT", body: { items, source_text: sourceText } },
      );
    },
    principleCheck(tradeId) {
      return callForKind<ReviewPrincipleCheck>(
        target,
        `/v1/review/trades/${tradePath(tradeId)}/principle-check`,
        "review_principle_check",
      );
    },
    whatIf(tradeId, strategyId) {
      const query = strategyId
        ? `?strategy_id=${encodeURIComponent(strategyId)}`
        : "";
      return callForKind<ReviewWhatIf>(
        target,
        `/v1/review/trades/${tradePath(tradeId)}/what-if${query}`,
        "review_what_if",
      );
    },
    strategySuggestions(tradeId) {
      return callForKind<ReviewStrategySuggestions>(
        target,
        `/v1/review/trades/${tradePath(tradeId)}/strategy-suggestions`,
        "review_strategy_suggestions",
      );
    },
    checkStatus() {
      return callForKind<ReviewCheckStatus>(
        target,
        "/v1/review/principle-checks/status",
        "review_check_status",
      );
    },
    runChecks(fromDate, toDate) {
      return callForKind<ReviewCheckStatus>(
        target,
        "/v1/review/principle-checks/run",
        "review_check_status",
        { method: "POST", body: { from_date: fromDate, to_date: toDate } },
      );
    },
    makePeriodReview({ fromDate, toDate, ledger, accountRef, strategyId }) {
      return callForKind<ReviewPeriod>(
        target,
        "/v1/review/periods",
        "review_period",
        {
          method: "POST",
          body: {
            from_date: fromDate,
            to_date: toDate,
            ledger,
            ...(accountRef ? { account_ref: accountRef } : {}),
            ...(strategyId ? { strategy_id: strategyId } : {}),
          },
        },
      );
    },
    periodReviews() {
      return callForKind<ReviewPeriodList>(
        target,
        "/v1/review/periods",
        "review_period_list",
      );
    },
    periodReview(reviewId) {
      return callForKind<ReviewPeriod>(
        target,
        `/v1/review/periods/${encodeURIComponent(reviewId)}`,
        "review_period",
      );
    },
    periodPayload(reviewId) {
      return callForKind<ReviewPeriodPayload>(
        target,
        `/v1/review/periods/${encodeURIComponent(reviewId)}/payload`,
        "review_period_payload",
      );
    },
    makePeriodCommentary(reviewId, language) {
      return callForKind<ReviewPeriodCommentary>(
        target,
        `/v1/review/periods/${encodeURIComponent(reviewId)}/commentary`,
        "review_period_commentary",
        { method: "POST", body: { language } },
      );
    },
    periodCommentaries(reviewId) {
      return callForKind<ReviewPeriodCommentaryList>(
        target,
        `/v1/review/periods/${encodeURIComponent(reviewId)}/commentaries`,
        "review_period_commentary_list",
      );
    },
    periodConversation(reviewId) {
      return callForKind<ReviewConversation>(
        target,
        `/v1/review/periods/${encodeURIComponent(reviewId)}/conversation`,
        "review_conversation",
      );
    },
    askPeriodQuestion(reviewId, question, language) {
      return callForKind<ReviewConversation>(
        target,
        `/v1/review/periods/${encodeURIComponent(reviewId)}/conversation/messages`,
        "review_conversation",
        { method: "POST", body: { question, language } },
      );
    },
    async reports(tradeId) {
      const response = await callForKind<ReviewTradeReportList>(
        target,
        `/v1/review/trades/${tradePath(tradeId)}/reports`,
        "review_trade_report_list",
      );
      return response.reports;
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
