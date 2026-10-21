import type { AgentAnalysis, ManualExecution } from "@siw/contracts/types";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";

import type { AgentApi } from "./agent-api";
import { AgentPanel } from "./AgentPanel";
import { MarketApiError } from "./market-api";

const analysis = JSON.parse(
  readFileSync(
    resolve(
      import.meta.dirname,
      "../../../tests/fixtures/contracts/valid/agent-analysis.json",
    ),
    "utf8",
  ),
) as AgentAnalysis;
const fresh: AgentAnalysis = { ...analysis, commentaries: [] };
const darkPool = JSON.parse(
  readFileSync(
    resolve(
      import.meta.dirname,
      "../../../tests/fixtures/contracts/valid/agent-analysis-dark-pool.json",
    ),
    "utf8",
  ),
) as AgentAnalysis;
const AAPL = "us:aapl:xnas:2026";

function fakeApi(overrides: Partial<AgentApi> = {}): AgentApi {
  return {
    reviewChart: vi.fn(async () => fresh),
    reviewTrades: vi.fn(async (): Promise<AgentAnalysis> => ({
      ...fresh,
      target: { type: "trades", instrument_id: AAPL, execution_count: 1 },
      facts: [
        {
          code: "mfe_pct",
          subject: null,
          value: "4.20",
          unit: "percent",
          text: "While held, the high reached +4.20% from the average buy",
          hindsight: true,
        },
      ],
    })) as AgentApi["reviewTrades"],
    reviewBacktest: vi.fn(async () => fresh),
    reviewFilings: vi.fn(async () => fresh),
    reviewDarkPool: vi.fn(async () => darkPool),
    analyses: vi.fn(async () => []),
    payload: vi.fn(async () => ({ facts: ["sent"] })),
    askClaude: vi.fn(async () => analysis),
    ...overrides,
  };
}

const execution = (id: string, instrument: string) =>
  ({
    execution_id: id,
    instrument_id: instrument,
    side: "buy",
    quantity: "10",
    price: "250.00",
    executed_at_utc: "2026-10-09T14:00:00Z",
  }) as ManualExecution;

function renderPanel(api: AgentApi, request = null as never) {
  return render(
    <AgentPanel
      api={api}
      instrumentId={AAPL}
      timeframe="1d"
      cutoffUtc={() => "2026-10-21T12:00:00Z"}
      real={{
        manual: [execution("m1", AAPL), execution("m2", "us:msft:xnas:2026")],
        imported: [],
        broker: [],
      }}
      request={request}
      onRequestHandled={vi.fn()}
      symbolOf={(id) => (id === AAPL ? "AAPL" : id)}
    />,
  );
}

describe("review Agent panel", () => {
  it("reviews the chart now and shows the facts", async () => {
    const api = fakeApi();
    renderPanel(api);
    fireEvent.click(screen.getByRole("button", { name: "Review this chart" }));
    await waitFor(() =>
      expect(api.reviewChart).toHaveBeenCalledWith({
        instrumentId: AAPL,
        timeframe: "1d",
        cutoffUtc: "2026-10-21T12:00:00Z",
      }),
    );
    const facts = await screen.findByRole("table", { name: "Facts" });
    expect(within(facts).getByText("RSI 14")).toBeInTheDocument();
    expect(within(facts).getByText("Bars used")).toBeInTheDocument();
  });

  it("reviews chosen executions of this instrument only and marks hindsight", async () => {
    const api = fakeApi();
    renderPanel(api);
    const list = screen.getByRole("list", { name: "Executions to review" });
    expect(within(list).getAllByRole("checkbox")).toHaveLength(1);
    fireEvent.click(within(list).getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Review 1 selected" }));
    await waitFor(() =>
      expect(api.reviewTrades).toHaveBeenCalledWith(AAPL, [
        { ledger: "manual", execution_id: "m1" },
      ]),
    );
    expect(await screen.findByText("hindsight")).toBeInTheDocument();
  });

  it("shows what will be sent, then Claude's commentary apart from the facts", async () => {
    const api = fakeApi();
    renderPanel(api);
    fireEvent.click(screen.getByRole("button", { name: "Review this chart" }));
    fireEvent.click(await screen.findByText("What will be sent"));
    await waitFor(() =>
      expect(api.payload).toHaveBeenCalledWith(fresh.analysis_id),
    );
    expect(await screen.findByText(/"sent"/)).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Ask Claude for commentary" }),
    );
    const commentary = await screen.findByRole("region", {
      name: "Claude commentary",
    });
    expect(api.askClaude).toHaveBeenCalledWith(fresh.analysis_id, "en");
    expect(within(commentary).getByText("Inferences")).toBeInTheDocument();
    expect(
      within(commentary).getByText(/not investment advice/),
    ).toBeInTheDocument();
  });

  it("asks for a Claude key when none is registered", async () => {
    const api = fakeApi({
      askClaude: vi.fn(async () => {
        throw new MarketApiError("missing", "agent_key_missing");
      }),
    });
    renderPanel(api);
    fireEvent.click(screen.getByRole("button", { name: "Review this chart" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Ask Claude for commentary" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("Keys screen");
  });

  it("reviews FINRA weekly ATS volume at the chart's time and says what it is not", async () => {
    const api = fakeApi();
    renderPanel(api);
    expect(
      screen.getByText(/No direction, prices or intraday/),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Review weekly ATS volume" }),
    );
    await waitFor(() =>
      expect(api.reviewDarkPool).toHaveBeenCalledWith({
        instrumentId: AAPL,
        cutoffUtc: "2026-10-21T12:00:00Z",
      }),
    );
    const facts = await screen.findByRole("table", { name: "Facts" });
    expect(
      within(facts).getByText("ATS share of off-exchange volume"),
    ).toBeInTheDocument();
    expect(screen.getByText(/official SEC or FINRA data/)).toBeInTheDocument();
  });

  it("asks for filings with hindsight only when ticked", async () => {
    const api = fakeApi();
    renderPanel(api);
    fireEvent.click(screen.getByRole("button", { name: "Review filings" }));
    await waitFor(() =>
      expect(api.reviewFilings).toHaveBeenLastCalledWith({
        instrumentId: AAPL,
        cutoffUtc: "2026-10-21T12:00:00Z",
        afterDays: 0,
      }),
    );
    fireEvent.click(screen.getByLabelText(/7 days after/));
    fireEvent.click(screen.getByRole("button", { name: "Review filings" }));
    await waitFor(() =>
      expect(api.reviewFilings).toHaveBeenLastCalledWith(
        expect.objectContaining({ afterDays: 7 }),
      ),
    );
  });

  it("explains a missing FINRA credential", async () => {
    const api = fakeApi({
      reviewDarkPool: vi.fn(async () => {
        throw new MarketApiError("missing", "finra_credential_missing");
      }),
    });
    renderPanel(api);
    fireEvent.click(
      screen.getByRole("button", { name: "Review weekly ATS volume" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "FINRA API credential",
    );
  });
});
