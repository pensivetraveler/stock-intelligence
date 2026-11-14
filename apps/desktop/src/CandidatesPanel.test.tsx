import type {
  ScreeningCandidates,
  ScreeningCheck,
  ScreeningFlowWatch,
  ScreeningStage2,
} from "@siw/contracts/types";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CandidatesPanel, NirDigest } from "./CandidatesPanel";
import type { ScreeningApi } from "./screening-api";

const listing = {
  instrument: {
    kind: "instrument_ref",
    schema_version: "0.1",
    instrument_id: "us:aapl:xnas:2026",
    symbol: "AAPL",
    venue: "XNAS",
    asset_class: "us_equity",
    currency: "USD",
    identity_as_of: "2026-01-01T00:00:00Z",
  },
  source_id: "test",
  dataset_revision: "test-r1",
  session_dates: [],
} as unknown as NonNullable<ScreeningCandidates["candidates"][0]["listing"]>;

function candidates(
  checks: ScreeningCandidates["candidates"][0]["checks"] = {},
): ScreeningCandidates {
  return {
    kind: "screening_candidates",
    schema_version: "0.1",
    session_date: "2026-11-09",
    cutoff_utc: "2026-11-09T15:30:00.000Z",
    selection_noon_utc: "2026-11-09T17:00:00.000Z",
    condition_name: "SIW_당일급등주",
    recorded: true,
    watch_coverage: {
      watches: 2,
      interrupted: 0,
      first_started_utc: "2026-11-09T09:01:00.000Z",
      last_heard_utc: "2026-11-09T15:30:00.000Z",
      open_now: true,
      gaps: [],
    },
    candidates: [
      {
        symbol: "AAPL",
        sources: ["condition"],
        first_seen_utc: "2026-11-09T09:01:00.000Z",
        in_list: true,
        kiwoom_exchange: "ND",
        first_seen_how: "first_list",
        entered_after_utc: "2026-11-09T09:00:00.000Z",
        enter_count: 0,
        seconds_in_list: 23_340,
        listing,
        checks,
      },
      {
        symbol: "ZZZQ",
        sources: ["condition"],
        first_seen_utc: "2026-11-09T09:05:00.000Z",
        in_list: false,
        kiwoom_exchange: "ND",
        first_seen_how: "enter_event",
        entered_after_utc: null,
        enter_count: 16,
        seconds_in_list: 820,
        listing: null,
        checks: {},
      },
    ],
  };
}

type ApiKey = keyof ScreeningApi;

/** Calls a test does not use: mocks, so the panel never reaches a network. */
function unused(keep: ApiKey[] = []): Partial<ScreeningApi> {
  const keys: ApiKey[] = [
    "runDilutionCheck",
    "refreshCheck",
    "runNewsCheck",
    "refreshNewsCheck",
    "runOffExchangeCheck",
    "runStructureCheck",
    "runMomentumCheck",
  ];
  return Object.fromEntries(
    keys.filter((key) => !keep.includes(key)).map((key) => [key, vi.fn()]),
  );
}

