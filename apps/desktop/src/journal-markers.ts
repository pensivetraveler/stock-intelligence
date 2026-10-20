import type {
  ImportedExecution,
  ManualExecution,
  PriceBar,
  ScenarioDefinition,
} from "@siw/contracts/types";

export interface ChartMarker {
  time: number;
  position: "aboveBar" | "belowBar";
  shape: "arrowUp" | "arrowDown" | "circle" | "square";
  color: string;
  text: string;
  id: string;
}

/** Distinct shapes and colours per ledger, so actual and hypothetical never look alike. */
export const MARKER_COLORS = {
  manual: "#4c8dff",
  imported: "#2ec4b6",
  broker: "#f5a623",
  hypothetical: "#c08bff",
};

function seconds(utc: string): number {
  return Math.floor(Date.parse(utc) / 1000);
}

/** Start time of the chart bar whose interval contains `utc`, if one is drawn. */
function barStart(bars: PriceBar[], utc: string): number | null {
  const at = Date.parse(utc);
  const bar = bars.find(
    (item) =>
      Date.parse(item.interval_start_utc) <= at &&
      at < Date.parse(item.interval_end_utc),
  );
  return bar ? seconds(bar.interval_start_utc) : null;
}

export function journalMarkers(
  bars: PriceBar[],
  instrumentId: string,
  manual: ManualExecution[],
  scenarios: ScenarioDefinition[],
  imported: ImportedExecution[] = [],
): ChartMarker[] {
  const markers: ChartMarker[] = [];
  for (const execution of imported) {
    if (execution.instrument_id !== instrumentId) continue;
    const time = barStart(bars, execution.executed_at_utc);
    if (time === null) continue;
    const buy = execution.side === "buy";
    markers.push({
      id: `imported:${execution.execution_id}`,
      time,
      position: buy ? "belowBar" : "aboveBar",
      shape: buy ? "arrowUp" : "arrowDown",
      color: MARKER_COLORS.imported,
      text: `I ${execution.side} ${execution.quantity}`,
    });
  }
  for (const execution of manual) {
    if (execution.instrument_id !== instrumentId) continue;
    // A claimed time places the marker; with an unknown time, the clicked candle does.
    const time = barStart(
      bars,
      execution.executed_at_utc ?? execution.selected_interval_start_utc,
    );
    if (time === null) continue;
    const buy = execution.side === "buy";
    markers.push({
      id: `manual:${execution.execution_id}`,
      time,
      position: buy ? "belowBar" : "aboveBar",
      shape: buy ? "arrowUp" : "arrowDown",
      color: MARKER_COLORS.manual,
      text: `M ${execution.side} ${execution.quantity}${execution.executed_at_utc ? "" : " (time unknown)"}`,
    });
  }
  for (const scenario of scenarios) {
    if (scenario.instrument_id !== instrumentId) continue;
    for (const [leg, utc] of [
      ["entry", scenario.entry_interval_start_utc],
      ["exit", scenario.exit_interval_start_utc],
    ] as const) {
      const time = barStart(bars, utc);
      if (time === null) continue;
      markers.push({
        id: `hypothetical:${scenario.scenario_id}:${leg}`,
        time,
        position: "aboveBar",
        shape: leg === "entry" ? "circle" : "square",
        color: MARKER_COLORS.hypothetical,
        text: `H ${scenario.direction} ${leg}`,
      });
    }
  }
  return markers.sort((a, b) => a.time - b.time || a.id.localeCompare(b.id));
}
