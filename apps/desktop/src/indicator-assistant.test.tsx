import type { IndicatorConversation } from "@siw/contracts/types";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { IndicatorAssistant } from "./IndicatorAssistant";
import {
  type IndicatorAssistantApi,
  nextRetry,
} from "./indicator-assistant-api";
import type { OhlcvBar } from "./indicators";
import { pineReference, runForAssistant } from "./pine-assistant";

const BARS: OhlcvBar[] = Array.from({ length: 60 }, (_, i) => {
  const close = 100 + Math.sin(i / 4) * 5;
  return {
    time: 1_700_000_000 + i * 300,
    open: close,
    high: close + 1,
    low: close - 1,
    close,
    volume: 1000 + i,
  };
});
const CONTEXT = {
  timeZone: "America/New_York",
  symbol: "AAPL",
  timeframe: "5m",
};

const GOOD = `//@version=5
indicator("Low touch", overlay=true)
len = input.int(20, "Length")
basis = ta.sma(close, len)
plot(basis, "Basis")
plotshape(ta.crossunder(close, basis), "Cross", shape.triangledown)`;

describe("runForAssistant", () => {
  it("summarizes plots and signals without bar values", () => {
    const run = runForAssistant(GOOD, BARS, CONTEXT);
    expect(run.ok).toBe(true);
    if (!run.ok) return;
    expect(run.summary).toContain(
      "Title: Low touch; on the price chart; 60 bars.",
    );
    expect(run.summary).toContain("Inputs: Length=20.");
    expect(run.summary).toMatch(/plot 'Basis' \(line\): last [\d.]+, min/);
    expect(run.summary).toMatch(/empty 32%/); // 19 of 60 bars before the average starts
    expect(run.summary).toMatch(/\d+ shapes\/characters\/arrows/);
  });

  it("flags values that never change and reports errors by line and column", () => {
    const flat = runForAssistant(
      '//@version=5\nindicator("Flat")\nplot(0, "Zero")',
      BARS,
      CONTEXT,
    );
    expect(flat.ok && flat.summary).toContain("never changes");
    const broken = runForAssistant(
      '//@version=5\nindicator("X")\nplot(ta.nope(close))',
      BARS,
      CONTEXT,
    );
    expect(broken).toMatchObject({ ok: false });
    expect(!broken.ok && broken.error).toMatch(/^line 3, column \d+:/);
    const strategy = runForAssistant(
      '//@version=5\nstrategy("S")\nplot(close)',
      BARS,
      CONTEXT,
    );
    expect(!strategy.ok && strategy.error).toContain("indicator()");
  });

  it("describes the subset without strategy functions", () => {
    const reference = pineReference();
    expect(reference).toContain("ta.sma");
    expect(reference).not.toContain("strategy.entry");
  });
});

