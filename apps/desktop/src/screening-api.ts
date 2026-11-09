import type { ScreeningCandidates, ScreeningCheck } from "@siw/contracts/types";

import { callForKind, type ApiTarget } from "./market-api";

/**
 * Daily candidate screening (ADR 0052 S1): today's candidates from the condition record
 * plus added tickers, and the entry check. Results are stored on the API; nothing here
 * places an order or approves a plan.
 */
export interface ScreeningApi {
  candidates(sessionDate?: string): Promise<ScreeningCandidates>;
  addCandidate(
    symbol: string,
    sessionDate?: string,
  ): Promise<ScreeningCandidates>;
  runEntryCheck(
    symbol: string,
    strategyId: string,
    sessionDate?: string,
  ): Promise<ScreeningCheck>;
  /** Check C: rules-only unless `llm` (kept for the few candidates the user picks). */
  runDilutionCheck(
    symbol: string,
    llm: boolean,
    sessionDate?: string,
  ): Promise<ScreeningCheck>;
  /** A waiting check looked at again; a new result when there is one. */
  refreshCheck(checkId: number): Promise<ScreeningCheck>;
  check(checkId: number): Promise<ScreeningCheck>;
}

export function createScreeningApi(
  baseUrl: string,
  fetcher: typeof fetch = fetch,
  timeoutMs = 60_000,
): ScreeningApi {
  const target: ApiTarget = { baseUrl, fetcher, timeoutMs };
  const dated = (sessionDate?: string) =>
    sessionDate ? { session_date: sessionDate } : {};
  return {
    candidates(sessionDate) {
      const query = sessionDate
        ? `?${new URLSearchParams({ session_date: sessionDate }).toString()}`
        : "";
      return callForKind<ScreeningCandidates>(
        target,
        `/v1/screening/candidates${query}`,
        "screening_candidates",
      );
    },
    addCandidate(symbol, sessionDate) {
      return callForKind<ScreeningCandidates>(
        target,
        "/v1/screening/candidates",
        "screening_candidates",
        { method: "POST", body: { symbol, ...dated(sessionDate) } },
      );
    },
    runEntryCheck(symbol, strategyId, sessionDate) {
      return callForKind<ScreeningCheck>(
        target,
        "/v1/screening/checks/entry",
        "screening_check",
        {
          method: "POST",
          body: { symbol, strategy_id: strategyId, ...dated(sessionDate) },
        },
      );
    },
    runDilutionCheck(symbol, llm, sessionDate) {
      return callForKind<ScreeningCheck>(
        target,
        "/v1/screening/checks/dilution",
        "screening_check",
        { method: "POST", body: { symbol, llm, ...dated(sessionDate) } },
      );
    },
    refreshCheck(checkId) {
      return callForKind<ScreeningCheck>(
        target,
        `/v1/screening/checks/${checkId}/refresh`,
        "screening_check",
        { method: "POST" },
      );
    },
    check(checkId) {
      return callForKind<ScreeningCheck>(
        target,
        `/v1/screening/checks/${checkId}`,
        "screening_check",
      );
    },
  };
}