describe("CandidatesPanel", () => {
  it("lists candidates without the retired entry check and opens a chart", async () => {
    const api = {
      // A stored entry check from before ADR 0057 is not shown.
      candidates: vi.fn(() =>
        Promise.resolve(
          candidates({
            entry: {
              check_id: 7,
              status: "passed",
              created_at_utc: "2026-11-09T15:31:00Z",
            },
          }),
        ),
      ),
      addCandidate: vi.fn(() => Promise.resolve(candidates())),
      check: vi.fn(),
      ...unused(),
    } as ScreeningApi;
    const onPick = vi.fn();
    render(<CandidatesPanel api={api} onPick={onPick} />);
    await screen.findByText(/2 condition watches/);
    expect(screen.queryByText("Entry rules")).toBeNull();
    expect(screen.queryByLabelText("Entry rules from")).toBeNull();
    expect(screen.queryByRole("button", { name: "met" })).toBeNull();
    fireEvent.click(
      screen.getByRole("button", { name: "Show AAPL in the active chart" }),
    );
    expect(onPick).toHaveBeenCalledWith(listing);
  });

  it("shows each candidate's stage 3 flow state and both window counts", async () => {
    const flow = {
      kind: "screening_flow_watch",
      schema_version: "0.1",
      session_date: "2026-11-09",
      created_at_utc: "2026-11-09T15:30:30Z",
      after_the_fact: false,
      versions: { check: "flow-watch-1" },
      candidates: [
        {
          symbol: "AAPL",
          instrument_id: "us:aapl:xnas:2026",
          status: "ok",
          result: {
            state: "excluded",
            stage2: "passed",
            new_buys_allowed: false,
            resting_orders: "keep",
            as_of_utc: "2026-11-09T15:30:00Z",
            liquidity: { passing: 3, of: 5 },
            volatility: { passing: 4, of: 20 },
            halts: { available: false },
          },
          warnings: [],
          sources: {},
        },
        {
          symbol: "ZZZQ",
          instrument_id: null,
          status: "unsupported",
          result: { reason: "ZZZQ is not in the app's symbol list" },
          warnings: [],
          sources: {},
        },
      ],
    } as unknown as ScreeningFlowWatch;
    const api = {
      candidates: vi.fn(() => Promise.resolve(candidates())),
      addCandidate: vi.fn(),
      check: vi.fn(),
      flow: vi.fn(() => Promise.resolve(flow)),
      ...unused(),
    } as ScreeningApi;
    render(<CandidatesPanel api={api} onPick={vi.fn()} />);
    const cell = await screen.findByLabelText("AAPL Flow now (stage 3)");
    await waitFor(() => expect(cell.textContent).toBe("Out · 3/5 · 4/20"));
    expect(cell.getAttribute("title")).toMatch(/No halt record/);
    expect(screen.getByLabelText("ZZZQ Flow now (stage 3)").textContent).toBe(
      "Not listed",
    );
  });

  it("shows stage 2's verdict, its basis, and the stage 2 exclusion in stage 3", async () => {
    const verdict = (
      outcome: "passed" | "excluded_today",
      material: ScreeningStage2["material"],
      strategy: ScreeningStage2["strategy"],
      reason: string,
    ): ScreeningStage2 => ({
      label: "재료 영향력(2단계)",
      version: "material-stage2-1",
      rules: "adr-0057-stage2-1",
      outcome,
      material,
      strategy,
      reason,
      conclusions: {
        version: "material-judgement-2",
        material: "present",
        filter: outcome === "excluded_today" ? "yes" : "no",
        rationale: outcome === "excluded_today" ? null : "yes",
      },
      articles: [
        {
          source_id: "alpaca-news",
          record_id: "AAPL:1",
          headline: "AAPL Wins Purchase Order",
          published_at_utc: "2026-11-09T12:00:00Z",
          categories: ["binding_contract"],
          filter: "no",
          filter_basis: "rules",
          filter_reasons: [],
          rationale: "yes",
          rationale_basis: "nir",
          nir_record_id: "NIR-AAPL-1",
          nir_state: "skipped",
          nir_inherited_from: { source_id: "alpaca-news", record_id: "AAPL:0" },
        },
      ],
      news_check_id: 4,
      news_status: "material",
      news_cutoff_utc: "2026-11-09T15:00:00.000Z",
    });
    const document = candidates();
    document.candidates[0].stage2 = verdict(
      "passed",
      "rationale",
      "normal",
      "rally_rationale",
    );
    document.candidates[1].stage2 = verdict(
      "excluded_today",
      "low_impact",
      null,
      "low_impact_material",
    );
    const flow = {
      kind: "screening_flow_watch",
      schema_version: "0.1",
      session_date: "2026-11-09",
      created_at_utc: "2026-11-09T15:30:30Z",
      after_the_fact: false,
      versions: { check: "flow-watch-1" },
      candidates: [
        {
          symbol: "ZZZQ",
          instrument_id: null,
          status: "stage2_excluded",
          result: { stage2: "failed", new_buys_allowed: false },
          warnings: [],
          sources: {},
        },
      ],
    } as unknown as ScreeningFlowWatch;
    const api = {
      candidates: vi.fn(() => Promise.resolve(document)),
      addCandidate: vi.fn(),
      check: vi.fn(),
      flow: vi.fn(() => Promise.resolve(flow)),
      ...unused(),
    } as ScreeningApi;
    render(<CandidatesPanel api={api} onPick={vi.fn()} />);
    const passed = await screen.findByLabelText(
      "AAPL Material (stage 2): Pass · rally reason · normal",
    );
    expect(passed.getAttribute("title")).toMatch(/inference/);
    expect(
      screen.getByLabelText("ZZZQ Material (stage 2): Out for today"),
    ).toBeTruthy();
    const out = screen.getByLabelText("ZZZQ Flow now (stage 3)");
    await waitFor(() => expect(out.textContent).toBe("Out (stage 2)"));
    fireEvent.click(passed);
    const basis = screen.getByRole("region", { name: "AAPL stage 2 basis" });
    expect(basis.textContent).toMatch(/AAPL Wins Purchase Order/);
    expect(basis.textContent).toMatch(/no · rules/);
    expect(basis.textContent).toMatch(/yes · nir · twin article's LLM answer/);
  });

  it("says when there is no condition record and adds a ticker", async () => {
    const empty = { ...candidates(), recorded: false, candidates: [] };
    const api = {
      candidates: vi.fn(() => Promise.resolve(empty)),
      addCandidate: vi.fn(() => Promise.resolve(candidates())),
      check: vi.fn(),
      ...unused(),
    } as ScreeningApi;
    render(<CandidatesPanel api={api} onPick={vi.fn()} />);
    await screen.findByText(/no condition record/);
    fireEvent.change(screen.getByLabelText("Add a ticker"), {
      target: { value: "aapl" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() =>
      expect(api.addCandidate).toHaveBeenCalledWith("AAPL", "2026-11-09"),
    );
    await screen.findByText("ZZZQ");
  });

  it("asks for rules-only dilution and keeps unknown prices unknown", async () => {
    const supply = {
      kind: "screening_check",
      check_id: 9,
      check: "dilution",
      symbol: "AAPL",
      status: "incomplete",
      result: {
        llm: false,
        incomplete_reasons: ["llm_disabled"],
        securities: [
          {
            id: "S1",
            kind: "warrant",
            description: "Series A Warrant",
            price: {
              amount: "1.25",
              unit: "usd_per_share",
              status: "confirmed",
              fact: true,
            },
            price_range: null,
            floor: null,
            outstanding: {
              amount: null,
              unit: "shares",
              status: "unverified",
              fact: false,
            },
            conditions: [],
            after_base_point: false,
            approval_unknown: true,
          },
        ],
        capacity: [],
      },
      warnings: [],
    } as unknown as ScreeningCheck;
    let done = false;
    const api = {
      candidates: vi.fn(() => {
        const found = candidates();
        if (done)
          found.candidates[0].checks.dilution = {
            check_id: 9,
            status: "incomplete",
            created_at_utc: "2026-11-09T15:31:00Z",
          };
        return Promise.resolve(found);
      }),
      addCandidate: vi.fn(),
      check: vi.fn(() => Promise.resolve(supply)),
      runDilutionCheck: vi.fn(() => {
        done = true;
        return Promise.resolve(supply);
      }),
      ...unused(["runDilutionCheck"]),
    } as ScreeningApi;
    render(<CandidatesPanel api={api} onPick={vi.fn()} />);
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Dilution for all (rules only)",
      }),
    );
    await waitFor(() =>
      expect(api.runDilutionCheck).toHaveBeenCalledWith(
        "AAPL",
        false,
        "2026-11-09",
      ),
    );
    expect(api.runDilutionCheck).toHaveBeenCalledTimes(1);
    fireEvent.click(
      await screen.findByRole("button", {
        name: "AAPL Dilution supply: partial",
      }),
    );
    await screen.findByText(/totals are unknown, not zero/);
    expect(screen.getByText("?")).toBeTruthy();
    expect(
      screen.getByText(/may not convert before shareholder approval/),
    ).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "Analyze with LLM (costs)" }),
    );
    await waitFor(() =>
      expect(api.runDilutionCheck).toHaveBeenLastCalledWith(
        "AAPL",
        true,
        "2026-11-09",
      ),
    );
  });

  it("says no securities from rules alone is unknown, not none", async () => {
    const none = {
      kind: "screening_check",
      check_id: 11,
      check: "dilution",
      symbol: "AAPL",
      instrument_id: "us:aapl:xnas:2026",
      status: "incomplete",
      result: {
        llm: false,
        incomplete_reasons: ["llm_disabled"],
        securities: [],
        capacity: [],
      },
      warnings: [],
    } as unknown as ScreeningCheck;
    const api = {
      candidates: vi.fn(() => {
        const found = candidates();
        found.candidates[0].checks.dilution = {
          check_id: 11,
          status: "incomplete",
          created_at_utc: "2026-11-09T15:31:00Z",
        };
        return Promise.resolve(found);
      }),
      addCandidate: vi.fn(),
      check: vi.fn(() => Promise.resolve(none)),
      ...unused(),
    } as ScreeningApi;
    render(<CandidatesPanel api={api} onPick={vi.fn()} />);
    fireEvent.click(
      await screen.findByRole("button", {
        name: "AAPL Dilution supply: partial",
      }),
    );
    await screen.findByText(/that means unknown, not none/);
  });

  it("runs news, off-exchange and structure checks and draws levels", async () => {
    const offExchange = {
      kind: "screening_check",
      check_id: 21,
      check: "off_exchange",
      symbol: "AAPL",
      instrument_id: "us:aapl:xnas:2026",
      status: "ok",
      result: {
        sessions_requested: 5,
        sessions: [
          { session_date: "2026-11-06", partial: false, truncated: true },
        ],
        reference_price: "101.5",
        off_exchange_share_pct: "41.2",
        levels: [
          {
            price_low: "100.00",
            price_high: "100.50",
            volume: "1000",
            share_pct: "12.5",
            side: "below",
          },
        ],
        notes: [],
      },
      warnings: [],
    } as unknown as ScreeningCheck;
    const structure = {
      kind: "screening_check",
      check_id: 22,
      check: "off_exchange_structure",
      symbol: "AAPL",
      instrument_id: "us:aapl:xnas:2026",
      status: "pending",
      result: {
        progress: {
          total_sessions: 20,
          cached_sessions: 4,
          reading_sessions: 16,
          missing: [],
        },
        limitations: [],
        unverified: [],
      },
      warnings: [],
    } as unknown as ScreeningCheck;
    const news = {
      kind: "screening_check",
      check_id: 23,
      check: "news",
      symbol: "AAPL",
      instrument_id: "us:aapl:xnas:2026",
      status: "material",
      result: {
        lookback_hours: 72,
        article_count: 2,
        categories: { binding_contract: 1 },
        out_of_scope: { dilution: { articles: 1 } },
        feeds: [],
        latest_categorized: null,
      },
      warnings: [],
    } as unknown as ScreeningCheck;
    let ran = false;
    const api = {
      candidates: vi.fn(() => {
        const found = candidates();
        if (ran)
          found.candidates[0].checks = {
            news: { check_id: 23, status: "material", created_at_utc: "x" },
            off_exchange: { check_id: 21, status: "ok", created_at_utc: "x" },
            off_exchange_structure: {
              check_id: 22,
              status: "pending",
              created_at_utc: "x",
            },
          };
        return Promise.resolve(found);
      }),
      addCandidate: vi.fn(),
      check: vi.fn((id: number) =>
        Promise.resolve(id === 21 ? offExchange : news),
      ),
      runNewsCheck: vi.fn(() => Promise.resolve(news)),
      runOffExchangeCheck: vi.fn(() => Promise.resolve(offExchange)),
      runStructureCheck: vi.fn(() => {
        ran = true;
        return Promise.resolve(structure);
      }),
      ...unused(["runNewsCheck", "runOffExchangeCheck", "runStructureCheck"]),
    } as ScreeningApi;
    const onShowLevels = vi.fn();
    const onPick = vi.fn();
    render(
      <CandidatesPanel api={api} onPick={onPick} onShowLevels={onShowLevels} />,
    );
    fireEvent.click(
      await screen.findByRole("button", {
        name: "News, off-exchange and structure for all",
      }),
    );
    await waitFor(() => expect(api.runStructureCheck).toHaveBeenCalled());
    expect(api.runNewsCheck).toHaveBeenCalledWith("AAPL", "2026-11-09");
    expect(api.runOffExchangeCheck).toHaveBeenCalledWith(
      "AAPL",
      "2026-11-09",
      20,
    );

    fireEvent.click(
      await screen.findByRole("button", { name: "AAPL News: catalyst" }),
    );
    await screen.findByText(/Binding contract: 1/);
    await screen.findByText(/1 articles are about dilution/);
    // A result made before the LLM step: the card says it is rules only.
    await screen.findByText(/Rules only: no LLM reading for this check/);

    fireEvent.click(
      screen.getByRole("button", { name: "AAPL Off-exchange: done" }),
    );
    await screen.findByText(/1 sessions were read only in part/);
    fireEvent.click(
      screen.getByRole("button", { name: "Show 2 lines on the chart" }),
    );
    expect(onShowLevels).toHaveBeenCalledWith(
      expect.objectContaining({
        instrumentId: "us:aapl:xnas:2026",
        levels: [
          expect.objectContaining({ price: 100, title: "12.5%" }),
          expect.objectContaining({ price: 100.5 }),
        ],
      }),
    );
    expect(onPick).toHaveBeenCalledWith(listing);

    // A waiting structure check is asked again by posting, which shows progress.
    fireEvent.click(
      screen.getByRole("button", { name: "AAPL Price structure: waiting" }),
    );
    await screen.findByText(/4\/20 sessions read/);
    expect(api.runStructureCheck).toHaveBeenCalledTimes(2);
  });
  it("asks a waiting off-exchange check again with the sessions chosen", async () => {
    const waiting = {
      kind: "screening_check",
      check_id: 31,
      check: "off_exchange",
      symbol: "AAPL",
      instrument_id: "us:aapl:xnas:2026",
      status: "pending",
      result: {
        sessions_requested: 10,
        sessions: [
          {
            session_date: "2026-11-09",
            source: "live",
            partial: true,
            truncated: false,
          },
          {
            session_date: "2026-11-06",
            source: "cache",
            partial: false,
            truncated: false,
          },
        ],
        progress: {
          total_sessions: 10,
          cached_sessions: 1,
          reading_sessions: 9,
          missing: [],
        },
        reference_price: "101.5",
        off_exchange_share_pct: "41.2",
        levels: [],
        notes: [],
      },
      warnings: [],
    } as unknown as ScreeningCheck;
    const api = {
      candidates: vi.fn(() => {
        const found = candidates();
        found.candidates[0].checks = {
          off_exchange: {
            check_id: 31,
            status: "pending",
            created_at_utc: "x",
          },
        };
        return Promise.resolve(found);
      }),
      addCandidate: vi.fn(),
      check: vi.fn(() => Promise.resolve(waiting)),
      runOffExchangeCheck: vi.fn(() => Promise.resolve(waiting)),
      ...unused(["runOffExchangeCheck"]),
    } as ScreeningApi;
    render(<CandidatesPanel api={api} onPick={vi.fn()} />);
    fireEvent.change(await screen.findByLabelText("Off-exchange sessions"), {
      target: { value: "10" },
    });
    fireEvent.click(
      await screen.findByRole("button", { name: "AAPL Off-exchange: waiting" }),
    );
    await screen.findByText(/1\/10 sessions read/);
    await screen.findByText(/closed sessions 1\/10 \+ today up to the cutoff/);
    expect(api.runOffExchangeCheck).toHaveBeenCalledWith(
      "AAPL",
      "2026-11-09",
      10,
    );
  });
  it("shows record gaps, unknown entry times, the noon cut and values at entry", async () => {
    let measured = false;
    const momentum = {
      kind: "screening_check",
      check_id: 9,
      check: "momentum",
      symbol: "AAPL",
      status: "ok",
      strategy: null,
      result: {
        first_seen_utc: "2026-11-09T09:01:00.000Z",
        entry_time_known: false,
        bars_until_utc: "2026-11-09T09:01:00.000Z",
        price: {
          value: "5",
          bar_end_utc: "2026-11-09T09:01:00Z",
          session: "pre",
        },
        previous_close: { value: "4", session_date: "2026-11-06" },
        change_percent: "25.00",
        volume: "2000",
        dollar_volume_estimate: "1250000.00",
        missing_minutes: {},
        missing: [],
      },
      sources: { bars: { source_feed_scope: "alpaca-sip" } },
      warnings: ["Rebuilt from bars fetched when the check ran."],
    } as unknown as ScreeningCheck;
    const document = () => {
      const found = candidates();
      found.watch_coverage.gaps = [
        {
          from_utc: "2026-11-09T13:40:47.146Z",
          to_utc: "2026-11-09T15:30:00.000Z",
          reason: "not_recording",
          previous_end: "stopped",
        },
      ];
      found.candidates[1].first_seen_utc = "2026-11-09T17:05:00.000Z";
      if (measured)
        found.candidates[0].checks = {
          momentum: {
            check_id: 9,
            status: "ok",
            created_at_utc: "2026-11-09T15:31:00Z",
            summary: {
              price: "5",
              change_percent: "25.00",
              dollar_volume_estimate: "1250000.00",
              entry_time_known: false,
            },
          },
        };
      return found;
    };
    const api = {
      candidates: vi.fn(() => Promise.resolve(document())),
      addCandidate: vi.fn(),
      check: vi.fn(() => Promise.resolve(momentum)),
      ...unused(["runMomentumCheck"]),
      runMomentumCheck: vi.fn(() => {
        measured = true;
        return Promise.resolve(momentum);
      }),
    } as ScreeningApi;
    render(<CandidatesPanel api={api} onPick={vi.fn()} />);
    expect(
      await screen.findByText(/08:40–10:30 not recording \(stopped\)/),
    ).toBeTruthy();
    // AAPL was in the opening list: only a range, not an entry time.
    expect(screen.getByText("04:00–04:01?")).toBeTruthy();
    expect(screen.getByText("16× · 14 min")).toBeTruthy();
    expect(screen.getByText("Entered after noon (New York)")).toBeTruthy();

    fireEvent.click(
      screen.getByRole("button", { name: "Values at entry for all" }),
    );
    await waitFor(() =>
      expect(api.runMomentumCheck).toHaveBeenCalledWith("AAPL", "2026-11-09"),
    );
    // ZZZQ is not chartable, so it is not measured.
    expect(api.runMomentumCheck).toHaveBeenCalledTimes(1);
    fireEvent.click(
      await screen.findByRole("button", { name: "AAPL At entry (Alpaca)" }),
    );
    expect(await screen.findByText(/The entry time is not known/)).toBeTruthy();
    expect(screen.getByText("$1.3M")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("Only up to noon (12:00 New York)"));
    await waitFor(() =>
      expect(api.candidates).toHaveBeenLastCalledWith(
        undefined,
        "2026-11-09T17:00:00.000Z",
      ),
    );
  });
});

