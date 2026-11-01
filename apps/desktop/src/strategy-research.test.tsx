import type { StrategyConversation } from "@siw/contracts/types";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { OhlcvBar } from "./indicators";
import { pineReference } from "./pine-assistant";
import {
  concentrationOf,
  MAX_SENT_TRADES,
  pineStrategiesOn,
  resultsOf,
  runStrategy,
} from "./strategy-research";
import type { StrategyResearchApi } from "./strategy-research-api";
import { StrategyResearch } from "./StrategyResearch";

const BARS: OhlcvBar[] = Array.from({ length: 120 }, (_, i) => {
  const close = 100 + Math.sin(i / 3) * 5;
  return {
    time: 1_700_000_000 + i * 300,
    open: close,
    high: close + 1,
    low: close - 1,
    close,
    volume: 1000,
  };
});
const CONTEXT = {
  timeZone: "America/New_York",
  symbol: "AAPL",
  timeframe: "5m",
};
const CHART = { symbol: "AAPL", timeframe: "5m", bars: BARS, context: CONTEXT };
const GOOD = `//@version=5
strategy("Cross", overlay=true)
len = input.int(5, "Length")
basis = ta.sma(close, len)
if ta.crossover(close, basis)
    strategy.entry("L", strategy.long)
if ta.crossunder(close, basis)
    strategy.close("L")`;
const NEVER = `//@version=5
strategy("Never")
if close < 0
    strategy.entry("L", strategy.long)`;

describe("runStrategy", () => {
  it("tells Claude the trade count and never the profit", () => {
    const run = runStrategy(GOOD, BARS, CONTEXT);
    expect(run.ok).toBe(true);
    if (!run.ok) return;
    expect(run.summary).toContain("Title: Cross; 120 bars.");
    expect(run.summary).toContain("Inputs: Length=5.");
    expect(run.summary).toMatch(/Closed trades: [1-9]\d*/);
    expect(run.summary).not.toContain(run.report.netProfit);
    expect(run.summary).not.toMatch(/profit|win|drawdown/i);
  });

  it("says a strategy made no trades, refuses indicators and reports errors", () => {
    const never = runStrategy(NEVER, BARS, CONTEXT);
    expect(never.ok && never.summary).toContain("It made no trades");
    const indicator = runStrategy(
      '//@version=5\nindicator("X")\nplot(close)',
      BARS,
      CONTEXT,
    );
    expect(!indicator.ok && indicator.error).toContain("strategy()");
    const broken = runStrategy(
      '//@version=5\nstrategy("X")\nplot(ta.nope(close))',
      BARS,
      CONTEXT,
    );
    expect(!broken.ok && broken.error).toMatch(/^line 3, column \d+:/);
  });
});

describe("resultsOf", () => {
  it("keeps totals and only the latest trades, in UTC", () => {
    const run = runStrategy(GOOD, BARS, CONTEXT);
    if (!run.ok) throw new Error(run.error);
    const many = {
      ...run.report,
      closedTrades: 80,
      trades: Array.from({ length: 80 }, (_, i) => ({
        ...run.report.trades[0],
        entryId: `e${i}`,
        entryPrice: String(i),
      })),
    };
    const results = resultsOf(many, CHART, 2);
    expect(results.version).toBe(2);
    expect(results.closed_trades).toBe(80);
    expect(results.trades).toHaveLength(MAX_SENT_TRADES);
    // Counted over all 80 trades, not only the 50 that are sent.
    expect(results.concentration.top_days[0]?.trades).toBe(80);
    expect(results.trades[MAX_SENT_TRADES - 1].entry_price).toBe("79");
    expect(results.from_utc).toBe("2023-11-14T22:13:20Z");
    expect(JSON.stringify(results)).not.toContain('"entryId"');
  });
});