function conversation(
  changes: Partial<IndicatorConversation>,
): IndicatorConversation {
  return {
    kind: "indicator_conversation",
    schema_version: "0.1",
    conversation_id: "conversation-1",
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

const AT = "2026-10-28T12:00:00Z";

describe("IndicatorAssistant", () => {
  it("runs Claude's script, previews it and applies the last working one", async () => {
    const codeEvent = {
      type: "code" as const,
      at_utc: AT,
      tool_use_id: "t1",
      code: GOOD,
      text: "first try",
    };
    const api: IndicatorAssistantApi = {
      settings: vi.fn(),
      setModel: vi.fn(),
      conversation: vi.fn(),
      ask: vi.fn(),
      start: vi.fn().mockResolvedValue(
        conversation({
          status: "needs_run",
          runs: 1,
          pending_run: { tool_use_id: "t1", code: GOOD },
          events: [
            { type: "question", at_utc: AT, text: "draw it" },
            codeEvent,
          ],
        }),
      ),
      sendRun: vi.fn().mockResolvedValue(
        conversation({
          runs: 1,
          events: [
            { type: "question", at_utc: AT, text: "draw it" },
            codeEvent,
            {
              type: "run",
              at_utc: AT,
              tool_use_id: "t1",
              ok: true,
              text: "ok",
            },
            { type: "answer", at_utc: AT, text: "**Done**: a 20-bar average" },
          ],
          usage: {
            input_tokens: 10,
            output_tokens: 5,
            cache_read_tokens: 0,
            cache_write_tokens: 0,
          },
        }),
      ),
    };
    const onPreview = vi.fn();
    const onApply = vi.fn();
    render(
      <IndicatorAssistant
        api={api}
        conversationId={null}
        initialCode=""
        applied={false}
        chart={() => ({
          symbol: "AAPL",
          timeframe: "5m",
          bars: BARS,
          context: CONTEXT,
        })}
        onPreview={onPreview}
        onApply={onApply}
      />,
    );
    fireEvent.change(screen.getByPlaceholderText(/Bollinger/), {
      target: { value: "draw it" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText("Done");
    const [question, language, reference, context] = vi.mocked(api.start).mock
      .calls[0]!;
    expect(question).toBe("draw it");
    expect(language).toBe("en");
    expect(reference).toContain("ta.sma");
    expect(context).toEqual({
      symbol: "AAPL",
      timeframe: "5m",
      bars: 60,
      code: null,
    });
    const [, toolUseId, result] = vi.mocked(api.sendRun).mock.calls[0]!;
    expect(toolUseId).toBe("t1");
    expect(result.ok).toBe(true);
    expect(result.summary).toContain("Low touch");
    expect(onPreview).toHaveBeenCalledWith(GOOD);
    fireEvent.click(screen.getByRole("button", { name: "Add to chart" }));
    expect(onApply).toHaveBeenCalledWith(GOOD, "conversation-1");
  });

  it("retries on Sonnet high first once the fixes run out", async () => {
    const stopped = conversation({
      status: "stopped",
      runs: 6,
      error: "Claude could not fix the script in 5 tries",
      events: [
        {
          type: "question",
          at_utc: AT,
          text: "draw it",
          model: "claude-sonnet-5-5",
          effort: "medium",
        },
        {
          type: "stopped",
          at_utc: AT,
          text: "Claude could not fix the script in 5 tries",
        },
      ],
    });
    const api: IndicatorAssistantApi = {
      settings: vi.fn(),
      setModel: vi.fn(),
      start: vi.fn(),
      sendRun: vi.fn(),
      conversation: vi.fn().mockResolvedValue(stopped),
      ask: vi.fn().mockResolvedValue(conversation({})),
    };
    render(
      <IndicatorAssistant
        api={api}
        conversationId="conversation-1"
        initialCode={GOOD}
        applied
        chart={() => ({
          symbol: "AAPL",
          timeframe: "5m",
          bars: BARS,
          context: CONTEXT,
        })}
        onPreview={vi.fn()}
        onApply={vi.fn()}
      />,
    );
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Retry with Sonnet (high effort)",
      }),
    );
    expect(
      screen.queryByRole("button", { name: "Retry with Opus" }),
    ).toBeNull();
    await waitFor(() => expect(api.ask).toHaveBeenCalled());
    const [, , context, retry] = vi.mocked(api.ask).mock.calls[0]!;
    expect(retry).toEqual({ model: "claude-sonnet-5-5", effort: "high" });
    expect(context.code).toBe(GOOD);
    expect(
      screen.getByRole("button", { name: "Apply to indicator" }),
    ).toBeTruthy();
  });
});

describe("nextRetry", () => {
  const asked = (model: string, effort?: "medium" | "high") => [
    {
      type: "question" as const,
      at_utc: AT,
      text: "q",
      model,
      ...(effort ? { effort } : {}),
    },
  ];

  it("goes from the default to Sonnet high, then Opus, then stops", () => {
    expect(nextRetry(asked("claude-sonnet-5-5", "medium"))).toEqual({
      model: "claude-sonnet-5-5",
      effort: "high",
    });
    expect(nextRetry(asked("claude-haiku-5-5"))).toEqual({
      model: "claude-sonnet-5-5",
      effort: "high",
    });
    expect(nextRetry(asked("claude-sonnet-5-5", "high"))).toEqual({
      model: "claude-opus-5-5",
      effort: "medium",
    });
    expect(nextRetry(asked("claude-opus-5-5", "medium"))).toBeNull();
  });
});

describe("saved assistant link", () => {
  it("keeps a Pine indicator's conversation id and drops a malformed one", async () => {
    const { parseIndicators } = await import("./indicators");
    const [kept] = parseIndicators([
      {
        key: "a",
        type: "pine",
        pine: GOOD,
        params: {},
        assistant: "conversation-1a",
      },
    ]);
    expect(kept?.assistant).toBe("conversation-1a");
    const [dropped] = parseIndicators([
      { key: "b", type: "pine", pine: GOOD, params: {}, assistant: "../x y" },
    ]);
    expect(dropped?.assistant).toBeUndefined();
  });
});

describe("IndicatorAssistant starting points and images", () => {
  const chart = () => ({
    symbol: "AAPL",
    timeframe: "5m",
    bars: BARS,
    context: CONTEXT,
  });
  const answered = (over: Partial<IndicatorConversation> = {}) =>
    conversation({
      events: [
        { type: "question", at_utc: AT, text: "q" },
        { type: "answer", at_utc: AT, text: "Answered" },
      ],
      ...over,
    });
  const apiFor = (): IndicatorAssistantApi => ({
    settings: vi.fn(),
    setModel: vi.fn(),
    conversation: vi.fn(),
    sendRun: vi.fn(),
    start: vi.fn().mockResolvedValue(answered()),
    ask: vi.fn().mockResolvedValue(answered()),
  });
  const send = () =>
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
  const SCRIPTS = [
    { key: "here", title: "Mine", code: GOOD, sameChart: true },
    { key: "there", title: "Theirs", code: GOOD, sameChart: false },
  ];

  it("moves TradingView code without a request and reports its first error", async () => {
    const api = apiFor();
    const pasted = '//@version=5\nindicator("X")\ntype Foo\nplot(close)';
    render(
      <IndicatorAssistant
        api={api}
        conversationId={null}
        initialCode=""
        applied={false}
        chart={chart}
        onPreview={vi.fn()}
        onApply={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText("Start from"), {
      target: { value: "tradingview" },
    });
    // Nothing to move yet, so nothing to send.
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    fireEvent.change(
      screen.getByRole("textbox", { name: "Pine Script code" }),
      {
        target: { value: pasted },
      },
    );
    send();
    await screen.findByText("Answered");
    const [question, , , context, extras] = vi.mocked(api.start).mock.calls[0]!;
    expect(question).toContain("TradingView");
    expect(context.code).toBe(pasted);
    expect(extras?.origin?.kind).toBe("tradingview");
    expect(extras?.origin?.detail).toMatch(/^line 3, column \d+:/);
    expect(extras?.image).toBeUndefined();
  });

  it("starts from a built-in with its settings and values, never its bars", async () => {
    const api = apiFor();
    render(
      <IndicatorAssistant
        api={api}
        conversationId={null}
        initialCode=""
        applied={false}
        chart={chart}
        onPreview={vi.fn()}
        onApply={vi.fn()}
        initialStart={{ kind: "builtin", id: "rsi", params: { length: 7 } }}
      />,
    );
    send();
    await screen.findByText("Answered");
    const [, , , context, extras] = vi.mocked(api.start).mock.calls[0]!;
    expect(context.code).toBeNull();
    expect(extras?.origin?.kind).toBe("builtin");
    const detail = extras?.origin?.detail ?? "";
    expect(detail).toContain("Length=7");
    expect(detail).toMatch(/Output '.+' \(line\): last [\d.]+, min [\d.]+/);
    expect(detail).toContain("Guide levels: 70, 30");
  });

  it("loads a saved script; only one on this chart is updated by applying", () => {
    const onTarget = vi.fn();
    render(
      <IndicatorAssistant
        api={apiFor()}
        conversationId={null}
        initialCode=""
        applied={false}
        chart={chart}
        onPreview={vi.fn()}
        onApply={vi.fn()}
        scripts={SCRIPTS}
        onTarget={onTarget}
      />,
    );
    fireEvent.change(screen.getByLabelText("Start from"), {
      target: { value: "saved" },
    });
    expect(onTarget).toHaveBeenLastCalledWith("here");
    expect(
      screen.getByRole("textbox", { name: "Pine Script code" }),
    ).toHaveValue(GOOD);
    fireEvent.change(screen.getByLabelText("Indicator"), {
      target: { value: "there" },
    });
    expect(onTarget).toHaveBeenLastCalledWith(null);
    fireEvent.change(screen.getByLabelText("Start from"), {
      target: { value: "new" },
    });
    expect(
      screen.getByRole("textbox", { name: "Pine Script code" }),
    ).toHaveValue("");
  });

  it("hides the choice when reopening a saved conversation", async () => {
    const api = apiFor();
    vi.mocked(api.conversation).mockResolvedValue(answered());
    render(
      <IndicatorAssistant
        api={api}
        conversationId="conversation-1"
        initialCode={GOOD}
        applied
        chart={chart}
        onPreview={vi.fn()}
        onApply={vi.fn()}
        scripts={SCRIPTS}
      />,
    );
    await screen.findByText("Answered");
    expect(screen.queryByLabelText("Start from")).toBeNull();
  });

  it("sends a chart image once, only after it is attached", async () => {
    const api = apiFor();
    const image = {
      png_base64: "aW1hZ2U=",
      bars: 40,
      from_utc: "2026-10-28T13:30:00Z",
      to_utc: "2026-10-28T16:45:00Z",
    };
    const captureImage = vi.fn().mockResolvedValue(image);
    render(
      <IndicatorAssistant
        api={api}
        conversationId={null}
        initialCode=""
        applied={false}
        chart={chart}
        onPreview={vi.fn()}
        onApply={vi.fn()}
        captureImage={captureImage}
      />,
    );
    const ask = (text: string) =>
      fireEvent.change(screen.getByPlaceholderText(/Bollinger/), {
        target: { value: text },
      });
    // Attached then removed: nothing goes.
    fireEvent.click(screen.getByRole("button", { name: "Attach chart image" }));
    await screen.findByAltText("Candles of the 40 bars in view");
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(screen.queryByAltText("Candles of the 40 bars in view")).toBeNull();
    ask("first");
    send();
    await screen.findByText("Answered");
    expect(vi.mocked(api.start).mock.calls[0]![4]).toEqual({});
    // Attached: it goes with the next request and then leaves the panel.
    fireEvent.click(screen.getByRole("button", { name: "Attach chart image" }));
    await screen.findByAltText("Candles of the 40 bars in view");
    ask("second");
    send();
    await waitFor(() => expect(api.ask).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.ask).mock.calls[0]![4]).toEqual(image);
    await waitFor(() =>
      expect(
        screen.queryByAltText("Candles of the 40 bars in view"),
      ).toBeNull(),
    );
    ask("third");
    send();
    await waitFor(() => expect(api.ask).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.ask).mock.calls[1]![4]).toBeUndefined();
  });

  it("stops offering images at the conversation's limit", async () => {
    const api = apiFor();
    vi.mocked(api.conversation).mockResolvedValue(
      answered({
        events: Array.from({ length: 5 }, () => ({
          type: "question" as const,
          at_utc: AT,
          text: "q",
          has_image: true,
        })),
      }),
    );
    render(
      <IndicatorAssistant
        api={api}
        conversationId="conversation-1"
        initialCode=""
        applied={false}
        chart={chart}
        onPreview={vi.fn()}
        onApply={vi.fn()}
        captureImage={vi.fn()}
      />,
    );
    await screen.findAllByText("Chart image attached");
    expect(
      screen.getByRole("button", { name: "Attach chart image" }),
    ).toBeDisabled();
  });
});
