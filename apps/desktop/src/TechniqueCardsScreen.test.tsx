import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type {
  CardCondition,
  TechniqueCard,
  TechniqueCardsApi,
  TechniquePreview,
} from "./technique-cards-api";
import { slotsLabel } from "./technique-cards-api";
import { TechniqueCardsScreen } from "./TechniqueCardsScreen";

const ITEM = "kl://kl-lib/item/01M7CJ84TD615YWVWKWHDTKGH7";

const entry: CardCondition = {
  field: "entry",
  side: "long",
  text: "Buy when the close crosses above the 50 EMA.",
  basis: "source",
  left: { kind: "price", name: "close", period: null, value: null },
  comparator: "cross_above",
  right: { kind: "indicator", name: "ema", period: 50, value: null },
  timeframe: "",
  citations: [{ locator: "00:50", quote: "When the price closes back above" }],
  value_citations: [{ value: 50, locator: "00:10", quote: "a fifty period" }],
};
const rsi: CardCondition = {
  ...entry,
  text: "RSI(14) above 50",
  left: { kind: "indicator", name: "rsi", period: 14, value: null },
  comparator: "above",
  right: { kind: "value", name: "none", period: null, value: 50 },
  value_citations: [],
};

const preview: TechniquePreview = {
  kind: "technique_source_preview",
  item_ref: ITEM,
  title: "Demo lesson",
  url: "https://example.test/v",
  lines: 5,
  parts: 1,
  characters: 1200,
  model: "claude-sonnet-5-5",
  fallback: "openai/gpt-6-luna",
  models: ["claude-sonnet-5-5", "google/gemini-4-argon", "openai/gpt-6-luna"],
  estimated_cost_usd: "0.06",
  estimate_note: "estimate",
  verification_status: "unverified",
  data_grade: "private",
  recheck_outcome: "conditional",
};

function card(overrides: Partial<TechniqueCard> = {}): TechniqueCard {
  return {
    kind: "technique_card",
    card_id: "c1",
    item_ref: ITEM,
    title: "Demo lesson",
    url: "https://example.test/v",
    version: 1,
    usage: {
      input_tokens: 9,
      output_tokens: 9,
      cost_usd: "0.2",
      models: ["claude-sonnet-5-5"],
      calls: 1,
      parts: 1,
      flags: {},
    },
    recheck_outcome: "conditional",
    rechecked_at: "2026-11-14T00:00:00Z",
    withdrawn: false,
    ready: false,
    blocks: ["condition 1 not resolved"],
    corrected: 0,
    created_at: "2026-11-14T00:00:00Z",
    updated_at: "2026-11-14T00:00:00Z",
    card: {
      source_id: "demo",
      title: "Demo lesson",
      conditions: [entry, rsi],
      timeframe: [],
      universe: [],
      position: [],
      inexpressible: [],
      ambiguities: [
        {
          text: "How much is a surge?",
          question: "What volume ratio?",
          citations: [],
        },
      ],
    },
    checks: {
      conditions: [
        { index: 0, field: "entry", flags: [] },
        {
          index: 1,
          field: "entry",
          flags: [{ code: "unbacked", reason: "no value citation saying 14" }],
        },
      ],
      counts: { unbacked: 1, no_criterion: 0 },
    },
    resolutions: { conditions: {}, ambiguities: {} },
    verification_status: "unverified",
    timeline: [
      {
        time: "00:10",
        text: "a fifty period moving average",
        conditions: [0],
        universe: [],
      },
      {
        time: "01:10",
        text: "The stop sits two ATR below",
        conditions: [],
        universe: [],
      },
    ],
    ...overrides,
  };
}

function fakeApi(): TechniqueCardsApi {
  return {
    sources: vi.fn().mockResolvedValue([
      {
        item_ref: ITEM,
        title: "Demo lesson",
        url: "https://example.test/v",
        source_key: "demo",
        tags: ["매매기법"],
        delivered_at: "2026-11-14T00:00:00Z",
        eligibility: ["conditional"],
        card_ids: [],
      },
    ]),
    preview: vi.fn().mockResolvedValue(preview),
    start: vi
      .fn()
      .mockResolvedValue({ kind: "technique_card_job", job_id: "j1" }),
    job: vi.fn().mockResolvedValue({
      kind: "technique_card_job",
      job_id: "j1",
      state: "done",
      card_id: "c1",
    }),
    cards: vi.fn().mockResolvedValue([]),
    card: vi.fn().mockResolvedValue(card()),
    resolve: vi.fn().mockResolvedValue(card()),
    recheck: vi.fn().mockResolvedValue(card()),
  };
}

