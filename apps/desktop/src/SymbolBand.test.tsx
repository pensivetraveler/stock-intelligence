import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { MarketApi, SessionQuote } from "./market-api";
import { SymbolBandNumbers } from "./SymbolBand";

const AAPL = "us:aapl:xnas:2026";
const quote: SessionQuote = {
  instrumentId: AAPL,
  sessionDate: "2026-10-09",
  open: "200.00",
  last: "202.50",
  lastFinal: true,
  high: "203.10",
  low: "199.40",
  volume: "1234567",
  closes: ["200.50", "201.00", "202.50"],
};

function fakeApi() {
  return { quote: vi.fn(async () => quote) } as unknown as MarketApi & {
    quote: ReturnType<typeof vi.fn>;
  };
}

describe("symbol band numbers", () => {
  it("shows the session's last, change, open, high, low and volume", async () => {
    const api = fakeApi();
    render(
      <SymbolBandNumbers
        api={api}
        instrumentId={AAPL}
        sessionDate="2026-10-09"
        hidden={false}
        ready
      />,
    );
    expect(await screen.findByText("202.50")).toBeInTheDocument();
    expect(screen.getByText("+1.25%")).toBeInTheDocument();
    expect(screen.getByText("203.10")).toBeInTheDocument();
    expect(screen.getByText("199.40")).toBeInTheDocument();
    expect(screen.getByText((1234567).toLocaleString())).toBeInTheDocument();
    expect(api.quote).toHaveBeenCalledWith(AAPL, "2026-10-09");
  });

  it("hides the numbers during replay and asks for nothing", () => {
    const api = fakeApi();
    render(
      <SymbolBandNumbers
        api={api}
        instrumentId={AAPL}
        sessionDate="2026-10-09"
        hidden
        ready
      />,
    );
    expect(
      screen.getByText("Numbers hidden during replay"),
    ).toBeInTheDocument();
    expect(api.quote).not.toHaveBeenCalled();
  });

  it("waits for the charts, then reuses the watchlist's quote without asking again", async () => {
    const api = fakeApi();
    const { rerender } = render(
      <SymbolBandNumbers
        api={api}
        instrumentId={AAPL}
        sessionDate="2026-10-09"
        hidden={false}
        ready={false}
      />,
    );
    expect(api.quote).not.toHaveBeenCalled();
    rerender(
      <SymbolBandNumbers
        api={api}
        instrumentId={AAPL}
        sessionDate="2026-10-09"
        hidden={false}
        ready
        shared={{ status: "ready", quote }}
      />,
    );
    expect(await screen.findByText("202.50")).toBeInTheDocument();
    expect(api.quote).not.toHaveBeenCalled();
  });
});
