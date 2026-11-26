import type { MarketQuote } from "@siw/contracts/types";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { MarketApi } from "./market-api";
import { QuotePanel } from "./QuotePanel";

function quote(overrides: Partial<MarketQuote> = {}): MarketQuote {
  return {
    kind: "market_quote",
    schema_version: "0.1",
    instrument_id: "us:para:xnas:2026",
    symbol: "PARA",
    source_id: "toss-us",
    currency: "USD",
    observed_at_utc: "2026-11-23T07:37:37Z",
    last: null,
    orderbook: null,
    trades: [],
    price_limits: null,
    ...overrides,
  };
}

function panel(result: MarketQuote) {
  const api = {
    liveQuote: vi.fn().mockResolvedValue(result),
  } as unknown as MarketApi;
  render(
    <QuotePanel
      api={api}
      instrumentId="us:para:xnas:2026"
      symbol="PARA"
      replayOn={false}
    />,
  );
}

describe("QuotePanel", () => {
  it("says why there is no last price when the session has no trade yet", async () => {
    panel(quote());
    expect(
      await screen.findByText(/No trade yet in this session/),
    ).toBeTruthy();
  });

  it("does not show the no-trade note once there is a last trade", async () => {
    panel(
      quote({ last: { price: "3.03", at_utc: "2026-11-23T07:36:36.000Z" } }),
    );
    expect(await screen.findByText("3.03")).toBeTruthy();
    expect(screen.queryByText(/No trade yet in this session/)).toBeNull();
  });
});