describe("technique cards", () => {
  it("previews before sending, extracts on the button and opens the card", async () => {
    const api = fakeApi();
    render(
      <TechniqueCardsScreen api={api} onClose={() => undefined} pollMs={5} />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Demo lesson" }));
    expect(
      await screen.findByText(/1 slice\(s\), 5 transcript lines/),
    ).toBeTruthy();
    expect(api.start).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole("button", { name: "Extract card (costs money)" }),
    );
    await waitFor(() => expect(api.card).toHaveBeenCalledWith("c1"));
    expect(api.start).toHaveBeenCalledWith(ITEM, "claude-sonnet-5-5");
    expect(
      await screen.findByText(/Not ready: flagged conditions to decide #2/),
    ).toBeTruthy();
    expect(screen.getByText("Unbacked")).toBeTruthy();
  });

  it("saves an edited number, a dropped condition and an answer", async () => {
    const api = fakeApi();
    vi.mocked(api.cards).mockResolvedValue([card()]);
    render(<TechniqueCardsScreen api={api} onClose={() => undefined} />);
    await waitFor(() =>
      expect(
        screen.getAllByRole("button", { name: "Demo lesson" }),
      ).toHaveLength(2),
    );
    const cards = screen.getAllByRole("button", { name: "Demo lesson" });
    fireEvent.click(cards[1]);
    const rows = await screen.findAllByRole("row");
    const flagged = rows[2];
    fireEvent.click(
      within(flagged).getByRole("button", { name: "Edit numbers" }),
    );
    fireEvent.change(within(flagged).getByLabelText("left.period"), {
      target: { value: "2" },
    });
    fireEvent.change(screen.getByPlaceholderText(/Your answer/), {
      target: { value: "2x" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save decisions" }));
    await waitFor(() => expect(api.resolve).toHaveBeenCalled());
    const [cardId, version, conditions, answers] = vi.mocked(api.resolve).mock
      .calls[0];
    expect([cardId, version]).toEqual(["c1", 1]);
    expect(conditions["1"].action).toBe("edit");
    expect(conditions["1"].condition?.left.period).toBe(2);
    expect(answers).toEqual({ "0": "2x" });
  });

  it("filters the timeline to lines the card left out", async () => {
    const api = fakeApi();
    vi.mocked(api.cards).mockResolvedValue([card()]);
    render(<TechniqueCardsScreen api={api} onClose={() => undefined} />);
    await waitFor(() =>
      expect(
        screen.getAllByRole("button", { name: "Demo lesson" }),
      ).toHaveLength(2),
    );
    fireEvent.click(screen.getAllByRole("button", { name: "Demo lesson" })[1]);
    fireEvent.click(
      await screen.findByLabelText("Only lines no condition cites"),
    );
    expect(screen.queryByText("a fifty period moving average")).toBeNull();
    expect(screen.getByText("The stop sits two ATR below")).toBeTruthy();
  });

  it("asks for a new preview when another model is picked and extracts with it", async () => {
    const api = fakeApi();
    vi.mocked(api.preview).mockImplementation(async (_item, model) => ({
      ...preview,
      model: model ?? "claude-sonnet-5-5",
    }));
    render(<TechniqueCardsScreen api={api} onClose={() => undefined} />);
    fireEvent.click(await screen.findByRole("button", { name: "Demo lesson" }));
    fireEvent.change(
      await screen.findByLabelText("Model for this extraction"),
      {
        target: { value: "google/gemini-4-argon" },
      },
    );
    await waitFor(() =>
      expect(api.preview).toHaveBeenLastCalledWith(
        ITEM,
        "google/gemini-4-argon",
      ),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Extract card (costs money)" }),
    );
    await waitFor(() =>
      expect(api.start).toHaveBeenCalledWith(ITEM, "google/gemini-4-argon"),
    );
  });

  it("labels slots", () => {
    expect(slotsLabel(entry)).toBe("close ↗ crosses above ema(50)");
    expect(slotsLabel(rsi)).toBe("rsi(14) > 50 value");
  });
});
