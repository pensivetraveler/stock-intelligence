import { describe, expect, it, vi } from "vitest";

import { createMarketApi, MarketApiError } from "./market-api";

function json(status: number, payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("market API client", () => {
  it("builds the bars query and returns the response", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(json(200, { kind: "market_bars_response", bars: [] }));
    const api = createMarketApi("http://api", fetcher);

    await api.bars({
      instrumentId: "us:synth:xnas:2026",
      timeframe: "15m",
      startUtc: "2026-10-09T13:30:00Z",
      endUtc: "2026-10-09T20:00:00Z",
    });

    const url = new URL(String(fetcher.mock.calls[0][0]));
    expect(url.pathname).toBe("/v1/market/bars");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      instrument_id: "us:synth:xnas:2026",
      timeframe: "15m",
      start_utc: "2026-10-09T13:30:00Z",
      end_utc: "2026-10-09T20:00:00Z",
    });
  });

  it("surfaces the API error envelope with its request ID", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      json(422, {
        error: {
          code: "invalid_market_data_query",
          message: "must align",
          request_id: "r-1",
        },
      }),
    );

    const failure = createMarketApi("http://api", fetcher).session(
      "2026-10-09",
    );

    await expect(failure).rejects.toMatchObject({
      code: "invalid_market_data_query",
      message: "must align",
      requestId: "r-1",
    });
  });

  it("rejects a response of the wrong kind", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(json(200, { status: "ok" }));

    await expect(
      createMarketApi("http://api", fetcher).instruments(),
    ).rejects.toBeInstanceOf(MarketApiError);
  });
});
