import type {
  BacktestFill,
  BrokerOrderExecution,
  ImportedExecution,
  ManualExecution,
  PriceBar,
  ScenarioDefinition,
} from "@siw/contracts/types";

import { exchangeTime } from "./chart-data";
import { zoneOf } from "./market-zone";

export interface ChartMarker {
  time: number;
  position: "aboveBar" | "belowBar";
  shape: "arrowUp" | "arrowDown" | "circle" | "square";
  color: string;
  text: string;
  id: string;
  /** One line per record behind the marker; several once a bar's fills are grouped. */
  details?: string[];
  /** Exact time of the record (ms), when known; orders a group's details. */
  at?: number;
  /** What groups it: ledger and side ("B buy"), quantity, and a note kept apart. */
  group?: { label: string; quantity: string | null; note: string };
}

/** Distinct shapes and colours per ledger, so actual and hypothetical never look alike. */
export const MARKER_COLORS = {
  manual: "#4c8dff",
  imported: "#2ec4b6",
  broker: "#f5a623",
  hypothetical: "#c08bff",
  backtest: "#ff6f91",
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

/** A buy/sell marker of one ledger; `detail` says what the record claims. */
function fillMarker(options: {
  id: string;
  time: number;
  side: "buy" | "sell";
  color: string;
  prefix: string;
  quantity: string;
  note?: string;
  detail: string;
  utc?: string | null;
}): ChartMarker {
  const buy = options.side === "buy";
  const label = `${options.prefix} ${options.side}`;
  const note = options.note ?? "";
  return {
    id: options.id,
    time: options.time,
    position: buy ? "belowBar" : "aboveBar",
    shape: buy ? "arrowUp" : "arrowDown",
    color: options.color,
    text: `${label} ${options.quantity}${note ? ` ${note}` : ""}`,
    details: [options.detail],
    ...(options.utc ? { at: Date.parse(options.utc) } : {}),
    group: { label, quantity: options.quantity, note },
  };
}

function describe(
  label: string,
  quantity: string,
  price: string | null | undefined,
  utc: string | null | undefined,
  instrumentId: string,
): string {
  const at = utc ? ` · ${exchangeTime(utc, zoneOf(instrumentId))}` : "";
  return `${label} ${quantity}${price ? ` @ ${price}` : ""}${at}`;
}

export function journalMarkers(
  bars: PriceBar[],
  instrumentId: string,
  manual: ManualExecution[],
  scenarios: ScenarioDefinition[],
  imported: ImportedExecution[] = [],
  broker: BrokerOrderExecution[] = [],
): ChartMarker[] {
  const markers: ChartMarker[] = [];
  // Broker API fills: one per order (total quantity at the average price), at the
  // time the broker reported; an order without a reported time is not drawn.
  for (const execution of broker) {
    if (
      execution.instrument_id !== instrumentId ||
      !execution.executed_at_utc
    ) {
      continue;
    }
    const time = barStart(bars, execution.executed_at_utc);
    if (time === null) continue;
    markers.push(
      fillMarker({
        id: `broker:${execution.execution_id}`,
        time,
        side: execution.side,
        color: MARKER_COLORS.broker,
        prefix: "B",
        quantity: execution.quantity,
        utc: execution.executed_at_utc,
        detail: describe(
          `B ${execution.side}`,
          execution.quantity,
          execution.price,
          execution.executed_at_utc,
          instrumentId,
        ),
      }),
    );
  }
  for (const execution of imported) {
    if (execution.instrument_id !== instrumentId) continue;
    const time = barStart(bars, execution.executed_at_utc);
    if (time === null) continue;
    markers.push(
      fillMarker({
        id: `imported:${execution.execution_id}`,
        time,
        side: execution.side,
        color: MARKER_COLORS.imported,
        prefix: "I",
        quantity: execution.quantity,
        utc: execution.executed_at_utc,
        detail: describe(
          `I ${execution.side}`,
          execution.quantity,
          execution.price,
          execution.executed_at_utc,
          instrumentId,
        ),
      }),
    );
  }
  for (const execution of manual) {
    if (execution.instrument_id !== instrumentId) continue;
    // A claimed time places the marker; with an unknown time, the clicked candle does.
    const time = barStart(
      bars,
      execution.executed_at_utc ?? execution.selected_interval_start_utc,
    );
    if (time === null) continue;
    const note = execution.executed_at_utc ? "" : "(time unknown)";
    markers.push(
      fillMarker({
        id: `manual:${execution.execution_id}`,
        time,
        side: execution.side,
        color: MARKER_COLORS.manual,
        prefix: "M",
        quantity: execution.quantity,
        utc: execution.executed_at_utc,
        note,
        detail:
          describe(
            `M ${execution.side}`,
            execution.quantity,
            execution.price,
            execution.executed_at_utc,
            instrumentId,
          ) + (note ? ` ${note}` : ""),
      }),
    );
  }
  for (const scenario of scenarios) {
    if (scenario.instrument_id !== instrumentId) continue;
    for (const [leg, utc] of [
      ["entry", scenario.entry_interval_start_utc],
      ["exit", scenario.exit_interval_start_utc],
    ] as const) {
      const time = barStart(bars, utc);
      if (time === null) continue;
      const text = `H ${scenario.direction} ${leg}`;
      markers.push({
        id: `hypothetical:${scenario.scenario_id}:${leg}`,
        time,
        position: "aboveBar",
        shape: leg === "entry" ? "circle" : "square",
        color: MARKER_COLORS.hypothetical,
        text,
        details: [`${text} ${scenario.quantity}`],
        group: { label: text, quantity: null, note: "" },
      });
    }
  }
  return markers.sort((a, b) => a.time - b.time || a.id.localeCompare(b.id));
}

/** Fills of one backtest run, shown on request; they are never journal entries. */
export function backtestMarkers(
  bars: PriceBar[],
  instrumentId: string,
  fills: BacktestFill[],
): ChartMarker[] {
  const markers: ChartMarker[] = [];
  for (const fill of fills) {
    if (fill.instrument_id !== instrumentId) continue;
    const time = barStart(bars, fill.filled_at_utc);
    if (time === null) continue;
    markers.push(
      fillMarker({
        id: `backtest:${fill.fill_id}`,
        time,
        side: fill.side,
        color: MARKER_COLORS.backtest,
        prefix: "BT",
        quantity: fill.quantity,
        utc: fill.filled_at_utc,
        detail: describe(
          `BT ${fill.side}`,
          fill.quantity,
          fill.price,
          fill.filled_at_utc,
          instrumentId,
        ),
      }),
    );
  }
  return markers;
}

/** Sum of decimal strings without float rounding; null if one is not a plain decimal. */
export function sumDecimals(values: string[]): string | null {
  let scale = 0;
  for (const value of values) {
    const match = /^-?\d+(?:\.(\d+))?$/.exec(value.trim());
    if (!match) return null;
    scale = Math.max(scale, match[1]?.length ?? 0);
  }
  let total = 0n;
  for (const raw of values) {
    const value = raw.trim();
    const [whole, fraction = ""] = value.replace("-", "").split(".");
    const units = BigInt(whole + fraction.padEnd(scale, "0"));
    total += value.startsWith("-") ? -units : units;
  }
  const negative = total < 0n;
  const digits = (negative ? -total : total)
    .toString()
    .padStart(scale + 1, "0");
  const whole = digits.slice(0, digits.length - scale);
  const fraction = scale
    ? digits.slice(digits.length - scale).replace(/0+$/, "")
    : "";
  return `${negative ? "-" : ""}${whole}${fraction ? `.${fraction}` : ""}`;
}

/**
 * Collapses the markers of one ledger and side on one bar into a single marker, so a
 * bar with dozens of fills stays readable: "B buy ×12 (340)" carries the count and the
 * total quantity, and every record stays listed in `details`. Ledgers never merge.
 */
export function groupMarkers(markers: ChartMarker[]): ChartMarker[] {
  const groups = new Map<string, ChartMarker[]>();
  for (const marker of markers) {
    const group = marker.group ?? { label: marker.text, note: "" };
    const key = [
      marker.time,
      marker.color,
      marker.shape,
      group.label,
      group.note,
    ].join("|");
    const members = groups.get(key);
    if (members) members.push(marker);
    else groups.set(key, [marker]);
  }
  const grouped: ChartMarker[] = [];
  for (const members of groups.values()) {
    // Details in the order the records happened; records without a time last.
    members.sort(
      (a, b) =>
        (a.at ?? Number.POSITIVE_INFINITY) -
          (b.at ?? Number.POSITIVE_INFINITY) || a.id.localeCompare(b.id),
    );
    const first = members[0];
    const details = members.flatMap(
      (marker) => marker.details ?? [marker.text],
    );
    if (members.length === 1) {
      grouped.push({ ...first, details });
      continue;
    }
    const label = first.group?.label ?? first.text;
    const note = first.group?.note ?? "";
    const quantities = members.map((marker) => marker.group?.quantity ?? null);
    const total = quantities.every((value) => value !== null)
      ? sumDecimals(quantities as string[])
      : null;
    grouped.push({
      ...first,
      id: `${first.id}+${members.length - 1}`,
      text: `${label} ×${members.length}${total !== null ? ` (${total})` : ""}${note ? ` ${note}` : ""}`,
      details,
    });
  }
  return grouped.sort((a, b) => a.time - b.time || a.id.localeCompare(b.id));
}

/**
 * Drops the label (not the marker) of a marker whose label would sit within `gapBars`
 * bars of another label on the same side of the candles. The biggest groups keep
 * theirs; every marker's full text stays in the bar's legend and candle detail.
 */
export function thinLabels(
  markers: ChartMarker[],
  barSeconds: number,
  gapBars = 4,
): ChartMarker[] {
  const gap = gapBars * barSeconds;
  const labelled: ChartMarker[] = [];
  const keep = new Set<string>();
  const byWeight = [...markers].sort(
    (a, b) =>
      (b.details?.length ?? 1) - (a.details?.length ?? 1) ||
      a.time - b.time ||
      a.id.localeCompare(b.id),
  );
  for (const marker of byWeight) {
    const crowded = labelled.some(
      (other) =>
        other.position === marker.position &&
        Math.abs(other.time - marker.time) < gap,
    );
    if (crowded) continue;
    labelled.push(marker);
    keep.add(marker.id);
  }
  return markers.map((marker) =>
    keep.has(marker.id) ? marker : { ...marker, text: "" },
  );
}
