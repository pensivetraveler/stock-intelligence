import type {
  MarketBarsResponse,
  ReviewAccountCapitalList,
  ReviewReportDraft,
  ReviewSettings,
  ReviewTradeListResponse,
  ReviewTradeReport,
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

import type { MarketApi } from "./market-api";
import type { ReviewApi } from "./review-api";
import { ReviewSettingsPanel } from "./ReviewSettingsPanel";
import { TradeReportPanel } from "./TradeReportPanel";
import { TradeReviewScreen } from "./TradeReviewScreen";

// The chart library needs a real canvas; the chart's own markers are tested elsewhere.
vi.mock("./TradeChart", () => ({
  TradeChart: ({ markers }: { markers: unknown[] }) => (
    <div data-testid="trade-chart">{markers.length} markers</div>
  ),
}));

function fixture<T>(name: string): T {
  return JSON.parse(
    readFileSync(
      resolve(
        import.meta.dirname,
        `../../../tests/fixtures/contracts/valid/${name}`,
      ),
      "utf8",
    ),
  ) as T;
}

const list = fixture<ReviewTradeListResponse>("review-trade-list.json");
const bars = fixture<MarketBarsResponse>("market-bars-response.json");
const settings = fixture<ReviewSettings>("review-settings.json");
const draft = fixture<ReviewReportDraft>("review-report-draft.json");
const report = fixture<ReviewTradeReport>("review-trade-report.json");
const capital: ReviewAccountCapitalList = {
  kind: "review_account_capital_list",
  schema_version: "0.1",
  accounts: [{ account_ref: "kiwoom-a", currency: "USD" }],
  entries: [],
};

function fakeApi(overrides: Partial<ReviewApi> = {}): ReviewApi {
  return {
    trades: vi.fn(async () => list),
    assign: vi.fn(async () => ({
      kind: "review_assignment_result" as const,
      schema_version: "0.1" as const,
      strategy_id: "strategy-x",
      updated: 2,
    })),
    setRule: vi.fn(async () => ({
      kind: "review_strategy_rule" as const,
      schema_version: "0.1" as const,
      strategy_id: "strategy-x",
      rule: "fifo" as const,
    })),
    makeGroup: vi.fn(async () => ({
      kind: "review_group" as const,
      schema_version: "0.1" as const,
      group_id: "g1",
      execution_count: 2,
    })),
    removeGroup: vi.fn(async () => undefined),
    annotate: vi.fn(async (tradeId, annotation) => ({
      kind: "review_annotation" as const,
      schema_version: "0.1" as const,
      trade_id: tradeId,
      ...annotation,
      recorded_at_utc: "2026-10-26T12:00:00Z",
      revision: 1,
    })),
    settings: vi.fn(async () => settings),
    setReportModel: vi.fn(async (model) => ({
      ...settings,
      report_model: model,
    })),
    capital: vi.fn(async () => capital),
    addCapital: vi.fn(async () => capital),
    removeCapital: vi.fn(async () => undefined),
    draftReport: vi.fn(async () => draft),
    sendReport: vi.fn(async () => report),
    reports: vi.fn(async () => []),
    ...overrides,
  };
}

const marketApi = {
  bars: vi.fn(async () => bars),
} as unknown as MarketApi;

function show(api: ReviewApi) {
  return render(
    <TradeReviewScreen
      api={api}
      marketApi={marketApi}
      instruments={[]}
      activeInstrumentId="us:aapl:xnas:2026"
      onClose={() => undefined}
      today="2026-10-26"
    />,
  );
}

describe("TradeReviewScreen", () => {
  it("lists trades and shows the chosen one", async () => {
    const api = fakeApi();
    show(api);
    expect(await screen.findByText("2 of 2 trades")).toBeInTheDocument();
    expect(api.trades).toHaveBeenCalledWith({
      ledger: "broker",
      limit: 300,
      offset: 0,
    });
    const rows = screen.getAllByRole("row");
    expect(within(rows[1]!).getByText("446")).toBeInTheDocument();
    expect(within(rows[1]!).getByText("Closed ✓")).toBeInTheDocument();

    fireEvent.click(within(rows[1]!).getByText("AAPL"));
    const detail = await screen.findByLabelText("Trade AAPL");
    expect(within(detail).getByText("446 USD")).toBeInTheDocument();
    expect(within(detail).getByText("20 @ 105")).toBeInTheDocument();
    // The 15-share exit is one row however many lots it closes.
    expect(within(detail).getByText("15")).toBeInTheDocument();
    expect(within(detail).getByDisplayValue("분할 매도")).toBeInTheDocument();
    await waitFor(() => expect(marketApi.bars).toHaveBeenCalled());
  });

  it("assigns a strategy to every fill of the trade", async () => {
    const api = fakeApi();
    show(api);
    fireEvent.click((await screen.findAllByText("AAPL"))[1]!);
    const detail = await screen.findByLabelText("Trade AAPL");
    fireEvent.change(within(detail).getByLabelText("Strategy"), {
      target: { value: "strategy-x" },
    });
    await waitFor(() =>
      expect(api.assign).toHaveBeenCalledWith(
        [
          { ledger: "broker", execution_id: "brk-5" },
          { ledger: "broker", execution_id: "brk-6" },
        ],
        "strategy-x",
      ),
    );
    await waitFor(() => expect(api.trades).toHaveBeenCalledTimes(2));
  });

  it("saves notes, tags and the reviewed mark", async () => {
    const api = fakeApi();
    show(api);
    fireEvent.click((await screen.findAllByText("AAPL"))[1]!);
    const detail = await screen.findByLabelText("Trade AAPL");
    fireEvent.change(within(detail).getByLabelText("Note"), {
      target: { value: "추격 매수" },
    });
    fireEvent.change(within(detail).getByLabelText("Tags (comma separated)"), {
      target: { value: "돌파, 충동, 돌파" },
    });
    fireEvent.click(within(detail).getByLabelText("Reviewed"));
    fireEvent.click(within(detail).getByText("Save note"));
    await waitFor(() =>
      expect(api.annotate).toHaveBeenCalledWith("flat:broker:brk-5", {
        note: "추격 매수",
        tags: ["돌파", "충동"],
        reviewed: true,
      }),
    );
    expect(
      await within(detail).findByText(/Written after the trade/),
    ).toBeInTheDocument();
  });

  it("merges only trades of a manual-rule strategy", async () => {
    const api = fakeApi();
    show(api);
    await screen.findByText("2 of 2 trades");
    for (const box of screen.getAllByRole("checkbox", { name: /^Select / })) {
      fireEvent.click(box);
    }
    expect(screen.getByText("2 selected")).toBeInTheDocument();
    expect(screen.getByText("Merge into one trade")).toBeDisabled();
  });

  it("filters by status and strategy", async () => {
    const api = fakeApi();
    show(api);
    await screen.findByText("2 of 2 trades");
    fireEvent.change(screen.getByLabelText("Status"), {
      target: { value: "open" },
    });
    await waitFor(() =>
      expect(api.trades).toHaveBeenLastCalledWith({
        ledger: "broker",
        status: "open",
        limit: 300,
        offset: 0,
      }),
    );
  });
});

describe("TradeReportPanel", () => {
  it("shows what will be sent, then sends the drawn images", async () => {
    const api = fakeApi();
    const draw = vi.fn(async () => "iVBORw0KGgo=");
    renderPanel(api, draw);
    fireEvent.click(await screen.findByText("Prepare a Claude report"));
    const preview = await screen.findByLabelText("What will be sent");
    expect(api.draftReport).toHaveBeenCalledWith("flat:broker:brk-1", true);
    expect(
      within(preview).getByText(/Sent to claude-sonnet-5-5/),
    ).toBeInTheDocument();
    expect(within(preview).getByText(/갭 보고 추격/)).toBeInTheDocument();
    expect(within(preview).getAllByText("(hindsight)").length).toBeGreaterThan(
      0,
    );
    await waitFor(() => expect(draw).toHaveBeenCalledTimes(3));
    expect(api.sendReport).not.toHaveBeenCalled();
    fireEvent.click(within(preview).getByText("Send to Claude"));
    await waitFor(() =>
      expect(api.sendReport).toHaveBeenCalledWith("draft-0123456789ab", "en", [
        { kind: "entry", png_base64: "iVBORw0KGgo=" },
        { kind: "exit", png_base64: "iVBORw0KGgo=" },
        { kind: "context", png_base64: "iVBORw0KGgo=" },
      ]),
    );
    const shown = await screen.findByLabelText("Report");
    expect(within(shown).getByText("Entry timing")).toBeInTheDocument();
    expect(
      within(shown).getByText("프리마켓 고가 위에서 추격했다"),
    ).toBeInTheDocument();
    expect(
      within(shown).getByText(/Not investment advice/),
    ).toBeInTheDocument();
  });

  it("drafts again without the note when it is unticked", async () => {
    const api = fakeApi();
    renderPanel(
      api,
      vi.fn(async () => "x"),
    );
    fireEvent.click(await screen.findByText("Prepare a Claude report"));
    fireEvent.click(await screen.findByLabelText(/Include my note/));
    await waitFor(() =>
      expect(api.draftReport).toHaveBeenLastCalledWith(
        "flat:broker:brk-1",
        false,
      ),
    );
  });
});

function renderPanel(
  api: ReviewApi,
  draw: (image: unknown) => Promise<string>,
) {
  return render(
    <TradeReportPanel
      api={api}
      tradeId="flat:broker:brk-1"
      render={draw as never}
    />,
  );
}

describe("ReviewSettingsPanel", () => {
  it("changes the model and adds account capital", async () => {
    const api = fakeApi();
    render(<ReviewSettingsPanel api={api} today="2026-10-26" />);
    const model = await screen.findByLabelText("Claude model for reports");
    await waitFor(() => expect(model).toHaveValue("claude-sonnet-5-5"));
    fireEvent.change(model, { target: { value: "claude-opus-5-5" } });
    await waitFor(() =>
      expect(api.setReportModel).toHaveBeenCalledWith("claude-opus-5-5"),
    );
    fireEvent.change(screen.getByLabelText(/Amount/), {
      target: { value: "20000" },
    });
    fireEvent.click(screen.getByText("Add"));
    await waitFor(() =>
      expect(api.addCapital).toHaveBeenCalledWith({
        accountRef: "kiwoom-a",
        currency: "USD",
        effectiveFrom: "2026-10-26",
        amount: "20000",
      }),
    );
  });
});
