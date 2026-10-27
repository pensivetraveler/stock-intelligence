import type {
  MarketBarsResponse,
  ReviewAccountCapitalList,
  ReviewCheckStatus,
  ReviewPeriod,
  ReviewPeriodCommentary,
  ReviewPeriodList,
  ReviewWhatIf,
  ReviewConversation,
  ReviewPrincipleCheck,
  ReviewPrinciples,
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
import { PeriodCommentary } from "./PeriodCommentary";
import { PeriodReview } from "./PeriodReview";
import { PrinciplesPanel } from "./PrinciplesPanel";
import { ReportConversation } from "./ReportConversation";
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
const period = fixture<ReviewPeriod>("review-period.json");
const periodList = fixture<ReviewPeriodList>("review-period-list.json");
const commentary: ReviewPeriodCommentary = {
  kind: "review_period_commentary",
  schema_version: "0.1",
  review_id: "period-1",
  model: "claude-sonnet-5-5",
  language: "en",
  created_at_utc: "2026-10-27T00:00:00Z",
  summary: "Losses came from the open.",
  repeated_mistakes: ["Chasing in the first 30 minutes"],
  went_well: [],
  focus_next: ["Wait 30 minutes"],
  principle_suggestions: [
    {
      action: "add",
      item_id: null,
      scope: "shared",
      kind: "no_entry_first_minutes",
      value: "30",
      text: "No buying in the first 30 minutes",
      reason: "first30: 1 trade, -51",
    },
    {
      action: "change",
      item_id: "p1",
      scope: "shared",
      kind: "stop_loss_pct",
      value: "3",
      text: "Stop at 3%",
      reason: "average loss -6%",
    },
  ],
  uncertainty: "",
  usage: { input_tokens: 10, output_tokens: 5 },
};
const draft = fixture<ReviewReportDraft>("review-report-draft.json");
const report = fixture<ReviewTradeReport>("review-trade-report.json");
const whatIf: ReviewWhatIf = {
  kind: "review_what_if",
  schema_version: "0.1",
  trade_id: "flat:broker:brk-1",
  principles: {
    type: "principles",
    status: "computed",
    hindsight: true,
    reason: "stop",
    exit_price: "93",
    exit_at_utc: "2026-03-03T16:30:00Z",
    timeframe: "5m",
    quantity: "20",
    gross_pnl: "-140",
    actual_gross_pnl: "40",
    difference: "-180",
    notes: ["all shares are taken at the average buy from the first buy"],
  },
  strategy: null,
};
const checkStatus: ReviewCheckStatus = {
  kind: "review_check_status",
  schema_version: "0.1",
  auto_from_date: "2026-07-28",
  queued: 0,
  running: false,
  done: 12,
  failed: 0,
  last_error: null,
};
const noPrinciples: ReviewPrinciples = {
  kind: "review_principles",
  schema_version: "0.1",
  current: null,
  versions: [],
};
const principleCheck: ReviewPrincipleCheck = {
  kind: "review_principle_check",
  schema_version: "0.1",
  trade_id: "flat:broker:brk-1",
  version: 2,
  after_the_fact: true,
  results: [
    {
      item_id: "p1",
      scope: "shared",
      kind: "stop_loss_pct",
      value: "7",
      text: "손절 -7%",
      status: "broken",
      detail: "sold at -9.10%, past the stop of -7% (93.0000)",
    },
  ],
};
const emptyConversation: ReviewConversation = {
  kind: "review_conversation",
  schema_version: "0.1",
  conversation_id: null,
  report_id: "report-0123456789ab",
  model: null,
  status: "idle",
  error: null,
  events: [],
  pending_images: [],
  usage: {
    input_tokens: 0,
    output_tokens: 0,
    cache_read_tokens: 0,
    cache_write_tokens: 0,
  },
};
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
    conversation: vi.fn(async () => emptyConversation),
    askQuestion: vi.fn(async () => emptyConversation),
    sendChartImages: vi.fn(async () => emptyConversation),
    principles: vi.fn(async () => noPrinciples),
    draftPrinciples: vi.fn(async () => ({
      kind: "review_principle_draft" as const,
      schema_version: "0.1" as const,
      items: [
        {
          scope: "shared",
          kind: "stop_loss_pct",
          value: "7",
          text: "손절 -7%",
        },
        {
          scope: "shared",
          kind: "text",
          value: null,
          text: "급등주 추격 금지",
        },
      ],
      notes: "",
      model: "claude-sonnet-5-5",
    })),
    savePrinciples: vi.fn(async () => noPrinciples),
    principleCheck: vi.fn(async () => principleCheck),
    whatIf: vi.fn(async (tradeId: string) => ({
      ...whatIf,
      trade_id: tradeId,
    })),
    strategySuggestions: vi.fn(async (tradeId: string) => ({
      kind: "review_strategy_suggestions" as const,
      schema_version: "0.1" as const,
      trade_id: tradeId,
      suggestions: [
        {
          strategy_id: "strategy-x",
          name: "Breakout",
          timeframes: ["5m" as const],
        },
      ],
    })),
    checkStatus: vi.fn(async () => checkStatus),
    runChecks: vi.fn(async () => ({ ...checkStatus, queued: 3 })),
    makePeriodReview: vi.fn(async () => period),
    periodReviews: vi.fn(async () => periodList),
    periodReview: vi.fn(async () => period),
    periodPayload: vi.fn(async () => ({
      kind: "review_period_payload" as const,
      schema_version: "0.1" as const,
      review_id: period.review_id,
      payload: {
        statistics: [{}, {}],
        principles: [{}],
        best_and_worst_trades: {
          USD: {
            best: [{ note_written_after_the_trade: "메모" }],
            worst: [{ note_written_after_the_trade: null }],
          },
        },
      },
    })),
    makePeriodCommentary: vi.fn(async () => commentary),
    periodCommentaries: vi.fn(async () => ({
      kind: "review_period_commentary_list" as const,
      schema_version: "0.1" as const,
      review_id: period.review_id,
      commentaries: [],
    })),
    periodConversation: vi.fn(async () => emptyConversation),
    askPeriodQuestion: vi.fn(async () => emptyConversation),
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
    expect(within(preview).getByText("Kept: 손절 -7% (7)")).toBeInTheDocument();
    expect(
      within(preview).getByText(
        "Claude judges: 뉴스 없는 급등은 추격하지 않는다",
      ),
    ).toBeInTheDocument();
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

describe("ReportConversation", () => {
  it("asks, draws the requested chart, and shows the answer", async () => {
    const image = draft.images[0]!;
    const asked: ReviewConversation = {
      ...emptyConversation,
      conversation_id: "conversation-1",
      model: "claude-sonnet-5-5",
      status: "needs_images",
      events: [
        {
          type: "question",
          text: "청산 구간 차트?",
          at_utc: "2026-10-27T00:00:00Z",
        },
        {
          type: "request",
          tool: "chart_image",
          input: {},
          summary: "chart us:aapl:xnas:2026 5m",
          error: false,
          hindsight: false,
          at_utc: "2026-10-27T00:00:01Z",
        },
      ],
      pending_images: [{ ...image, kind: "tool", tool_use_id: "c1" }],
    };
    const answered: ReviewConversation = {
      ...asked,
      status: "idle",
      pending_images: [],
      events: [
        ...asked.events,
        {
          type: "answer",
          text: "청산은 박스 하단 근처였습니다.\n- **매도** 3분 뒤\n\n| 종목 | 손익 |\n|---|---|\n| FUFU | -1.56 |",
          model: "claude-sonnet-5-5",
          at_utc: "2026-10-27T00:00:05Z",
        },
      ],
      usage: {
        input_tokens: 900,
        output_tokens: 80,
        cache_read_tokens: 5000,
        cache_write_tokens: 0,
      },
    };
    const api = fakeApi({
      askQuestion: vi.fn(async () => asked),
      sendChartImages: vi.fn(async () => answered),
    });
    const draw = vi.fn(async () => "iVBORw0KGgo=");
    render(
      <ReportConversation
        api={api}
        reportId="report-0123456789ab"
        render={draw as never}
      />,
    );
    fireEvent.change(await screen.findByLabelText("Your question"), {
      target: { value: "청산 구간 차트?" },
    });
    fireEvent.click(screen.getByText("Ask Claude"));
    await waitFor(() =>
      expect(api.sendChartImages).toHaveBeenCalledWith("conversation-1", [
        { tool_use_id: "c1", png_base64: "iVBORw0KGgo=" },
      ]),
    );
    expect(api.askQuestion).toHaveBeenCalledWith(
      "report-0123456789ab",
      "청산 구간 차트?",
      "en",
    );
    expect(draw).toHaveBeenCalledTimes(1);
    expect(
      await screen.findByText("청산은 박스 하단 근처였습니다."),
    ).toBeInTheDocument();
    expect(screen.getByText(/Request chart_image:/)).toBeInTheDocument();
    expect(screen.getByText("매도").tagName).toBe("STRONG");
    expect(screen.getByText("-1.56").tagName).toBe("TD");
    expect(screen.getByText("종목").tagName).toBe("TH");
    expect(screen.getByText(/900 in, 5000 from cache/)).toBeInTheDocument();
  });
});

describe("principles", () => {
  it("turns text into items with Claude, edits them and saves a version", async () => {
    const api = fakeApi();
    render(<PrinciplesPanel api={api} strategies={[]} />);
    expect(await screen.findByText(/No principles yet/)).toBeInTheDocument();
    fireEvent.change(
      screen.getByLabelText("Your principles, in your own words"),
      {
        target: { value: "손절은 7%, 급등주 추격 금지" },
      },
    );
    fireEvent.click(screen.getByText("Turn into items with Claude"));
    const proposed = await screen.findByLabelText("Items to save");
    expect(api.draftPrinciples).toHaveBeenCalledWith(
      "손절은 7%, 급등주 추격 금지",
      "en",
    );
    expect(api.savePrinciples).not.toHaveBeenCalled();
    fireEvent.change(within(proposed).getAllByLabelText("Value")[0]!, {
      target: { value: "5" },
    });
    fireEvent.click(within(proposed).getByText("Save as a new version"));
    await waitFor(() =>
      expect(api.savePrinciples).toHaveBeenCalledWith(
        [
          {
            scope: "shared",
            kind: "stop_loss_pct",
            value: "5",
            text: "손절 -7%",
          },
          {
            scope: "shared",
            kind: "text",
            value: null,
            text: "급등주 추격 금지",
          },
        ],
        "손절은 7%, 급등주 추격 금지",
      ),
    );
  });

  it("shows the trade's check with the version and after-the-fact mark", async () => {
    const api = fakeApi();
    show(api);
    fireEvent.click((await screen.findAllByText("AAPL"))[1]!);
    const check = await screen.findByLabelText("Principles check");
    expect(
      await within(check).findByText(/version 2 · written after this trade/),
    ).toBeInTheDocument();
    expect(within(check).getByText("Broken")).toBeInTheDocument();
    expect(within(check).getByText(/past the stop of -7%/)).toBeInTheDocument();
  });
});

describe("what-ifs and checks", () => {
  it("shows the principles replay and suggests strategies for an unlinked trade", async () => {
    const unlinked = {
      ...list,
      trades: list.trades.map((trade) => ({ ...trade, strategy_id: null })),
    };
    const api = fakeApi({ trades: vi.fn(async () => unlinked) });
    show(api);
    fireEvent.click((await screen.findAllByText("AAPL"))[1]!);
    const panel = await screen.findByLabelText("What if");
    expect(await within(panel).findByText("-180")).toBeInTheDocument();
    expect(within(panel).getByText(/stopped out/)).toBeInTheDocument();
    expect(within(panel).getByText(/not available/)).toBeInTheDocument();
    expect(
      await within(panel).findByText(/Breakout \(5m\)/),
    ).toBeInTheDocument();
    fireEvent.click(within(panel).getByText("Link this strategy"));
    await waitFor(() =>
      expect(api.assign).toHaveBeenCalledWith(
        [
          { ledger: "broker", execution_id: "brk-5" },
          { ledger: "broker", execution_id: "brk-6" },
        ],
        "strategy-x",
      ),
    );
    fireEvent.change(within(panel).getByLabelText("Replay strategy"), {
      target: { value: "strategy-x" },
    });
    await waitFor(() =>
      expect(api.whatIf).toHaveBeenLastCalledWith(
        "flat:broker:brk-5",
        "strategy-x",
      ),
    );
  });

  it("lists broken counts, filters them and runs checks over dates", async () => {
    const api = fakeApi();
    show(api);
    await screen.findByText("2 of 2 trades");
    expect(screen.getByText("1 broken")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Principles"), {
      target: { value: "broken" },
    });
    await waitFor(() =>
      expect(api.trades).toHaveBeenLastCalledWith({
        ledger: "broker",
        principles: "broken",
        limit: 300,
        offset: 0,
      }),
    );
    expect(
      await screen.findByText(
        /Trades from 2026-07-28 are checked automatically/,
      ),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("From"), {
      target: { value: "2026-01-01" },
    });
    fireEvent.click(screen.getByText("Run checks"));
    await waitFor(() =>
      expect(api.runChecks).toHaveBeenCalledWith("2026-01-01", "2026-10-26"),
    );
  });
});

