import type {
  BacktestResults,
  BacktestRun,
  BacktestRunListResponse,
  BacktestStrategy,
  BacktestStrategyListResponse,
} from "@siw/contracts/types";

import { callForKind, type ApiTarget } from "./market-api";

export type StrategyDefinition = Record<string, unknown> & {
  type: "rules" | "python";
};
export type RunConfig = BacktestRun["config"];
/** What the screen sends; the API fills in the fill policy and checks the rest. */
export type RunConfigRequest = Omit<
  RunConfig,
  "fill_policy" | "as_of_utc" | "instrument_ids"
> & {
  /** 1 to 20 IDs; the API enforces the limit. */
  instrument_ids: string[];
  as_of_utc?: string | null;
};

/**
 * Backtest client (ADR 0021). Runs are queued on the API and polled here; their
 * fills live in the backtest store only, never in the journal.
 */
export interface BacktestApi {
  strategies(): Promise<BacktestStrategy[]>;
  strategy(strategyId: string, version?: number): Promise<BacktestStrategy>;
  /** A new strategy, or with `strategyId` its next version. */
  saveStrategy(
    name: string,
    definition: StrategyDefinition,
    strategyId?: string,
  ): Promise<BacktestStrategy>;
  runs(): Promise<BacktestRun[]>;
  run(runId: string): Promise<BacktestRun>;
  submit(
    strategyId: string,
    strategyVersion: number,
    config: RunConfigRequest,
  ): Promise<BacktestRun>;
  results(runId: string): Promise<BacktestResults>;
  cancel(runId: string): Promise<BacktestRun>;
}

export function createBacktestApi(
  baseUrl: string,
  fetcher: typeof fetch = fetch,
  timeoutMs = 20_000,
): BacktestApi {
  const target: ApiTarget = { baseUrl, fetcher, timeoutMs };
  const runPath = (runId: string) =>
    `/v1/backtests/runs/${encodeURIComponent(runId)}`;
  return {
    async strategies() {
      const response = await callForKind<BacktestStrategyListResponse>(
        target,
        "/v1/backtests/strategies",
        "backtest_strategy_list_response",
      );
      return response.strategies;
    },
    strategy(strategyId, version) {
      const query = version ? `?version=${version}` : "";
      return callForKind<BacktestStrategy>(
        target,
        `/v1/backtests/strategies/${encodeURIComponent(strategyId)}${query}`,
        "backtest_strategy",
      );
    },
    saveStrategy(name, definition, strategyId) {
      return callForKind<BacktestStrategy>(
        target,
        "/v1/backtests/strategies",
        "backtest_strategy",
        {
          body: {
            name,
            definition,
            ...(strategyId ? { strategy_id: strategyId } : {}),
          },
        },
      );
    },
    async runs() {
      const response = await callForKind<BacktestRunListResponse>(
        target,
        "/v1/backtests/runs",
        "backtest_run_list_response",
      );
      return response.runs;
    },
    run(runId) {
      return callForKind<BacktestRun>(target, runPath(runId), "backtest_run");
    },
    submit(strategyId, strategyVersion, config) {
      return callForKind<BacktestRun>(
        target,
        "/v1/backtests/runs",
        "backtest_run",
        {
          body: {
            strategy_id: strategyId,
            strategy_version: strategyVersion,
            config,
          },
        },
      );
    },
    results(runId) {
      return callForKind<BacktestResults>(
        target,
        `${runPath(runId)}/results`,
        "backtest_results",
      );
    },
    cancel(runId) {
      return callForKind<BacktestRun>(
        target,
        `${runPath(runId)}/cancel`,
        "backtest_run",
        { method: "POST" },
      );
    },
  };
}
