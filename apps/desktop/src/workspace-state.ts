import type { SessionWindow } from "@siw/contracts/types";

import { parseDrawings, type Drawing } from "./drawings";
import { parseIndicators, type IndicatorInstance } from "./indicators";
import { TIMEFRAMES, type Timeframe } from "./market-api";

export type SessionScope = "regular" | "extended";

/** One chart's own settings. Charts never share or propagate these implicitly. */
export interface ChartConfig {
  instrumentId: string;
  timeframe: Timeframe;
  sessionDate: string;
  sessionScope: SessionScope;
  /** Indicators on this chart only; never shared with the other chart implicitly. */
  indicators?: IndicatorInstance[];
  /** Drawings on this chart; each one names the instrument it was drawn on. */
  drawings?: Drawing[];
}

export interface WorkspaceState {
  version: 1;
  syncTimeRange: boolean;
  charts: [ChartConfig, ChartConfig];
}

export const WORKSPACE_STORAGE_KEY = "siw.workspace.v1";

export const DEFAULT_WORKSPACE: WorkspaceState = {
  version: 1,
  syncTimeRange: true,
  charts: [
    {
      instrumentId: "us:synth:xnas:2026",
      timeframe: "5m",
      sessionDate: "2026-10-09",
      sessionScope: "extended",
    },
    {
      instrumentId: "us:synth:xnas:2026",
      timeframe: "1m",
      sessionDate: "2026-10-09",
      sessionScope: "regular",
    },
  ],
};

const SESSION_DATE = /^\d{4}-\d{2}-\d{2}$/;

function parseChart(value: unknown): ChartConfig | null {
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const chart = value as Record<string, unknown>;
  if (
    typeof chart.instrumentId !== "string" ||
    chart.instrumentId.length === 0 ||
    !TIMEFRAMES.includes(chart.timeframe as Timeframe) ||
    typeof chart.sessionDate !== "string" ||
    !SESSION_DATE.test(chart.sessionDate) ||
    (chart.sessionScope !== "regular" && chart.sessionScope !== "extended")
  ) {
    return null;
  }
  const indicators = parseIndicators(chart.indicators);
  const drawings = parseDrawings(chart.drawings);
  return {
    instrumentId: chart.instrumentId,
    timeframe: chart.timeframe as Timeframe,
    sessionDate: chart.sessionDate,
    sessionScope: chart.sessionScope,
    ...(indicators.length > 0 ? { indicators } : {}),
    ...(drawings.length > 0 ? { drawings } : {}),
  };
}

/** Accept only a complete, known-version layout; anything else falls back to defaults. */
export function parseWorkspace(value: unknown): WorkspaceState | null {
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const state = value as Record<string, unknown>;
  if (
    state.version !== 1 ||
    typeof state.syncTimeRange !== "boolean" ||
    !Array.isArray(state.charts) ||
    state.charts.length !== 2
  ) {
    return null;
  }
  const first = parseChart(state.charts[0]);
  const second = parseChart(state.charts[1]);
  if (first === null || second === null) {
    return null;
  }
  return {
    version: 1,
    syncTimeRange: state.syncTimeRange,
    charts: [first, second],
  };
}

export function loadWorkspace(storage: Storage | null): WorkspaceState {
  try {
    const raw = storage?.getItem(WORKSPACE_STORAGE_KEY);
    return (raw ? parseWorkspace(JSON.parse(raw)) : null) ?? DEFAULT_WORKSPACE;
  } catch {
    return DEFAULT_WORKSPACE;
  }
}

export function saveWorkspace(
  storage: Storage | null,
  state: WorkspaceState,
): void {
  try {
    storage?.setItem(WORKSPACE_STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Storage can be unavailable (private mode, quota); the layout simply is not kept.
  }
}

/** UTC request range for a chart: the calendar's session window, never a client guess. */
export function sessionRange(
  window: SessionWindow,
  scope: SessionScope,
): { startUtc: string; endUtc: string } | null {
  const start =
    scope === "extended" ? window.pre_open_utc : window.regular_open_utc;
  const end =
    scope === "extended" ? window.post_close_utc : window.regular_close_utc;
  return window.is_trading_day && start && end
    ? { startUtc: start, endUtc: end }
    : null;
}
