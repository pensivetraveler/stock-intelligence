import type {
  BacktestResults,
  BacktestRun,
  BacktestStrategy,
} from "@siw/contracts/types";
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

import type { BacktestApi } from "./backtest-api";
import { BacktestScreen } from "./BacktestScreen";
import type { InstrumentListing } from "./market-api";

// The canvas chart library needs browser APIs jsdom lacks; the curve is checked by points.
vi.mock("./BacktestChart", () => ({
  BacktestChart: ({ curve }: { curve: unknown[] }) => (
    <div data-testid="backtest-chart" data-points={curve.length} />
  ),
}));

const fixtures = resolve(
  import.meta.dirname,
  "../../../tests/fixtures/contracts/valid",
);
const fixture = <T,>(name: string): T =>
  JSON.parse(readFileSync(resolve(fixtures, name), "utf8")) as T;

const strategy = fixture<BacktestStrategy>("backtest-strategy.json");
const finished = fixture<BacktestRun>("backtest-run.json");
const results = fixture<BacktestResults>("backtest-results.json");

const listing = (id: string, symbol: string, currency: string) =>
  ({
    instrument: { instrument_id: id, symbol, currency },
  }) as InstrumentListing;
const instruments = [
  listing("us:aapl:xnas:2026", "AAPL", "USD"),
  listing("us:msft:xnas:2026", "MSFT", "USD"),
  listing("kr:005930:xkrx:2026", "005930", "KRW"),
];

function fakeApi(overrides: Partial<BacktestApi> = {}) {
  const queued: BacktestRun = {
    ...finished,
    status: "queued",
    progress: 0,
    summary: null,
    finished_at_utc: null,
  };
  let polls = 0;
  const api: BacktestApi = {
    strategies: vi.fn(async () => [strategy]),
    strategy: vi.fn(async () => strategy),
    saveStrategy: vi.fn(async (name, definition) => ({
      ...strategy,
      name,
      definition,
      strategy_type: definition.type,
    })),
    // The first listing is empty; once a run is queued it finishes on the next poll.
    runs: vi.fn(async () => (polls++ === 0 ? [] : [finished])),
    run: vi.fn(async () => finished),
    submit: vi.fn(async () => queued),
    results: vi.fn(async () => results),
    cancel: vi.fn(async () => ({ ...queued, status: "cancelled" as const })),
    ...overrides,
  };
  return api;
}

function renderScreen(
  api: BacktestApi,
  onShowFills = vi.fn(),
  onClose = vi.fn(),
) {
  render(
    <BacktestScreen
      api={api}
      instruments={instruments}
      activeInstrumentId="us:aapl:xnas:2026"
      real={{ manual: [], imported: [], broker: [] }}
      shownRunId={null}
      onShowFills={onShowFills}
      onClose={onClose}
    />,
  );
  return onShowFills;
}

describe("backtest screen", () => {
  it("saves a rules strategy, runs it and shows the results", async () => {
    const api = fakeApi();
    const onShowFills = renderScreen(api);
    fireEvent.click(screen.getByLabelText("MSFT"));
    fireEvent.click(screen.getByRole("button", { name: "Save and run" }));

    await waitFor(() => expect(api.submit).toHaveBeenCalled());
    const [name, definition] = vi.mocked(api.saveStrategy).mock.calls[0];
    expect(name).toBe("EMA 10 / SMA 30");
    expect(definition).toMatchObject({ type: "rules", max_positions: 5 });
    const [strategyId, version, config] = vi.mocked(api.submit).mock.calls[0];
    expect([strategyId, version]).toEqual([
      strategy.strategy_id,
      strategy.version,
    ]);
    expect(config).toMatchObject({
      instrument_ids: ["us:aapl:xnas:2026", "us:msft:xnas:2026"],
      currency: "USD",
      timeframe: "1d",
      fee_rate: "0.0005",
      slippage_bps: "5",
    });

    // The queued run is polled until it finishes, then its results load.
    expect(
      await screen.findByRole(
        "table",
        { name: "Backtest trades" },
        { timeout: 4000 },
      ),
    ).toBeInTheDocument();
    expect(api.results).toHaveBeenCalledWith(finished.run_id);
    expect(screen.getByText("Max drawdown")).toBeInTheDocument();
    expect(screen.getByTestId("backtest-chart")).toHaveAttribute(
      "data-points",
      String(results.equity_curve.length),
    );
    expect(screen.getByLabelText("Warnings")).toHaveTextContent("survivorship");
    const compare = screen.getByRole("table", {
      name: "Compared with your real fills",
    });
    expect(within(compare).getAllByRole("row")).toHaveLength(3);

    fireEvent.click(
      screen.getByRole("button", { name: "Show fills on charts" }),
    );
    expect(onShowFills).toHaveBeenCalledWith({
      runId: finished.run_id,
      fills: results.fills,
    });
  });

  it("refuses to mix currencies in one run", () => {
    renderScreen(fakeApi());
    fireEvent.click(screen.getByLabelText("005930"));
    expect(screen.getAllByText(/one currency/)[0]).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save and run" })).toBeDisabled();
  });

  it("sends Python code with its parameters and reports API errors", async () => {
    const api = fakeApi({
      saveStrategy: vi.fn(async () => {
        throw new Error("code does not compile: line 1: invalid syntax");
      }),
    });
    renderScreen(api);
    fireEvent.change(screen.getByDisplayValue("Rules"), {
      target: { value: "python" },
    });
    fireEvent.change(screen.getByLabelText("Strategy code"), {
      target: { value: "def on_bar(ctx) pass" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "does not compile",
    );
    expect(vi.mocked(api.saveStrategy).mock.calls[0][1]).toEqual({
      type: "python",
      code: "def on_bar(ctx) pass",
      params: { length: 14 },
    });
    expect(api.submit).not.toHaveBeenCalled();
  });

  it("loads a saved strategy into the form", async () => {
    renderScreen(fakeApi());
    const picker = await screen.findByDisplayValue("New strategy");
    await waitFor(() =>
      expect(within(picker).getAllByRole("option")).toHaveLength(2),
    );
    fireEvent.change(picker, { target: { value: strategy.strategy_id } });
    expect(screen.getByLabelText("Name")).toHaveValue(strategy.name);
    expect(screen.getAllByLabelText("Condition 1 comparison")[0]).toHaveValue(
      "cross_above",
    );
  });
  it("closes on Escape wherever the focus is", () => {
    const onClose = vi.fn();
    renderScreen(fakeApi(), vi.fn(), onClose);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