describe("NirDigest", () => {
  const base = { categories: { binding_contract: 1 } };
  it("marks the LLM reading as inference and flags failed checks", () => {
    render(
      <NirDigest
        result={{
          ...base,
          nir: {
            label: "INFERENCE",
            state: "done",
            reason: null,
            rules_only: false,
            model: "openai/gpt-6-luna",
            items: [
              {
                nir_record_id: "NIR-ABCD-20261109-mna-1-001",
                headline: "ABCD Receives $2M Purchase Order",
                event_type: "Purchase Order",
                legally_binding: "Yes",
                committed_amount: "$2 million",
                potential_amount: "UNKNOWN",
                economic_substance: "Moderate",
                promotion_risk: "Low",
                confidence: "Medium",
                summary_ko: "Invented summary",
                verification_failures: 1,
                rule_conflicts: [],
              },
            ],
          },
        }}
      />,
    );
    expect(screen.getByRole("note")).toHaveTextContent(/Inference by an LLM/);
    expect(screen.getByText(/Purchase Order · binding Yes/)).toHaveTextContent(
      /committed \$2 million/,
    );
    expect(screen.getByText(/not found in the article: 1/)).toBeInTheDocument();
  });
  it("says rules only with the reason, and nothing when no article was tagged", () => {
    const stopped = {
      label: "INFERENCE" as const,
      state: "stopped",
      reason: "monthly_cap_reached",
      rules_only: true,
      model: null,
      items: [],
    };
    const { unmount } = render(
      <NirDigest result={{ ...base, nir: stopped }} />,
    );
    expect(screen.getByRole("note")).toHaveTextContent(
      "Rules only: no LLM reading (monthly_cap_reached).",
    );
    unmount();
    const { container } = render(
      <NirDigest
        result={{ categories: {}, nir: { ...stopped, state: "not_needed" } }}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
