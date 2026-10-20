import type {
  ImportBatch,
  ImportedExecution,
  ImportedLedgerResponse,
  ImportPreviewResponse,
  ManualExecution,
  MarketBarsResponse,
  MarketInstrumentsResponse,
  ScenarioDefinition,
  SessionWindow,
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

import {
  MarketApiError,
  type BarsRequest,
  type ManualExecutionRequest,
  type MarketApi,
  type ScenarioRequest,
} from "./market-api";
import { Workspace } from "./Workspace";
import { LIBRARY_STORAGE_KEY } from "./workspace-library";
import { DEFAULT_WORKSPACE, WORKSPACE_STORAGE_KEY } from "./workspace-state";

// The real chart draws on canvas, which jsdom does not implement.
vi.mock("./PriceChart", () => ({
  PriceChart: ({
    chartId,
    candles,
    markers = [],
    onCandleClick,
  }: {
    chartId: string;
    candles: { time: number }[];
    markers?: { text: string }[];
    onCandleClick?: (time: number) => void;
  }) => (
    <div data-testid={`chart-${chartId}`}>
      {candles.length} candles ·{" "}
      {markers.map((marker) => marker.text).join(", ") || "no markers"}
      <button type="button" onClick={() => onCandleClick?.(candles[0].time)}>
        click first candle of {chartId}
      </button>
    </div>
  ),
}));

const fixtures = resolve(
  import.meta.dirname,
  "../../../tests/fixtures/contracts/valid",
);
const fixture = <T,>(name: string): T =>
  JSON.parse(readFileSync(resolve(fixtures, name), "utf8")) as T;

function fakeApi(
  options: { journalOff?: boolean; history?: boolean } = {},
): MarketApi & {
  barsCalls: BarsRequest[];
  sessionsCalls: { endDate: string; count: number }[];
  manualRequests: ManualExecutionRequest[];
  scenarioRequests: ScenarioRequest[];
  importRequests: { decisions: Record<string, string> }[];
} {
  const session = fixture<SessionWindow>("session-window-early-close.json");
  const barsCalls: BarsRequest[] = [];
  const sessionsCalls: { endDate: string; count: number }[] = [];
  const manualRequests: ManualExecutionRequest[] = [];
  const scenarioRequests: ScenarioRequest[] = [];
  const importRequests: { decisions: Record<string, string> }[] = [];
  const manual: ManualExecution[] = [];
  const imported: ImportedExecution[] = [];
  const scenarios: ScenarioDefinition[] = [];
  const off = () =>
    Promise.reject(
      new MarketApiError("Set SIW_DATA_DIR", "journal_storage_unconfigured"),
    );
  return {
    barsCalls,
    sessionsCalls,
    manualRequests,
    scenarioRequests,
    importRequests,
    instruments: () =>
      Promise.resolve(
        fixture<MarketInstrumentsResponse>("market-instruments-response.json")
          .instruments,
      ),
    searchSymbols: (query) =>
      Promise.resolve({
        kind: "symbol_search_response",
        schema_version: "0.1",
        query,
        source_id: "alpaca-sip",
        fetched_at_utc: "2026-10-20T01:00:00Z",
        stale: false,
        results: "NVIDIA".startsWith(query.toUpperCase())
          ? [
              {
                name: "NVIDIA Corporation Common Stock",
                listing: {
                  instrument: {
                    kind: "instrument_ref",
                    schema_version: "0.1",
                    instrument_id: "us:nvda:xnas:2026",
                    symbol: "NVDA",
                    venue: "XNAS",
                    asset_class: "us_equity",
                    currency: "USD",
                    identity_as_of: "2026-01-01T00:00:00Z",
                    identity_source: "alpaca-sip-assets",
                  },
                  source_id: "alpaca-sip",
                  dataset_revision: "alpaca-sip-assets-2026-10-20",
                  session_dates: [],
                },
              },
            ]
          : [],
      }),
    quote: (instrumentId, sessionDate) =>
      Promise.resolve(
        instrumentId === "us:aapl:xnas:2026"
          ? {
              instrumentId,
              sessionDate,
              open: "200.00",
              last: "202.50",
              lastFinal: true,
            }
          : null,
      ),
    // Earlier sessions only when asked for, so other tests load just the chart's day.
    sessions: (endDate, count) => {
      sessionsCalls.push({ endDate, count });
      return Promise.resolve(
        options.history
          ? Array.from({ length: count }, (_, index) => ({
              ...session,
              session_date: `2026-09-${String(index + 1).padStart(2, "0")}`,
            }))
          : [],
      );
    },
    session: (sessionDate) =>
      Promise.resolve(
        sessionDate === "2026-11-26"
          ? fixture<SessionWindow>("session-window-holiday.json")
          : session,
      ),
    bars: (request) => {
      barsCalls.push(request);
      const response = fixture<MarketBarsResponse>(
        "market-bars-response-aggregated.json",
      );
      // Serve the requested instrument, as the real API does.
      return Promise.resolve({
        ...response,
        bars: response.bars.map((bar) => ({
          ...bar,
          instrument_id: request.instrumentId,
        })),
        coverage: { ...response.coverage, instrument_id: request.instrumentId },
      });
    },
    manualLedger: () =>
      options.journalOff
        ? off()
        : Promise.resolve({
            kind: "manual_ledger_response",
            schema_version: "0.1",
            ledger: "manual_actual",
            executions: [...manual],
            positions: [],
          }),
    scenarioLedger: () =>
      options.journalOff
        ? off()
        : Promise.resolve({
            kind: "scenario_ledger_response",
            schema_version: "0.1",
            ledger: "hypothetical",
            scenarios: scenarios.map((scenario) => ({
              scenario,
              gross_pnl: "10",
              costs: "1",
              net_pnl: "9",
            })),
          }),
    importedLedger: () =>
      options.journalOff
        ? off()
        : Promise.resolve({
            ...fixture<ImportedLedgerResponse>("imported-ledger-response.json"),
            executions: [...imported],
            positions: [],
            batches: [],
          }),
    previewImport: () =>
      Promise.resolve(
        fixture<ImportPreviewResponse>("import-preview-response.json"),
      ),
    commitImport: (request) => {
      importRequests.push(request);
      imported.push(
        ...fixture<ImportedLedgerResponse>(
          "imported-ledger-response.json",
        ).executions.map((item) => ({
          ...item,
          instrument_id: "us:synth:xnas:2026",
          // Inside the single bar the fake serves, so the marker is drawn.
          executed_at_utc: "2026-10-09T13:31:00Z",
        })),
      );
      return Promise.resolve(fixture<ImportBatch>("import-batch.json"));
    },
    recordManual: (request) => {
      manualRequests.push(request);
      const record: ManualExecution = {
        kind: "manual_execution",
        schema_version: "0.1",
        execution_id: request.execution_id,
        instrument_id: request.instrument_id,
        side: request.side,
        quantity: request.quantity,
        price: request.price,
        currency: "USD",
        asserted_at_utc: "2026-10-12T00:00:00Z",
        time_precision: request.time_precision,
        executed_at_utc: "2026-10-09T13:31:00Z",
        selected_interval_start_utc: request.selected_interval_start_utc,
        selected_interval_end_utc: request.selected_interval_end_utc,
        evidence_status: "unverified",
        fees: request.fees,
        memo: request.memo,
      };
      manual.push(record);
      return Promise.resolve(record);
    },
    recordScenario: (request) => {
      scenarioRequests.push(request);
      const record: ScenarioDefinition = {
        kind: "scenario_definition",
        schema_version: "0.1",
        scenario_id: request.scenario_id,
        version: 1,
        instrument_id: request.instrument_id,
        origin: "hypothetical",
        direction: request.direction,
        entry_price: request.entry_price,
        exit_price: request.exit_price,
        quantity: request.quantity,
        currency: "USD",
        fee_assumption: request.fee_assumption,
        slippage_assumption: request.slippage_assumption,
        entry_interval_start_utc: request.entry_interval_start_utc,
        exit_interval_start_utc: "2026-10-09T13:34:00Z",
        created_at_utc: "2026-10-12T00:00:00Z",
      };
      scenarios.push(record);
      return Promise.resolve(record);
    },
  };
}

function memoryStorage(): Storage {
  const values = new Map<string, string>();
  return {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: () => null,
    removeItem: (key) => void values.delete(key),
    setItem: (key, value) => void values.set(key, value),
  };
}

/** Storage with the two-chart layout on, for tests that need both charts. */
function splitStorage(): Storage {
  const storage = memoryStorage();
  storage.setItem("siw.chartSplit", "1");
  return storage;
}

function openWorkspaceMenu() {
  fireEvent.click(screen.getByRole("button", { name: /^Workspace: / }));
}

function openPanel(name: string) {
  fireEvent.click(
    within(screen.getByRole("navigation", { name: "Panels" })).getByRole(
      "button",
      { name },
    ),
  );
}

function pickTimeframe(value: string) {
  fireEvent.click(
    within(screen.getByRole("group", { name: "Timeframe" })).getByRole(
      "button",
      { name: value },
    ),
  );
}

function savedLayout(storage: Storage) {
  const library = JSON.parse(storage.getItem(LIBRARY_STORAGE_KEY) ?? "{}");
  return library.workspaces.find(
    (item: { id: string }) => item.id === library.activeId,
  ).layout;
}

describe("chart workspace", () => {
  it("renders two charts with their own settings, coverage and lineage", async () => {
    const api = fakeApi();
    render(<Workspace api={api} storage={splitStorage()} />);

    expect(await screen.findByTestId("chart-chart-1")).toHaveTextContent(
      "1 candles",
    );
    expect(await screen.findByTestId("chart-chart-2")).toBeInTheDocument();
    expect(api.barsCalls.map((call) => call.timeframe)).toEqual(["5m", "1m"]);
    const first = screen.getByRole("region", { name: /Chart 1/ });
    expect(within(first).getByText("1 partial, not final")).toBeInTheDocument();
    expect(first.querySelector(".lineage")).toHaveTextContent(
      /fake-market-data/,
    );
    expect(within(first).getByText(/early close/)).toBeInTheDocument();
  });

  it("changes one chart without touching the other and saves the layout", async () => {
    const api = fakeApi();
    const storage = splitStorage();
    render(<Workspace api={api} storage={storage} />);
    await screen.findByTestId("chart-chart-2");

    // The toolbar acts on the active chart, which is chart 1 at first.
    pickTimeframe("1h");
    await screen.findByRole("region", { name: /Chart 1: .* 60m/ });

    const saved = savedLayout(storage);
    expect(saved.charts[0].timeframe).toBe("60m");
    expect(saved.charts[1]).toEqual(DEFAULT_WORKSPACE.charts[1]);
    expect(api.barsCalls.at(-1)?.timeframe).toBe("60m");
  });

  it("explains a closed day instead of drawing an empty chart", async () => {
    const storage = memoryStorage();
    storage.setItem(
      WORKSPACE_STORAGE_KEY,
      JSON.stringify({
        ...DEFAULT_WORKSPACE,
        charts: [
          { ...DEFAULT_WORKSPACE.charts[0], sessionDate: "2026-11-26" },
          DEFAULT_WORKSPACE.charts[1],
        ],
      }),
    );

    render(<Workspace api={fakeApi()} storage={storage} />);

    expect(
      await screen.findByText("2026-11-26 is not a trading day."),
    ).toBeInTheDocument();
  });

  it("persists the sync toggle", async () => {
    const storage = memoryStorage();
    render(<Workspace api={fakeApi()} storage={storage} />);
    await screen.findByTestId("chart-chart-1");

    openWorkspaceMenu();
    fireEvent.click(screen.getByLabelText("Sync visible time range"));

    expect(savedLayout(storage).syncTimeRange).toBe(false);
  });

  it("applies a template to both charts explicitly and can undo it", async () => {
    const api = fakeApi();
    render(<Workspace api={api} storage={splitStorage()} newId={sequence()} />);
    await screen.findByTestId("chart-chart-1");

    openWorkspaceMenu();
    fireEvent.change(screen.getByLabelText("Layout template"), {
      target: { value: "review" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Apply to both charts" }),
    );

    expect(
      await screen.findByRole("region", { name: /Chart 1: .* 5m/ }),
    ).toBeInTheDocument();
    expect(
      await screen.findByRole("region", { name: /Chart 2: .* 60m/ }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Sync visible time range")).toBeChecked();

    fireEvent.click(screen.getByRole("button", { name: "Undo" }));

    expect(
      await screen.findByRole("region", { name: /Chart 2: .* 1m/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Undo" })).toBeDisabled();
  });

  it("saves, switches, renames and deletes named workspaces", async () => {
    const storage = memoryStorage();
    render(<Workspace api={fakeApi()} storage={storage} newId={sequence()} />);
    await screen.findByTestId("chart-chart-1");

    openWorkspaceMenu();
    fireEvent.click(screen.getByRole("button", { name: "Save as…" }));
    fireEvent.change(screen.getByLabelText("New workspace name"), {
      target: { value: "default" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.getByRole("alert")).toHaveTextContent("already exists");

    fireEvent.change(screen.getByLabelText("New workspace name"), {
      target: { value: "Scalping" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    pickTimeframe("30m");
    await screen.findByRole("region", { name: /Chart 1: .* 30m/ });

    fireEvent.change(screen.getByLabelText("Workspace"), {
      target: { value: "id-1" },
    });
    expect(
      await screen.findByRole("region", { name: /Chart 1: .* 5m/ }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    fireEvent.change(screen.getByLabelText("Workspace name"), {
      target: { value: "Main" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save name" }));
    expect(screen.getByRole("option", { name: "Main" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(
      await screen.findByRole("region", { name: /Chart 1: .* 30m/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete" })).toBeDisabled();
    const library = JSON.parse(storage.getItem(LIBRARY_STORAGE_KEY) ?? "{}");
    expect(
      library.workspaces.map((item: { name: string }) => item.name),
    ).toEqual(["Scalping"]);
  });

  it("records an actual execution from a candle without treating the candle as the fill", async () => {
    const api = fakeApi();
    render(<Workspace api={api} storage={splitStorage()} newId={sequence()} />);
    fireEvent.click(
      await screen.findByRole("button", {
        name: "click first candle of chart-1",
      }),
    );

    const form = screen.getByRole("form", { name: "Trade entry" });
    expect(within(form).getByText(/Context only/)).toBeInTheDocument();
    expect(within(form).getByLabelText("Fill price (USD)")).toHaveValue("");
    fireEvent.change(within(form).getByLabelText("Quantity"), {
      target: { value: "10" },
    });
    fireEvent.change(within(form).getByLabelText("Fill price (USD)"), {
      target: { value: "250.20" },
    });
    fireEvent.change(within(form).getByLabelText("Fill time (ET)"), {
      target: { value: "09:31" },
    });
    fireEvent.click(
      within(form).getByRole("button", { name: "Record actual execution" }),
    );

    // Both charts show the same instrument and day, so both carry the marker.
    expect(await screen.findAllByText(/M buy 10/)).toHaveLength(2);
    expect(api.manualRequests[0]).toMatchObject({
      price: "250.20",
      executed_date_et: "2026-10-09",
      executed_time_et: "09:31",
      selected_interval_start_utc: "2026-10-09T13:30:00Z",
    });
    openPanel("Trade journal");
    const actual = screen.getByRole("region", { name: "Trade journal" });
    expect(within(actual).getByText("250.20")).toBeInTheDocument();
    expect(
      within(actual).getByText("No hypothetical scenarios yet."),
    ).toBeInTheDocument();
  });

  it("keeps hypothetical scenarios in their own ledger", async () => {
    const api = fakeApi();
    render(
      <Workspace api={api} storage={memoryStorage()} newId={sequence()} />,
    );
    fireEvent.click(
      await screen.findByRole("button", {
        name: "click first candle of chart-1",
      }),
    );

    const form = screen.getByRole("form", { name: "Trade entry" });
    fireEvent.click(
      within(form).getByRole("tab", { name: "Hypothetical scenario" }),
    );
    fireEvent.change(within(form).getByLabelText("Quantity"), {
      target: { value: "5" },
    });
    fireEvent.change(within(form).getByLabelText("Entry price"), {
      target: { value: "250" },
    });
    fireEvent.change(within(form).getByLabelText("Exit price"), {
      target: { value: "251" },
    });
    fireEvent.change(within(form).getByLabelText(/Exit time/), {
      target: { value: "09:34" },
    });
    fireEvent.click(
      within(form).getByRole("button", { name: "Save hypothetical scenario" }),
    );

    await waitFor(() => expect(api.scenarioRequests).toHaveLength(1));
    openPanel("Trade journal");
    expect(
      await screen.findByText(/9 USD \(gross 10, costs 1\)/),
    ).toBeInTheDocument();
    expect(api.manualRequests).toEqual([]);
    expect(api.scenarioRequests[0]).toMatchObject({
      entry_interval_start_utc: "2026-10-09T13:30:00Z",
      exit_time_et: "09:34",
    });
    expect(
      screen.getByText(
        "No actual executions recorded. Click a candle to add one.",
      ),
    ).toBeInTheDocument();
  });

  it("says plainly when the journal is off", async () => {
    render(
      <Workspace
        api={fakeApi({ journalOff: true })}
        storage={memoryStorage()}
      />,
    );

    await screen.findByTestId("chart-chart-1");
    openPanel("Trade journal");
    expect(await screen.findByText(/Trade journal is off/)).toBeInTheDocument();
  });

  it("previews a statement, requires a duplicate decision and adds a third ledger", async () => {
    const api = fakeApi();
    render(
      <Workspace api={api} storage={memoryStorage()} newId={sequence()} />,
    );
    await screen.findByTestId("chart-chart-1");

    // The statement import sits at the bottom of the journal panel.
    openPanel("Trade journal");
    fireEvent.click(screen.getByText("Import broker statement (CSV)"));
    const csv = new File(["Date/Time,Symbol\n"], "without_ids.csv", {
      type: "text/csv",
    });
    fireEvent.change(screen.getByLabelText("CSV file"), {
      target: { files: [csv] },
    });
    fireEvent.change(screen.getByLabelText(/Broker label/), {
      target: { value: "Example Broker" },
    });
    const preview = screen.getByRole("button", { name: "Preview" });
    await waitFor(() => expect(preview).toBeEnabled());
    fireEvent.click(preview);

    expect(await screen.findByText("Possible duplicate")).toBeInTheDocument();
    expect(screen.getByText(/matches row 1/)).toBeInTheDocument();
    const commit = screen.getByRole("button", { name: "Commit import" });
    expect(commit).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Decision for row 2"), {
      target: { value: "import" },
    });
    fireEvent.click(commit);

    expect(
      await screen.findByText(/Imported 3 of 3 rows from without_ids.csv/),
    ).toBeInTheDocument();
    expect(api.importRequests[0].decisions).toEqual({ "2": "import" });
    expect(
      await screen.findByText(/kept after duplicate review/),
    ).toBeInTheDocument();
    expect((await screen.findAllByText(/I buy 5/)).length).toBeGreaterThan(0);
    expect(
      screen.getByText(
        "No actual executions recorded. Click a candle to add one.",
      ),
    ).toBeInTheDocument();
  });

  it("shows the watchlist and puts a picked symbol in the active chart", async () => {
    const storage = splitStorage();
    render(<Workspace api={fakeApi()} storage={storage} />);
    const watchlist = await screen.findByRole("complementary", {
      name: "Watchlist",
    });
    await screen.findByTestId("chart-chart-2");
    expect(await within(watchlist).findByText("202.50")).toBeInTheDocument();
    expect(within(watchlist).getByText("+1.25%")).toBeInTheDocument();
    expect(await within(watchlist).findByText("no bars")).toBeInTheDocument();

    // Chart 2 becomes active by interacting with it; the pick goes there.
    const second = screen.getByRole("region", { name: /Chart 2/ });
    fireEvent.pointerDown(second);
    fireEvent.click(
      within(watchlist).getByRole("button", {
        name: "Show AAPL in the active chart",
      }),
    );
    await screen.findByRole("region", { name: /Chart 2: AAPL/ });
    expect(savedLayout(storage).charts[1].instrumentId).toBe(
      "us:aapl:xnas:2026",
    );
    expect(savedLayout(storage).charts[0].instrumentId).toBe(
      "us:synth:xnas:2026",
    );
  });

  it("searches symbols and steps the session date", async () => {
    const storage = memoryStorage();
    render(<Workspace api={fakeApi()} storage={storage} />);
    await screen.findByTestId("chart-chart-1");

    fireEvent.click(screen.getByRole("button", { name: "Instrument" }));
    const popover = screen.getByRole("dialog", { name: "Search symbol" });
    const search = within(popover).getByRole("textbox", {
      name: "Search symbol",
    });
    fireEvent.change(search, { target: { value: "aa" } });
    expect(
      within(popover).queryByRole("button", { name: /SYNTH/ }),
    ).not.toBeInTheDocument();
    fireEvent.keyDown(search, { key: "Enter" });
    await screen.findByRole("region", { name: /Chart 1: AAPL/ });

    fireEvent.click(screen.getByRole("button", { name: "Next day" }));
    expect(savedLayout(storage).charts[0].sessionDate).toBe("2026-10-10");
  });

  it("finds any US stock by ticker or name, charts it and keeps it", async () => {
    const api = fakeApi();
    const storage = memoryStorage();
    const { unmount } = render(<Workspace api={api} storage={storage} />);
    await screen.findByTestId("chart-chart-1");

    fireEvent.click(screen.getByRole("button", { name: "Instrument" }));
    const popover = screen.getByRole("dialog", { name: "Search symbol" });
    fireEvent.change(
      within(popover).getByRole("textbox", { name: "Search symbol" }),
      { target: { value: "nvid" } },
    );
    expect(within(popover).getByText("Searching all US stocks…")).toBeVisible();
    fireEvent.click(
      await within(popover).findByRole("button", {
        name: /NVDA.*NVIDIA Corporation Common Stock · XNAS/,
      }),
    );

    await screen.findByRole("region", { name: /Chart 1: NVDA/ });
    await waitFor(() =>
      expect(
        api.barsCalls.some((call) => call.instrumentId === "us:nvda:xnas:2026"),
      ).toBe(true),
    );
    // Added to the watchlist, removable there, and remembered on this device.
    expect(
      screen.getByRole("button", { name: "Remove NVDA from the watchlist" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Remove AAPL from the watchlist" }),
    ).not.toBeInTheDocument();
    unmount();

    render(<Workspace api={fakeApi()} storage={storage} />);
    await screen.findByRole("region", { name: /Chart 1: NVDA/ });
    fireEvent.click(
      screen.getByRole("button", { name: "Remove NVDA from the watchlist" }),
    );
    expect(
      screen.queryByRole("button", { name: "Show NVDA in the active chart" }),
    ).not.toBeInTheDocument();
  });

  it("shows the latest bar in the legend", async () => {
    render(<Workspace api={fakeApi()} storage={memoryStorage()} />);
    await screen.findByTestId("chart-chart-1");
    const first = screen.getByRole("region", { name: /Chart 1/ });
    const legend = first.querySelector(".chart-legend");
    expect(legend).not.toBeNull();
    expect(legend).toHaveTextContent(/SYNTH · 5m · fake-market-data/);
    expect(legend).toHaveTextContent("not final");
  });

  it("explains an instrument the current provider does not offer", async () => {
    const api = fakeApi();
    const storage = memoryStorage();
    storage.setItem(
      WORKSPACE_STORAGE_KEY,
      JSON.stringify({
        ...DEFAULT_WORKSPACE,
        charts: [
          { ...DEFAULT_WORKSPACE.charts[0], instrumentId: "us:old:xnas:2026" },
          DEFAULT_WORKSPACE.charts[1],
        ],
      }),
    );
    storage.setItem("siw.chartSplit", "1");
    render(<Workspace api={api} storage={storage} />);

    expect(
      await screen.findByText(/not offered by the current data provider/),
    ).toBeInTheDocument();
    await screen.findByTestId("chart-chart-2");
    expect(api.barsCalls.map((call) => call.instrumentId)).not.toContain(
      "us:old:xnas:2026",
    );
  });
});

function sequence(): () => string {
  let next = 0;
  return () => `id-${++next}`;
}

describe("terminal layout", () => {
  it("shows one chart by default and splits into two on request", async () => {
    const storage = memoryStorage();
    render(<Workspace api={fakeApi()} storage={storage} />);
    await screen.findByTestId("chart-chart-1");
    expect(screen.queryByTestId("chart-chart-2")).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "Two charts side by side" }),
    );
    expect(await screen.findByTestId("chart-chart-2")).toBeInTheDocument();
    expect(savedLayout(storage).layoutMode).toBe("2");

    fireEvent.click(screen.getByRole("button", { name: "Four charts" }));
    expect(await screen.findByTestId("chart-chart-4")).toBeInTheDocument();
    expect(savedLayout(storage).layoutMode).toBe("4");

    fireEvent.click(screen.getByRole("button", { name: "One chart" }));
    expect(screen.queryByTestId("chart-chart-2")).not.toBeInTheDocument();
    expect(savedLayout(storage).layoutMode).toBe("1");
  });

  it("offers the replay control and the drawing tools", async () => {
    render(<Workspace api={fakeApi()} storage={memoryStorage()} />);
    await screen.findByTestId("chart-chart-1");
    const tools = screen.getByRole("toolbar", { name: "Chart tools" });
    expect(
      within(tools).getByRole("button", { name: "Trend line" }),
    ).toBeEnabled();
    const magnet = within(tools).getByRole("button", {
      name: "Magnet: snap crosshair to OHLC",
    });
    fireEvent.click(magnet);
    expect(magnet).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /Replay/ })).toBeEnabled();
  });

  it("closes and reopens side panels from the right rail", async () => {
    render(<Workspace api={fakeApi()} storage={memoryStorage()} />);
    await screen.findByRole("complementary", { name: "Watchlist" });
    openPanel("Watchlist");
    expect(
      screen.queryByRole("complementary", { name: "Watchlist" }),
    ).not.toBeInTheDocument();
    openPanel("Watchlist");
    expect(
      screen.getByRole("complementary", { name: "Watchlist" }),
    ).toBeInTheDocument();
  });
});

describe("floating watchlist", () => {
  it("pops out, moves, docks and remembers its window", async () => {
    const storage = memoryStorage();
    render(<Workspace api={fakeApi()} storage={storage} />);
    await screen.findByRole("complementary", { name: "Watchlist" });

    fireEvent.click(
      screen.getByRole("button", {
        name: "Open the watchlist in a floating window",
      }),
    );
    const floating = screen.getByRole("dialog", { name: "Watchlist" });
    expect(
      screen.queryByRole("complementary", { name: "Watchlist" }),
    ).not.toBeInTheDocument();
    expect(await within(floating).findByText("202.50")).toBeInTheDocument();

    // Drag the title bar 40px right and 20px down.
    const title = within(floating).getByText("Watchlist");
    fireEvent.pointerDown(title, { button: 0, clientX: 200, clientY: 100 });
    fireEvent.pointerMove(title, { clientX: 240, clientY: 120 });
    fireEvent.pointerUp(title, { clientX: 240, clientY: 120 });
    const saved = JSON.parse(storage.getItem("siw.watchlistWindow") ?? "{}");
    expect(saved).toMatchObject({ open: true, geometry: { x: 160, y: 110 } });

    fireEvent.click(
      within(floating).getByRole("button", {
        name: "Dock the watchlist in the side panel",
      }),
    );
    expect(
      screen.queryByRole("dialog", { name: "Watchlist" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("complementary", { name: "Watchlist" }),
    ).toBeInTheDocument();
  });

  it("reopens floating after a reload and closes from the rail", async () => {
    const storage = memoryStorage();
    storage.setItem(
      "siw.watchlistWindow",
      JSON.stringify({
        open: true,
        geometry: { x: 10, y: 10, width: 300, height: 200 },
      }),
    );
    render(<Workspace api={fakeApi()} storage={storage} />);
    expect(
      await screen.findByRole("dialog", { name: "Watchlist" }),
    ).toBeInTheDocument();

    openPanel("Watchlist");
    expect(
      screen.queryByRole("dialog", { name: "Watchlist" }),
    ).not.toBeInTheDocument();
    expect(
      JSON.parse(storage.getItem("siw.watchlistWindow") ?? "{}").open,
    ).toBe(false);
  });
});

describe("watchlist quotes", () => {
  it("waits for the chart's bars before requesting quotes", async () => {
    const api = fakeApi();
    const serve = api.bars;
    let release = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    let barsRequested = false;
    api.bars = async (request) => {
      barsRequested = true;
      await gate;
      return serve(request);
    };
    const quote = vi.spyOn(api, "quote");
    render(<Workspace api={api} storage={memoryStorage()} />);
    const panel = await screen.findByRole("complementary", {
      name: "Watchlist",
    });
    await waitFor(() => expect(barsRequested).toBe(true));
    expect(quote).not.toHaveBeenCalled();

    release();
    expect(await within(panel).findByText("202.50")).toBeInTheDocument();
    expect(api.barsCalls).toHaveLength(1);
    expect(quote).toHaveBeenCalled();
  });

  it("requests each quote once for every watchlist view", async () => {
    const api = fakeApi();
    const quote = vi.spyOn(api, "quote");
    const storage = memoryStorage();
    storage.setItem(
      "siw.watchlistWindow",
      JSON.stringify({
        open: true,
        geometry: { x: 10, y: 10, width: 300, height: 200 },
      }),
    );
    render(<Workspace api={api} storage={storage} />);
    const floating = await screen.findByRole("dialog", { name: "Watchlist" });
    expect(await within(floating).findByText("202.50")).toBeInTheDocument();
    // Reopened floating after a reload, the watchlist is not also in the side panel.
    expect(
      screen.queryByRole("complementary", { name: "Watchlist" }),
    ).not.toBeInTheDocument();
    const instruments = (await api.instruments()).length;
    expect(quote).toHaveBeenCalledTimes(instruments);

    // Docking shows the quotes already loaded instead of requesting them again.
    fireEvent.click(
      within(floating).getByRole("button", {
        name: "Dock the watchlist in the side panel",
      }),
    );
    const panel = screen.getByRole("complementary", { name: "Watchlist" });
    expect(within(panel).getByText("202.50")).toBeInTheDocument();
    expect(quote).toHaveBeenCalledTimes(instruments);
  });
});

describe("indicators", () => {
  it("adds an indicator to the active chart only, then hides and removes it", async () => {
    const storage = splitStorage();
    render(<Workspace api={fakeApi()} storage={storage} newId={sequence()} />);
    await screen.findByTestId("chart-chart-2");

    fireEvent.click(screen.getByRole("button", { name: /Indicators/ }));
    const dialog = screen.getByRole("dialog", { name: "Indicators" });
    expect(within(dialog).getByText("Adds to SYNTH · 5m")).toBeInTheDocument();
    fireEvent.change(within(dialog).getByRole("textbox"), {
      target: { value: "relative" },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: /Relative Strength Index/ }),
    );
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Close indicators" }),
    );

    const first = screen.getByRole("region", { name: /Chart 1/ });
    const second = screen.getByRole("region", { name: /Chart 2/ });
    const legend = within(first).getByRole("list", {
      name: "Indicators on this chart",
    });
    expect(legend).toHaveTextContent("RSI 14");
    expect(
      within(second).queryByRole("list", { name: "Indicators on this chart" }),
    ).not.toBeInTheDocument();
    expect(savedLayout(storage).charts[0].indicators).toEqual([
      { key: "id-2", type: "rsi", params: { length: 14 } },
    ]);
    expect(savedLayout(storage).charts[1].indicators).toBeUndefined();

    fireEvent.click(
      within(legend).getByRole("button", { name: "RSI settings" }),
    );
    fireEvent.change(within(first).getByLabelText("Length"), {
      target: { value: "7" },
    });
    expect(legend).toHaveTextContent("RSI 7");

    fireEvent.click(within(legend).getByRole("button", { name: "Hide RSI" }));
    expect(savedLayout(storage).charts[0].indicators[0].hidden).toBe(true);
    fireEvent.click(within(legend).getByRole("button", { name: "Remove RSI" }));
    expect(
      within(first).queryByRole("list", { name: "Indicators on this chart" }),
    ).not.toBeInTheDocument();
  });

  it("filters the catalog by category", async () => {
    render(<Workspace api={fakeApi()} storage={memoryStorage()} />);
    await screen.findByTestId("chart-chart-1");
    fireEvent.click(screen.getByRole("button", { name: /Indicators/ }));
    const dialog = screen.getByRole("dialog", { name: "Indicators" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Volume" }));
    expect(
      within(dialog).getByRole("button", { name: /On Balance Volume/ }),
    ).toBeInTheDocument();
    expect(
      within(dialog).queryByRole("button", { name: /Relative Strength Index/ }),
    ).not.toBeInTheDocument();
  });
});

describe("drawing tools", () => {
  it("toggles a tool, cancels it with Escape and keeps one tool at a time", async () => {
    render(<Workspace api={fakeApi()} storage={memoryStorage()} />);
    await screen.findByTestId("chart-chart-1");
    const tools = screen.getByRole("toolbar", { name: "Chart tools" });
    const trend = within(tools).getByRole("button", { name: "Trend line" });
    const crosshair = within(tools).getByRole("button", { name: "Crosshair" });
    expect(crosshair).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(trend);
    expect(trend).toHaveAttribute("aria-pressed", "true");
    expect(crosshair).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(
      within(tools).getByRole("button", { name: "Fibonacci retracement" }),
    );
    expect(trend).toHaveAttribute("aria-pressed", "false");

    fireEvent.keyDown(window, { key: "Escape" });
    expect(crosshair).toHaveAttribute("aria-pressed", "true");
  });

  it("removes the active chart's drawings for its symbol and can undo it", async () => {
    const storage = memoryStorage();
    const trend = {
      id: "t1",
      kind: "trend",
      instrumentId: "us:synth:xnas:2026",
      points: [
        { time: 1, price: 50 },
        { time: 2, price: 51 },
      ],
    };
    const elsewhere = {
      id: "h1",
      kind: "hline",
      instrumentId: "us:aapl:xnas:2026",
      points: [{ time: 1, price: 200 }],
    };
    storage.setItem(
      WORKSPACE_STORAGE_KEY,
      JSON.stringify({
        ...DEFAULT_WORKSPACE,
        charts: [
          { ...DEFAULT_WORKSPACE.charts[0], drawings: [trend, elsewhere] },
          DEFAULT_WORKSPACE.charts[1],
        ],
      }),
    );
    render(<Workspace api={fakeApi()} storage={storage} />);
    await screen.findByTestId("chart-chart-1");
    const remove = screen.getByRole("button", { name: "Remove drawings" });
    expect(remove).toBeEnabled();

    fireEvent.click(remove);
    expect(savedLayout(storage).charts[0].drawings).toEqual([elsewhere]);
    expect(remove).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(savedLayout(storage).charts[0].drawings).toEqual([trend, elsewhere]);
  });
});

describe("replay", () => {
  it("starts at a picked bar, hides the watchlist and exits cleanly", async () => {
    const api = fakeApi();
    render(<Workspace api={api} storage={memoryStorage()} />);
    await screen.findByTestId("chart-chart-1");
    expect(await screen.findByText("202.50")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Replay/ }));
    const bar = screen.getByRole("toolbar", { name: "Replay controls" });
    expect(
      within(bar).getByText(/Click a bar on the active chart/),
    ).toBeInTheDocument();
    expect(screen.queryByText("202.50")).not.toBeInTheDocument();
    expect(screen.getByText(/Watchlist prices are hidden/)).toBeInTheDocument();

    // While choosing, a candle click picks the start; it does not open trade entry.
    fireEvent.click(
      screen.getByRole("button", { name: "click first candle of chart-1" }),
    );
    expect(screen.queryByRole("form", { name: "Trade entry" })).toBeNull();
    expect(within(bar).getByText(/ET$/)).toBeInTheDocument();
    expect(within(bar).getByRole("button", { name: "Play" })).toBeEnabled();
    // The chart asks the API for the session as of the replay time.
    await waitFor(() =>
      expect(api.barsCalls.some((call) => call.asOfUtc !== undefined)).toBe(
        true,
      ),
    );
    const asOfCall = api.barsCalls.find((call) => call.asOfUtc !== undefined);
    expect(asOfCall?.asOfUtc).toMatch(/^2026-10-09T\d{2}:\d{2}:00Z$/);
    // The only loaded bar has closed at its own end time, so it stays visible.
    expect(screen.getByTestId("chart-chart-1")).toHaveTextContent("1 candles");
    // Already at the last loaded bar: nothing further to step to.
    expect(
      within(bar).getByRole("button", { name: "Step forward" }),
    ).toBeDisabled();

    fireEvent.click(within(bar).getByRole("button", { name: "2x" }));
    expect(within(bar).getByRole("button", { name: "2x" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    fireEvent.click(within(bar).getByRole("button", { name: "Exit replay" }));
    expect(
      screen.queryByRole("toolbar", { name: "Replay controls" }),
    ).not.toBeInTheDocument();
    expect(await screen.findByText("202.50")).toBeInTheDocument();
  });

  it("leaves replay when the active chart moves to another day", async () => {
    render(<Workspace api={fakeApi()} storage={memoryStorage()} />);
    await screen.findByTestId("chart-chart-1");
    fireEvent.click(screen.getByRole("button", { name: /Replay/ }));
    fireEvent.click(
      screen.getByRole("button", { name: "click first candle of chart-1" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Next day" }));
    expect(
      screen.queryByRole("toolbar", { name: "Replay controls" }),
    ).not.toBeInTheDocument();
  });
});

describe("indicator warm-up", () => {
  it("loads earlier sessions only when the chart has indicators", async () => {
    const plain = fakeApi({ history: true });
    const { unmount } = render(
      <Workspace api={plain} storage={memoryStorage()} />,
    );
    await screen.findByTestId("chart-chart-1");
    // The chart day plus six earlier sessions of history.
    await waitFor(() => expect(plain.barsCalls.length).toBe(7));
    const plainCalls = plain.barsCalls.length;
    unmount();

    const storage = memoryStorage();
    storage.setItem(
      WORKSPACE_STORAGE_KEY,
      JSON.stringify({
        ...DEFAULT_WORKSPACE,
        charts: [
          {
            ...DEFAULT_WORKSPACE.charts[0],
            indicators: [{ key: "r", type: "rsi", params: { length: 14 } }],
          },
          DEFAULT_WORKSPACE.charts[1],
        ],
      }),
    );
    const withRsi = fakeApi({ history: true });
    render(<Workspace api={withRsi} storage={storage} />);
    await screen.findByTestId("chart-chart-1");
    // Each fake session has one bar, so warm-up looks back through ten sessions.
    await waitFor(() => expect(withRsi.barsCalls.length).toBe(plainCalls + 10));
    // Warm-up starts once, before the earliest history session.
    expect(withRsi.sessionsCalls).toEqual([
      { endDate: "2026-10-08", count: 6 },
      { endDate: "2026-08-31", count: 10 },
    ]);
  });
});

describe("history and timeframes", () => {
  it("loads earlier sessions to reach about 1,200 candles", async () => {
    const api = fakeApi({ history: true });
    render(<Workspace api={api} storage={memoryStorage()} />);
    await screen.findByTestId("chart-chart-1");
    // 5m extended has 192 bars a day: the chart day plus six earlier sessions.
    await waitFor(() =>
      expect(api.sessionsCalls).toContainEqual({
        endDate: "2026-10-08",
        count: 6,
      }),
    );
    await waitFor(() => expect(api.barsCalls.length).toBe(7));
    expect(new Set(api.barsCalls.map((call) => call.timeframe))).toEqual(
      new Set(["5m"]),
    );
    await waitFor(() =>
      expect(screen.getByTestId("chart-chart-1")).toHaveTextContent(
        "7 candles",
      ),
    );
  });

  it("shows favorite timeframes and lets the user star a preset", async () => {
    const storage = memoryStorage();
    render(<Workspace api={fakeApi()} storage={storage} />);
    await screen.findByTestId("chart-chart-1");
    const group = screen.getByRole("group", { name: "Timeframe" });
    expect(
      within(group)
        .getAllByRole("button")
        .map((item) => item.textContent),
    ).toEqual(["1m", "5m", "15m", "30m", "1h"]);

    fireEvent.click(screen.getByRole("button", { name: "More timeframes" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Add 3m to favorites" }),
    );
    expect(
      JSON.parse(storage.getItem("siw.timeframeFavorites") ?? "[]"),
    ).toEqual(["1m", "3m", "5m", "15m", "30m", "60m"]);
    expect(
      within(group).getByRole("button", { name: "3m" }),
    ).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "Remove 1h from favorites" }),
    );
    expect(
      within(group).queryByRole("button", { name: "1h" }),
    ).not.toBeInTheDocument();
  });

  it("applies a custom timeframe in hours", async () => {
    const api = fakeApi();
    const storage = memoryStorage();
    render(<Workspace api={api} storage={storage} />);
    await screen.findByTestId("chart-chart-1");

    fireEvent.click(screen.getByRole("button", { name: "More timeframes" }));
    const menu = screen.getByRole("dialog", { name: "Timeframes" });
    fireEvent.change(within(menu).getByLabelText("Custom"), {
      target: { value: "2" },
    });
    fireEvent.change(within(menu).getByLabelText("Unit"), {
      target: { value: "hours" },
    });
    fireEvent.click(within(menu).getByRole("button", { name: "Apply" }));

    await screen.findByRole("region", { name: /Chart 1: .* 120m/ });
    expect(savedLayout(storage).charts[0].timeframe).toBe("120m");
    expect(api.barsCalls.at(-1)?.timeframe).toBe("120m");
    // Not a favorite, but shown while it is the chart's timeframe.
    const group = screen.getByRole("group", { name: "Timeframe" });
    expect(within(group).getByRole("button", { name: "2h" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("rejects a custom timeframe longer than 16 hours", async () => {
    render(<Workspace api={fakeApi()} storage={memoryStorage()} />);
    await screen.findByTestId("chart-chart-1");
    fireEvent.click(screen.getByRole("button", { name: "More timeframes" }));
    const menu = screen.getByRole("dialog", { name: "Timeframes" });
    fireEvent.change(within(menu).getByLabelText("Custom"), {
      target: { value: "17" },
    });
    fireEvent.change(within(menu).getByLabelText("Unit"), {
      target: { value: "hours" },
    });
    fireEvent.click(within(menu).getByRole("button", { name: "Apply" }));
    expect(within(menu).getByRole("alert")).toHaveTextContent("16 hours");
  });
});

describe("daily and weekly charts", () => {
  it("loads a daily chart in one request and turns the hours toggle off", async () => {
    const api = fakeApi({ history: true });
    const storage = memoryStorage();
    storage.setItem(
      WORKSPACE_STORAGE_KEY,
      JSON.stringify({
        ...DEFAULT_WORKSPACE,
        charts: [
          {
            ...DEFAULT_WORKSPACE.charts[0],
            timeframe: "1d",
            indicators: [{ key: "r", type: "rsi", params: { length: 14 } }],
          },
          DEFAULT_WORKSPACE.charts[1],
        ],
      }),
    );
    render(<Workspace api={api} storage={storage} />);
    await screen.findByRole("region", { name: /Chart 1: .* 1d/ });
    await waitFor(() => expect(api.barsCalls.length).toBe(1));
    expect(api.barsCalls[0]).toMatchObject({
      timeframe: "1d",
      startUtc: "2022-01-04T00:00:00Z",
      endUtc: "2026-10-10T00:00:00Z",
    });
    // No session-by-session history and no separate warm-up.
    expect(api.sessionsCalls).toEqual([]);

    const hours = screen.getByRole("group", { name: "Hours" });
    for (const button of within(hours).getAllByRole("button")) {
      expect(button).toBeDisabled();
    }
  });

  it("offers 1D and 1W in the timeframe menu", async () => {
    const api = fakeApi();
    render(<Workspace api={api} storage={memoryStorage()} />);
    await screen.findByTestId("chart-chart-1");
    fireEvent.click(screen.getByRole("button", { name: "More timeframes" }));
    const menu = screen.getByRole("dialog", { name: "Timeframes" });
    fireEvent.click(within(menu).getByRole("button", { name: "1W" }));
    await screen.findByRole("region", { name: /Chart 1: .* 1w/ });
    expect(api.barsCalls.at(-1)?.timeframe).toBe("1w");
  });
});