describe("concentrationOf", () => {
  // 2026-10-13 is one exchange day in New York from 04:00 UTC to 04:00 UTC the next day.
  const at = (iso: string) => ({ entryTime: Date.parse(iso) / 1000 });

  it("counts every trade by exchange day and the share of the top three", () => {
    const trades = [
      ...Array.from({ length: 29 }, () => at("2026-10-13T14:00:00Z")),
      at("2026-10-14T03:00:00Z"), // still the 13th in New York
      at("2026-10-14T14:00:00Z"),
      at("2026-10-15T14:00:00Z"),
      at("2026-10-16T14:00:00Z"),
      at("2026-10-19T14:00:00Z"),
    ];
    expect(concentrationOf(trades, "America/New_York")).toEqual({
      days_with_trades: 5,
      top_days: [
        { date: "2026-10-13", trades: 30 },
        { date: "2026-10-14", trades: 1 },
        { date: "2026-10-15", trades: 1 },
      ],
      top3_share_percent: "94.12",
    });
  });

  it("is empty without trades", () => {
    expect(concentrationOf([], "America/New_York")).toEqual({
      days_with_trades: 0,
      top_days: [],
      top3_share_percent: "0.00",
    });
  });
});

describe("pineStrategiesOn and the reference", () => {
  it("offers only strategies, this chart's first", () => {
    const pine = (key: string, source: string) => ({
      key,
      type: "pine",
      params: {},
      pine: source,
    });
    const found = pineStrategiesOn(
      [
        {
          instrumentId: "a",
          timeframe: "5m",
          indicators: [pine("s1", GOOD)],
        },
        {
          instrumentId: "b",
          timeframe: "1h",
          indicators: [
            pine("s2", GOOD),
            pine("i1", '//@version=5\nindicator("X")\nplot(close)'),
          ],
        },
      ] as never,
      1,
      (id) => id.toUpperCase(),
    );
    expect(found.map((item) => [item.key, item.sameChart])).toEqual([
      ["s2", true],
      ["s1", false],
    ]);
  });

  it("describes strategies only to strategy research", () => {
    expect(pineReference("strategy")).toContain("strategy.entry");
    expect(pineReference("strategy")).toContain("Fills:");
    expect(pineReference()).not.toContain("strategy.entry");
  });
});

function conversation(
  changes: Partial<StrategyConversation>,
): StrategyConversation {
  return {
    kind: "strategy_conversation",
    schema_version: "0.1",
    conversation_id: "conv-1",
    model: "claude-sonnet-5-5",
    status: "idle",
    error: null,
    events: [],
    pending_run: null,
    runs: 0,
    max_runs: 6,
    usage: {
      input_tokens: 0,
      output_tokens: 0,
      cache_read_tokens: 0,
      cache_write_tokens: 0,
    },
    ...changes,
  };
}

const AT = "2026-11-01T00:00:00Z";
const ANSWERED = conversation({
  events: [
    { type: "question", text: "전략", at_utc: AT },
    { type: "code", tool_use_id: "t1", code: GOOD, text: "v1", at_utc: AT },
    { type: "run", tool_use_id: "t1", ok: true, text: "ran", at_utc: AT },
    { type: "answer", text: "작성했습니다", at_utc: AT },
  ],
});

function apiFor(): StrategyResearchApi {
  return {
    start: vi.fn(() =>
      Promise.resolve(
        conversation({
          status: "needs_run",
          pending_run: { tool_use_id: "t1", code: GOOD },
          events: [
            { type: "question", text: "전략", at_utc: AT },
            {
              type: "code",
              tool_use_id: "t1",
              code: GOOD,
              text: "v1",
              at_utc: AT,
            },
          ],
        }),
      ),
    ),
    conversation: vi.fn(() => Promise.resolve(ANSWERED)),
    ask: vi.fn(() => Promise.resolve(ANSWERED)),
    sendResults: vi.fn(() => Promise.resolve(ANSWERED)),
    sendRun: vi.fn(() => Promise.resolve(ANSWERED)),
  };
}

function renderPanel(api: StrategyResearchApi, onPreview = vi.fn()) {
  const onApply = vi.fn();
  render(
    <StrategyResearch
      api={api}
      conversationId={null}
      initialCode=""
      applied={false}
      chart={() => CHART}
      scripts={[]}
      onTarget={vi.fn()}
      onPreview={onPreview}
      onApply={onApply}
    />,
  );
  return { onApply, onPreview };
}

