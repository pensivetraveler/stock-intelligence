import type {
  PaperApproval,
  PaperIntentListResponse,
  PaperRunnerStatus,
} from "@siw/contracts/types";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AgentApp } from "./AgentApp";
import { createPaperApi, type PaperApi } from "./paper-api";
import { createRunnerControl, type RunnerControl } from "./runner-control";

function status(changes: Partial<PaperRunnerStatus> = {}): PaperRunnerStatus {
  return {
    kind: "paper_runner_status",
    schema_version: "0.1",
    halted: false,
    halted_reason: null,
    armed: false,
    approval_id: null,
    unknown_submission_count: 0,
    last_heartbeat_utc: "2026-10-23T14:31:10Z",
    updated_at_utc: "2026-10-23T14:31:10Z",
    ...changes,
  };
}

const APPROVAL: PaperApproval = {
  kind: "paper_approval",
  schema_version: "0.1",
  approval_id: "b".repeat(64),
  approved_at_utc: "2026-10-23T13:00:00Z",
  strategy: {
    strategy_id: "strat-1",
    strategy_version: 2,
    definition_sha256: "c".repeat(64),
  },
  instruments: [
    {
      instrument_id: "us:aapl:xnas:2026",
      symbol: "AAPL",
      time_zone: "America/New_York",
    },
  ],
  limits: {
    max_order_amount_krw: "1000000",
    max_daily_orders: null,
    max_daily_loss_rate: null,
    max_position_amount_krw: null,
    allowed_instrument_ids: ["us:aapl:xnas:2026"],
  },
  broker_id: "kiwoom",
  broker_account_ref: "kiwoom-0123456789abcdef-demo",
  market_buffer_bps: "50",
  fee_rate: "0",
  fx: null,
  fx_source: "fixed",
  equity_basis: null,
  max_signal_age_seconds: 300,
  bar_settle_seconds: null,
};

function fakeApi(changes: Partial<PaperApi> = {}): PaperApi {
  const intents: PaperIntentListResponse = {
    kind: "paper_intent_list_response",
    schema_version: "0.1",
    intents: [],
  };
  return {
    status: vi.fn(async () => status()),
    approvals: vi.fn(async () => ({
      kind: "paper_approval_list_response" as const,
      schema_version: "0.1" as const,
      approvals: [APPROVAL],
    })),
    intents: vi.fn(async () => intents),
    executions: vi.fn(async () => ({
      kind: "paper_execution_list_response" as const,
      schema_version: "0.1" as const,
      executions: [],
    })),
    strategies: vi.fn(async () => ({
      kind: "backtest_strategy_list_response" as const,
      schema_version: "0.1" as const,
      strategies: [],
    })),
    credentials: vi.fn(async () => ({
      kind: "credential_list_response" as const,
      schema_version: "0.1" as const,
      storage: "keychain",
      active_provider: "fake",
      credentials: [],
    })) as unknown as PaperApi["credentials"],
    searchSymbols: vi.fn(async () => ({
      kind: "symbol_search_response" as const,
      schema_version: "0.1" as const,
      query: "app",
      results: [
        {
          listing: {
            instrument: {
              instrument_id: "us:aapl:xnas:2026",
              symbol: "AAPL",
            },
            source_id: "alpaca-sip",
            dataset_revision: "assets",
            session_dates: [],
          },
          name: "Apple Inc.",
          local_name: "애플",
        },
      ],
    })) as unknown as PaperApi["searchSymbols"],
    approve: vi.fn(async () => APPROVAL),
    arm: vi.fn(async () =>
      status({ armed: true, approval_id: APPROVAL.approval_id }),
    ),
    disarm: vi.fn(async () => status()),
    kill: vi.fn(async () => status({ halted: true, halted_reason: "stop" })),
    release: vi.fn(async () => status()),
    ...changes,
  };
}

const noControl: RunnerControl = createRunnerControl();

