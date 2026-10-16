import type {
  MarketBarsResponse,
  MarketInstrumentsResponse,
  SessionWindow,
} from "@siw/contracts/types";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";

import type { BarsRequest, MarketApi } from "./market-api";
import { Workspace } from "./Workspace";
import { DEFAULT_WORKSPACE, WORKSPACE_STORAGE_KEY } from "./workspace-state";

// The real chart draws on canvas, which jsdom does not implement.
vi.mock("./PriceChart", () => ({
  PriceChart: ({
    chartId,
    candles,
  }: {
    chartId: string;
    candles: unknown[];
  }) => <div data-testid={`chart-${chartId}`}>{candles.length} candles</div>,
}));

const fixtures = resolve(
  import.meta.dirname,
  "../../../tests/fixtures/contracts/valid",
);
const fixture = <T,>(name: string): T =>
  JSON.parse(readFileSync(resolve(fixtures, name), "utf8")) as T;

function fakeApi(): MarketApi & { barsCalls: BarsRequest[] } {
  const session = fixture<SessionWindow>("session-window-early-close.json");
  const barsCalls: BarsRequest[] = [];
  return {
    barsCalls,
    instruments: () =>
      Promise.resolve(
        fixture<MarketInstrumentsResponse>("market-instruments-response.json")
          .instruments,
      ),
    session: (sessionDate) =>
      Promise.resolve(
        sessionDate === "2026-11-26"
          ? fixture<SessionWindow>("session-window-holiday.json")
          : session,
      ),
    bars: (request) => {
      barsCalls.push(request);
      return Promise.resolve(
        fixture<MarketBarsResponse>("market-bars-response-aggregated.json"),
      );
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

describe("chart workspace", () => {
  it("renders two charts with their own settings, coverage and lineage", async () => {
    const api = fakeApi();
    render(<Workspace api={api} storage={memoryStorage()} />);

    expect(await screen.findByTestId("chart-chart-1")).toHaveTextContent(
      "1 candles",
    );
    expect(await screen.findByTestId("chart-chart-2")).toBeInTheDocument();
    expect(api.barsCalls.map((call) => call.timeframe)).toEqual(["5m", "1m"]);
    const first = screen.getByRole("region", { name: /Chart 1/ });
    expect(within(first).getByText("1 partial, not final")).toBeInTheDocument();
    expect(within(first).getByText(/fake-market-data/)).toBeInTheDocument();
    expect(within(first).getByText(/early close/)).toBeInTheDocument();
  });

  it("changes one chart without touching the other and saves the layout", async () => {
    const api = fakeApi();
    const storage = memoryStorage();
    render(<Workspace api={api} storage={storage} />);
    await screen.findByTestId("chart-chart-2");

    const first = screen.getByRole("region", { name: /Chart 1/ });
    fireEvent.change(within(first).getByLabelText("Timeframe"), {
      target: { value: "60m" },
    });
    await screen.findByRole("region", { name: /Chart 1: .* 60m/ });

    const saved = JSON.parse(storage.getItem(WORKSPACE_STORAGE_KEY) ?? "{}");
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

    fireEvent.click(screen.getByLabelText("Sync visible time range"));

    expect(
      JSON.parse(storage.getItem(WORKSPACE_STORAGE_KEY) ?? "{}").syncTimeRange,
    ).toBe(false);
  });
});