const ask = async (api: StrategyResearchApi) => {
  fireEvent.change(screen.getByRole("textbox", { name: "Request" }), {
    target: { value: "EMA cross" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(api.sendRun).toHaveBeenCalled());
  await screen.findByText("작성했습니다");
};

describe("StrategyResearch", () => {
  it("runs Claude's script, previews it and sends only the error or trade count", async () => {
    const api = apiFor();
    const { onPreview } = renderPanel(api);
    await ask(api);
    expect(vi.mocked(api.start).mock.calls[0][2]).toContain("strategy.entry");
    expect(onPreview).toHaveBeenCalledWith(GOOD);
    const [, tool, result, kept] = vi.mocked(api.sendRun).mock.calls[0];
    expect(tool).toBe("t1");
    expect(result.ok).toBe(true);
    expect(JSON.stringify(result)).not.toMatch(/net_?profit/i);
    // The run's backtest is kept with its version, separately from what Claude gets.
    expect(kept?.version).toBe(1);
    expect(api.sendResults).not.toHaveBeenCalled();
  });

  it("shows what will be sent before sending results, and can cancel", async () => {
    const api = apiFor();
    renderPanel(api);
    await ask(api);
    const button = screen.getByRole("button", { name: /Send results/ });
    fireEvent.click(button);
    const draft = await screen.findByRole("region", {
      name: "What will be sent",
    });
    expect(draft.textContent).toContain("net_profit");
    expect(api.sendResults).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Don't send" }));
    expect(screen.queryByRole("region", { name: "What will be sent" })).toBe(
      null,
    );
    expect(api.sendResults).not.toHaveBeenCalled();

    fireEvent.click(button);
    fireEvent.click(screen.getByRole("button", { name: "Send these results" }));
    await waitFor(() => expect(api.sendResults).toHaveBeenCalled());
    const [id, question, context, results] = vi.mocked(api.sendResults).mock
      .calls[0];
    expect(id).toBe("conv-1");
    expect(question).toContain("Review this backtest");
    expect(context.code).toBe(GOOD);
    expect(results.version).toBe(1);
    expect(results.symbol).toBe("AAPL");
  });

  it("cannot send results before a conversation, and adds only on request", async () => {
    const api = apiFor();
    const { onApply } = renderPanel(api);
    expect(
      screen.getByRole<HTMLButtonElement>("button", { name: /Send results/ })
        .disabled,
    ).toBe(true);
    await ask(api);
    expect(onApply).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Add to chart" }));
    expect(onApply).toHaveBeenCalledWith(GOOD, "conv-1");
  });

  it("reports a failed run back to Claude and draws nothing", async () => {
    const api = apiFor();
    const broken = '//@version=5\nstrategy("X")\nplot(ta.nope(close))';
    vi.mocked(api.start).mockResolvedValue(
      conversation({
        status: "needs_run",
        pending_run: { tool_use_id: "t1", code: broken },
      }),
    );
    const { onPreview } = renderPanel(api);
    fireEvent.change(screen.getByRole("textbox", { name: "Request" }), {
      target: { value: "x" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(api.sendRun).toHaveBeenCalled());
    const [, , result, kept] = vi.mocked(api.sendRun).mock.calls[0];
    expect(result.ok).toBe(false);
    expect(result.error).toMatch(/^line 3/);
    expect(kept).toBeUndefined();
    expect(onPreview).not.toHaveBeenCalled();
  });

  it("offers Opus once after five failed fixes", async () => {
    const api = apiFor();
    vi.mocked(api.conversation).mockResolvedValue(
      conversation({
        status: "stopped",
        events: [
          {
            type: "question",
            text: "x",
            model: "claude-sonnet-5-5",
            at_utc: AT,
          },
          { type: "stopped", text: "could not fix", at_utc: AT },
        ],
      }),
    );
    render(
      <StrategyResearch
        api={api}
        conversationId="conv-1"
        initialCode=""
        applied={false}
        chart={() => CHART}
        scripts={[]}
        onTarget={vi.fn()}
        onPreview={vi.fn()}
        onApply={vi.fn()}
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: /Opus/ }));
    await waitFor(() => expect(api.ask).toHaveBeenCalled());
    expect(vi.mocked(api.ask).mock.calls[0][3]).toBe(true);
  });
});
