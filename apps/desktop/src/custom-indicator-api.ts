import type {
  CustomIndicator,
  CustomIndicatorListResponse,
  CustomIndicatorResult,
} from "@siw/contracts/types";

import type { OhlcvBar } from "./indicators";
import { callForKind, type ApiTarget } from "./market-api";

/** Python custom indicators (ADR 0023): saved, versioned and computed by the API. */
export interface CustomIndicatorApi {
  /** Every version of every indicator, so a chart can still draw a pinned one. */
  list(): Promise<CustomIndicator[]>;
  /** Checks the code in the sandbox, then saves it (with an ID: its next version). */
  save(code: string, indicatorId?: string): Promise<CustomIndicator>;
  compute(
    indicatorId: string,
    version: number,
    params: Record<string, number>,
    bars: OhlcvBar[],
    timeZone: string,
  ): Promise<CustomIndicatorResult>;
}

export function createCustomIndicatorApi(
  baseUrl: string,
  fetcher: typeof fetch = fetch,
  timeoutMs = 45_000,
): CustomIndicatorApi {
  const target: ApiTarget = { baseUrl, fetcher, timeoutMs };
  return {
    async list() {
      const response = await callForKind<CustomIndicatorListResponse>(
        target,
        "/v1/indicators/custom",
        "custom_indicator_list_response",
      );
      return response.indicators;
    },
    save(code, indicatorId) {
      return callForKind<CustomIndicator>(
        target,
        "/v1/indicators/custom",
        "custom_indicator",
        {
          body: { code, ...(indicatorId ? { indicator_id: indicatorId } : {}) },
        },
      );
    },
    compute(indicatorId, version, params, bars, timeZone) {
      return callForKind<CustomIndicatorResult>(
        target,
        `/v1/indicators/custom/${encodeURIComponent(indicatorId)}/compute`,
        "custom_indicator_result",
        {
          body: {
            version,
            params,
            time_zone: timeZone,
            bars: bars.map(({ time, open, high, low, close, volume }) => ({
              time,
              open,
              high,
              low,
              close,
              volume,
            })),
          },
        },
      );
    },
  };
}
