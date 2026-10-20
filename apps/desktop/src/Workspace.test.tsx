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

function fakeApi(options: { journalOff?: boolean } = {}): MarketApi & {
  barsCalls: BarsRequest[];
  manualRequests: ManualExecutionRequest[];
  scenarioRequests: ScenarioRequest[];
  importRequests: { decisions: Record<string, string> }[];
} {
  const session = fixture<SessionWindow>("session-window-early-close.json");
  const barsCalls: BarsRequest[] = [];
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
    manualRequests,
    scenarioRequests,
    importRequests,
    instruments: () =>
      Promise.resolve(
        fixture<MarketInstrumentsResponse>("market-instruments-response.json")
          .instruments,
      ),
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
    pickTimeframe("60m");
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

    openPanel("Import broker statement (CSV)");
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
    openPanel("Trade journal");
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
    expect(storage.getItem("siw.chartSplit")).toBe("1");
  });

  it("keeps later-stage tools visible but disabled", async () => {
    render(<Workspace api={fakeApi()} storage={memoryStorage()} />);
    await screen.findByTestId("chart-chart-1");
    const tools = screen.getByRole("toolbar", { name: "Chart tools" });
    expect(
      within(tools).getByRole("button", { name: "Trend line" }),
    ).toBeDisabled();
    const magnet = within(tools).getByRole("button", {
      name: "Magnet: snap crosshair to OHLC",
    });
    fireEvent.click(magnet);
    expect(magnet).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /Indicators/ })).toBeDisabled();
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
