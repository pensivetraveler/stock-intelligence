import type {
  BacktestStrategy,
  ScreeningCandidates,
  ScreeningCheck,
} from "@siw/contracts/types";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CandidatesPanel } from "./CandidatesPanel";
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
  entry?: ScreeningCandidates["candidates"][0]["checks"]["entry"],
): ScreeningCandidates {
  return {
    kind: "screening_candidates",
    schema_version: "0.1",
    session_date: "2026-11-09",
    cutoff_utc: "2026-11-09T15:30:00.000Z",
    condition_name: "SIW_당일급등주",
    recorded: true,
    watch_coverage: {
      watches: 2,
      interrupted: 0,
      first_started_utc: "2026-11-09T09:01:00.000Z",
      last_heard_utc: "2026-11-09T15:30:00.000Z",
      open_now: true,
    },
    candidates: [
      {
        symbol: "AAPL",
        sources: ["condition"],
        first_seen_utc: "2026-11-09T09:01:00.000Z",
        in_list: true,
        kiwoom_exchange: "ND",
        listing,
        checks: entry ? { entry } : {},
      },
      {
        symbol: "ZZZQ",
        sources: ["condition"],
        first_seen_utc: "2026-11-09T09:05:00.000Z",
        in_list: false,
        kiwoom_exchange: "ND",
        listing: null,
        checks: {},
      },
    ],
  };
}

const strategy = {
  kind: "backtest_strategy",
  strategy_id: "strategy-1",
  name: "Breakout",
  version: 2,
  strategy_type: "rules",
} as unknown as BacktestStrategy;

const check = {
  kind: "screening_check",
  check_id: 7,
  check: "entry",
  symbol: "AAPL",
  status: "passed",
  strategy: {
    strategy_id: "strategy-1",
    name: "Breakout",
    version: 2,
    definition_sha256: "0".repeat(64),
  },
  result: {
    bar: { session_date: "2026-11-06", close: "101.5" },
    conditions: [
      {
        path: "entry",
        op: "gt",
        left_value: "101.5",
        right_value: "100",
        holds: true,
      },
    ],
  },
  warnings: [],
} as unknown as ScreeningCheck;

describe("CandidatesPanel", () => {
  it("checks entry rules for chartable candidates and shows why", async () => {
    let checked = false;
    const api: ScreeningApi = {
      candidates: vi.fn(() =>
        Promise.resolve(
          candidates(
            checked
              ? {
                  check_id: 7,
                  status: "passed",
                  created_at_utc: "2026-11-09T15:31:00Z",
                }
              : undefined,
          ),
        ),
      ),
      addCandidate: vi.fn(() => Promise.resolve(candidates())),
      runEntryCheck: vi.fn(() => {
        checked = true;
        return Promise.resolve(check);
      }),
      check: vi.fn(() => Promise.resolve(check)),
      runDilutionCheck: vi.fn(),
      refreshCheck: vi.fn(),
    };
    const onPick = vi.fn();
    render(
      <CandidatesPanel
        api={api}
        strategies={() => Promise.resolve([strategy])}
        onPick={onPick}
      />,
    );
    await screen.findByText(/2 condition watches/);
    fireEvent.change(await screen.findByLabelText("Entry rules from"), {
      target: { value: "strategy-1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Check all" }));
    await waitFor(() =>
      expect(api.runEntryCheck).toHaveBeenCalledWith(
        "AAPL",
        "strategy-1",
        "2026-11-09",
      ),
    );
    // The ticker the app does not know is neither checked nor chartable.
    expect(api.runEntryCheck).toHaveBeenCalledTimes(1);
    fireEvent.click(await screen.findByRole("button", { name: "met" }));
    expect(
      await screen.findByText(/last closed daily bar 2026-11-06/),
    ).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "Show AAPL in the active chart" }),
    );
    expect(onPick).toHaveBeenCalledWith(listing);
  });

  it("says when there is no condition record and adds a ticker", async () => {
    const empty = { ...candidates(), recorded: false, candidates: [] };
    const api: ScreeningApi = {
      candidates: vi.fn(() => Promise.resolve(empty)),
      addCandidate: vi.fn(() => Promise.resolve(candidates())),
      runEntryCheck: vi.fn(),
      check: vi.fn(),
      runDilutionCheck: vi.fn(),
      refreshCheck: vi.fn(),
    };
    render(
      <CandidatesPanel
        api={api}
        strategies={() => Promise.resolve([])}
        onPick={vi.fn()}
      />,
    );
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
    const api: ScreeningApi = {
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
      runEntryCheck: vi.fn(),
      check: vi.fn(() => Promise.resolve(supply)),
      runDilutionCheck: vi.fn(() => {
        done = true;
        return Promise.resolve(supply);
      }),
      refreshCheck: vi.fn(),
    };
    render(
      <CandidatesPanel
        api={api}
        strategies={() => Promise.resolve([])}
        onPick={vi.fn()}
      />,
    );
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
    fireEvent.click(await screen.findByRole("button", { name: "partial" }));
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
});