// The chart library needs a real canvas.
vi.mock("./PeriodCurve", () => ({
  PeriodCurve: ({ points }: { points: unknown[] }) => (
    <div data-testid="period-curve">{points.length} points</div>
  ),
}));

describe("period review", () => {
  it("makes a review for last week and shows it against the period before", async () => {
    const api = fakeApi();
    render(<PeriodReview api={api} today="2026-03-11" strategies={[]} />);
    expect(
      await screen.findByText("2026-03-02 → 2026-03-06"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByText("Make the review"));
    await waitFor(() =>
      expect(api.makePeriodReview).toHaveBeenCalledWith({
        fromDate: "2026-03-02",
        toDate: "2026-03-08",
        ledger: "broker",
      }),
    );
    const usd = await screen.findByLabelText("USD");
    const results = within(usd).getByLabelText("Results");
    const net = within(results).getByText("Net P&L").closest("tr")!;
    expect(within(net).getByText("-53")).toBeInTheDocument();
    expect(within(net).getByText("199")).toBeInTheDocument();
    expect(within(net).getByText("▼ 252")).toBeInTheDocument();
    expect(within(usd).getByText("3 points")).toBeInTheDocument();
    expect(within(usd).getByLabelText("Time of day")).toHaveTextContent(
      "First 30 minutes",
    );
    expect(within(usd).getByLabelText("Daily P&L")).toBeInTheDocument();
  });

  it("presets and saved reviews", async () => {
    const api = fakeApi();
    render(<PeriodReview api={api} today="2026-03-11" strategies={[]} />);
    fireEvent.change(await screen.findByLabelText("Period"), {
      target: { value: "lastMonth" },
    });
    fireEvent.click(screen.getByText("Make the review"));
    await waitFor(() =>
      expect(api.makePeriodReview).toHaveBeenCalledWith(
        expect.objectContaining({
          fromDate: "2026-02-01",
          toDate: "2026-02-28",
        }),
      ),
    );
    // After making one, the review shows next to the saved list: open the saved entry.
    const saved = screen.getByLabelText("Saved reviews");
    fireEvent.click(within(saved).getByText("2026-03-02 → 2026-03-06"));
    await waitFor(() => expect(api.periodReview).toHaveBeenCalled());
  });
});

describe("period commentary", () => {
  it("previews, sends, and opens chosen suggestions in the principles editor", async () => {
    const current: ReviewPrinciples = {
      ...noPrinciples,
      current: {
        version: 1,
        created_at_utc: "2026-10-01T00:00:00Z",
        source_text: "",
        items: [
          {
            item_id: "p1",
            scope: "shared",
            kind: "stop_loss_pct",
            value: "7",
            text: "Stop at 7%",
          },
        ],
      },
      versions: [
        { version: 1, created_at_utc: "2026-10-01T00:00:00Z", item_count: 1 },
      ],
    };
    const api = fakeApi({ principles: vi.fn(async () => current) });
    render(<PeriodCommentary api={api} reviewId="period-1" strategies={[]} />);
    fireEvent.click(await screen.findByText("Prepare a Claude commentary"));
    expect(
      await screen.findByText(
        /statistics for 2 currencies, 1 principles, 2 best\/worst trades \(1 with your notes/,
      ),
    ).toBeInTheDocument();
    expect(api.makePeriodCommentary).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Send to Claude"));
    const view = await screen.findByLabelText("Period commentary");
    expect(
      within(view).getByText("Chasing in the first 30 minutes"),
    ).toBeInTheDocument();
    const boxes = within(view).getAllByRole("checkbox");
    fireEvent.click(boxes[0]!);
    fireEvent.click(boxes[1]!);
    fireEvent.click(within(view).getByText(/Open the chosen changes/));
    const editor = await screen.findByLabelText("Items to save");
    const texts = within(editor)
      .getAllByLabelText("Wording")
      .map((input) => (input as HTMLInputElement).value);
    expect(texts).toEqual(["Stop at 3%", "No buying in the first 30 minutes"]);
    expect(api.savePrinciples).not.toHaveBeenCalled();
  });

  it("asks period follow-ups through the period conversation", async () => {
    const api = fakeApi();
    render(<PeriodCommentary api={api} reviewId="period-1" strategies={[]} />);
    fireEvent.change(await screen.findByLabelText("Your question"), {
      target: { value: "Which tag lost most?" },
    });
    fireEvent.click(screen.getByText("Ask Claude"));
    await waitFor(() =>
      expect(api.askPeriodQuestion).toHaveBeenCalledWith(
        "period-1",
        "Which tag lost most?",
        "en",
      ),
    );
    expect(api.askQuestion).not.toHaveBeenCalled();
  });
});
