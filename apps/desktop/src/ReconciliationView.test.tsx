import type { TradeReconciliationResponse } from "@siw/contracts/types";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { LANGUAGE_STORAGE_KEY } from "./i18n";
import { I18nProvider } from "./I18nProvider";
import { MarketApiError, type MarketApi } from "./market-api";
import { ReconciliationView } from "./ReconciliationView";

const result = JSON.parse(
  readFileSync(
    resolve(
      import.meta.dirname,
      "../../../tests/fixtures/contracts/valid/trade-reconciliation-response.json",
    ),
    "utf8",
  ),
) as TradeReconciliationResponse;

function apiWith(reconciliation: MarketApi["reconciliation"]): MarketApi {
  return { reconciliation } as MarketApi;
}

function koreanStorage(): Storage {
  const items = new Map([[LANGUAGE_STORAGE_KEY, "ko"]]);
  return {
    length: 1,
    clear: () => items.clear(),
    getItem: (key) => items.get(key) ?? null,
    key: () => null,
    removeItem: (key) => void items.delete(key),
    setItem: (key, value) => void items.set(key, value),
  };
}

describe("ReconciliationView", () => {
  it("summarizes every status and lists what needs attention", async () => {
    const reconciliation = vi.fn(() => Promise.resolve(result));
    render(
      <ReconciliationView
        api={apiWith(reconciliation)}
        revision={1}
        symbolOf={() => "AAPL"}
      />,
    );

    const view = await screen.findByRole("region", {
      name: /Reconciliation/,
    });
    expect(await within(view).findByText("2 matched")).toBeInTheDocument();
    expect(within(view).getByText("2 conflicting")).toBeInTheDocument();
    expect(within(view).getByText("1 broker only")).toBeInTheDocument();
    expect(within(view).getByText("1 manual only")).toBeInTheDocument();
    expect(within(view).getByText(/tolerance 10 bps/)).toBeInTheDocument();

    // Matched rows are hidden until "All records" is chosen.
    const rows = () => within(view).getAllByRole("row").slice(1);
    expect(rows()).toHaveLength(4);
    expect(
      within(view).getAllByText(/price outside tolerance/).length,
    ).toBeGreaterThan(0);
    expect(within(view).getAllByText(/qty 0, price 20 bps/)).toHaveLength(2);
    fireEvent.change(within(view).getByLabelText(/Show/), {
      target: { value: "all" },
    });
    expect(rows()).toHaveLength(6);
    expect(
      within(view).getAllByText(/several fills of one order/).length,
    ).toBeGreaterThan(0);
  });

  it("recomputes when the ledgers change or on request", async () => {
    const reconciliation = vi.fn(() => Promise.resolve(result));
    const api = apiWith(reconciliation);
    const { rerender } = render(
      <ReconciliationView api={api} revision={1} symbolOf={(id) => id} />,
    );
    await screen.findByText("2 matched");
    rerender(
      <ReconciliationView api={api} revision={2} symbolOf={(id) => id} />,
    );
    await waitFor(() => expect(reconciliation).toHaveBeenCalledTimes(2));
    fireEvent.click(await screen.findByRole("button", { name: "Recompute" }));
    await waitFor(() => expect(reconciliation).toHaveBeenCalledTimes(3));
  });

  it("reports a failure without hiding the journal", async () => {
    render(
      <ReconciliationView
        api={apiWith(() => Promise.reject(new MarketApiError("boom", "x")))}
        revision={1}
        symbolOf={(id) => id}
      />,
    );
    expect(
      await screen.findByText("Reconciliation unavailable: boom"),
    ).toBeInTheDocument();
  });

  it("speaks Korean", async () => {
    render(
      <I18nProvider storage={koreanStorage()}>
        <ReconciliationView
          api={apiWith(() => Promise.resolve(result))}
          revision={1}
          symbolOf={(id) => id}
        />
      </I18nProvider>,
    );
    expect(await screen.findByText("일치 2")).toBeInTheDocument();
    expect(screen.getByText("불일치 2")).toBeInTheDocument();
    expect(screen.getAllByText(/가격이 허용 오차 밖/).length).toBeGreaterThan(
      0,
    );
  });

  it("explains a broker-only ledger and shows counts instead of rows", async () => {
    const brokerOnly = result.records.find(
      (record) => record.status === "broker_only",
    )!;
    const idle: TradeReconciliationResponse = {
      ...result,
      summary: {
        matched: 0,
        conflicting: 0,
        broker_only: 2,
        manual_only: 0,
        imported_only: 0,
      },
      ledger_counts: {
        broker_api: 2,
        manual_actual: 0,
        statement_imported: 0,
      },
      breakdown: {
        by_instrument: [
          {
            instrument_id: "us:aapl:xnas:2026",
            counts: {
              matched: 0,
              conflicting: 0,
              broker_only: 2,
              manual_only: 0,
              imported_only: 0,
            },
          },
        ],
        by_day: [
          {
            date: "2026-10-09",
            counts: {
              matched: 0,
              conflicting: 0,
              broker_only: 2,
              manual_only: 0,
              imported_only: 0,
            },
          },
        ],
      },
      records: [brokerOnly, { ...brokerOnly, execution_id: "brk-2" }],
      record_count: 2,
    };
    const reconciliation = vi.fn<MarketApi["reconciliation"]>(() =>
      Promise.resolve(idle),
    );
    render(
      <ReconciliationView
        api={apiWith(reconciliation)}
        revision={1}
        symbolOf={() => "AAPL"}
      />,
    );
    const view = await screen.findByRole("region", { name: /Reconciliation/ });
    expect(
      await within(view).findByText(/no manual entries or statement imports/),
    ).toBeInTheDocument();
    // Per instrument by default: one row of counts, not the raw records.
    const table = within(view).getByRole("table");
    const row = within(table).getAllByRole("row")[1];
    expect(row).toHaveTextContent("AAPL");
    expect(within(view).getByLabelText(/^View/)).toHaveValue("instrument");

    // Opening the instrument's list filters the API by it; broker-only rows stay
    // hidden under "Needs attention" and the note says how to see them.
    fireEvent.click(within(row).getByRole("button", { name: "List" }));
    await waitFor(() =>
      expect(reconciliation).toHaveBeenLastCalledWith(
        expect.objectContaining({ instrumentId: "us:aapl:xnas:2026" }),
      ),
    );
    expect(await within(view).findByText(/to list them/)).toBeInTheDocument();
    fireEvent.change(within(view).getByLabelText(/^Show/), {
      target: { value: "broker_only" },
    });
    await waitFor(() =>
      expect(reconciliation).toHaveBeenLastCalledWith(
        expect.objectContaining({ status: "broker_only" }),
      ),
    );
    expect(await within(view).findAllByRole("row")).toHaveLength(3);

    // Per day, a day opens that day's records.
    fireEvent.change(within(view).getByLabelText(/^View/), {
      target: { value: "day" },
    });
    fireEvent.click(
      within(within(view).getByRole("row", { name: /2026-10-09/ })).getByRole(
        "button",
        { name: "List" },
      ),
    );
    await waitFor(() =>
      expect(reconciliation).toHaveBeenLastCalledWith(
        expect.objectContaining({
          fromDate: "2026-10-09",
          toDate: "2026-10-09",
        }),
      ),
    );
  });
});