describe("AgentApp", () => {
  it("always shows the paper-only banner and the kill switch", async () => {
    const api = fakeApi();
    render(
      <AgentApp
        paperApi={api}
        runnerControl={noControl}
        languageStorage={null}
        defaultLanguage="en"
      />,
    );
    expect(await screen.findByRole("note")).toHaveTextContent(/paper trading/i);
    fireEvent.click(screen.getByRole("button", { name: /kill switch/i }));
    await waitFor(() => expect(api.kill).toHaveBeenCalledTimes(1));
  });

  it("arms an approved plan", async () => {
    const api = fakeApi();
    render(
      <AgentApp
        paperApi={api}
        runnerControl={noControl}
        languageStorage={null}
        defaultLanguage="en"
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Arm" }));
    await waitFor(() =>
      expect(api.arm).toHaveBeenCalledWith(APPROVAL.approval_id),
    );
  });

  it("offers release, not arming, while halted", async () => {
    const api = fakeApi({
      status: vi.fn(async () =>
        status({ halted: true, halted_reason: "stop" }),
      ),
    });
    render(
      <AgentApp
        paperApi={api}
        runnerControl={noControl}
        languageStorage={null}
        defaultLanguage="en"
      />,
    );
    expect(await screen.findByTestId("runner-state")).toHaveTextContent(
      "Halted: stop",
    );
    expect(screen.getByRole("button", { name: "Arm" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /release/i }));
    await waitFor(() => expect(api.release).toHaveBeenCalled());
  });

  it("stops the LaunchAgent when the API cannot record the kill switch", async () => {
    const stop = vi.fn(async () => ({
      installed: true,
      running: false,
      detail: "",
    }));
    const control: RunnerControl = {
      available: true,
      state: vi.fn(async () => ({
        installed: true,
        running: true,
        detail: "",
      })),
      install: vi.fn(),
      stop,
      uninstall: vi.fn(),
    };
    const api = fakeApi({
      kill: vi.fn(async () => {
        throw new Error("API down");
      }),
    });
    render(
      <AgentApp
        paperApi={api}
        runnerControl={control}
        languageStorage={null}
        defaultLanguage="en"
      />,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: /kill switch/i }),
    );
    await waitFor(() => expect(stop).toHaveBeenCalledTimes(1));
  });

  it("keeps a refused action's error after the next refresh", async () => {
    const api = fakeApi({
      status: vi.fn(async () =>
        status({ halted: true, halted_reason: "stop" }),
      ),
      release: vi.fn(async () => {
        throw new Error("Only the SIW Agent app can start paper trading");
      }),
    });
    render(
      <AgentApp
        paperApi={api}
        runnerControl={noControl}
        languageStorage={null}
        defaultLanguage="en"
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: /release/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Only the SIW Agent app can start paper trading",
    );
    await waitFor(() => expect(api.status).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("opens in Korean and summarises the runner", async () => {
    render(
      <AgentApp
        paperApi={fakeApi({
          status: vi.fn(async () =>
            status({ armed: true, approval_id: APPROVAL.approval_id }),
          ),
        })}
        runnerControl={noControl}
        languageStorage={null}
        now={() => new Date("2026-10-23T14:34:10Z")}
      />,
    );
    expect(await screen.findByText("꺼짐")).toBeInTheDocument();
    expect(screen.getByTestId("runner-state")).toHaveTextContent("무장됨");
    expect(screen.getByText("마지막 응답: 3분 전")).toBeInTheDocument();
    expect(screen.getByText("무장: strat-1@2")).toBeInTheDocument();
    // Armed and silent for over two minutes: a warning.
    expect(screen.getByText(/3분째 응답이 없습니다/)).toBeInTheDocument();
    expect(document.title).toBe("SIW Agent");
  });

  it("approves a plan step by step with a searched instrument", async () => {
    const api = fakeApi({
      strategies: vi.fn(async () => ({
        kind: "backtest_strategy_list_response" as const,
        schema_version: "0.1" as const,
        strategies: [{ strategy_id: "strat-1", name: "Flow", version: 2 }],
      })) as unknown as PaperApi["strategies"],
      credentials: vi.fn(async () => ({
        kind: "credential_list_response" as const,
        schema_version: "0.1" as const,
        storage: "keychain",
        active_provider: "fake",
        credentials: [
          {
            credential_id: "key-1",
            provider: "kiwoom",
            environment: "demo",
            enabled: true,
            label: "Kiwoom demo",
          },
        ],
      })) as unknown as PaperApi["credentials"],
    });
    render(
      <AgentApp
        paperApi={api}
        runnerControl={noControl}
        languageStorage={null}
        defaultLanguage="en"
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "New plan" }));
    // Step 1 refuses to go on without a strategy and a key.
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByRole("alert")).toHaveTextContent(/strategy/);
    await screen.findByRole("option", { name: "Flow (v2)" });
    fireEvent.change(screen.getByLabelText(/Strategy/), {
      target: { value: "strat-1" },
    });
    fireEvent.change(screen.getByLabelText(/Kiwoom demo key/), {
      target: { value: "key-1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    // Step 2: search and pick.
    fireEvent.change(screen.getByLabelText("Search by ticker or name"), {
      target: { value: "app" },
    });
    fireEvent.click(await screen.findByRole("button", { name: /AAPL/ }));
    expect(
      screen.getByRole("button", { name: "Remove AAPL" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    // Step 4 reviews, then approves.
    expect(screen.getByText("1,000,000 KRW")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() =>
      expect(api.approve).toHaveBeenCalledWith(
        expect.objectContaining({
          strategy_id: "strat-1",
          strategy_version: 2,
          credential_id: "key-1",
          instruments: [{ instrument_id: "us:aapl:xnas:2026", symbol: "AAPL" }],
          fx_mode: "bok_ecos",
        }),
      ),
    );
  });

  it("says runner control needs the Agent app outside Tauri", async () => {
    render(
      <AgentApp
        paperApi={fakeApi()}
        runnerControl={noControl}
        languageStorage={null}
        defaultLanguage="en"
      />,
    );
    expect(
      await screen.findByText("Runner control is only in the SIW Agent app."),
    ).toBeInTheDocument();
  });
});

describe("paper API client", () => {
  it("sends arm and kill as POSTs and never an order", async () => {
    const calls: Array<[string, RequestInit | undefined]> = [];
    const fetcher = vi.fn(
      async (url: RequestInfo | URL, init?: RequestInit) => {
        calls.push([String(url), init]);
        return new Response(JSON.stringify(status()), { status: 200 });
      },
    ) as unknown as typeof fetch;
    const api = createPaperApi("http://api", fetcher);
    await api.arm("a".repeat(64));
    await api.kill("stop");
    expect(calls.map(([url, init]) => [url, init?.method])).toEqual([
      ["http://api/v1/paper/arm", "POST"],
      ["http://api/v1/paper/kill", "POST"],
    ]);
    expect(Object.keys(api)).not.toContain("order");
  });
});
