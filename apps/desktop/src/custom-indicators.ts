import type { CustomIndicator } from "@siw/contracts/types";

import {
  INDICATOR_BY_ID,
  type IndicatorDefinition,
  type OhlcvBar,
  type Series,
} from "./indicators";

/**
 * Python indicators on the charts (ADR 0023). Each saved version becomes an indicator
 * definition `py:<id>@<version>`; its values are computed by the API and kept here by
 * the exact bars and settings they were computed for, so the chart draws them through
 * the same path as built-in indicators.
 */
export const isCustomType = (type: string) => type.startsWith("py:");

const PALETTE = [
  "#5b8def",
  "#f5a623",
  "#c08bff",
  "#2ec4b6",
  "#ff6f91",
  "#e6c547",
];
const results = new Map<string, Record<string, Series>>();
const registered = new Map<string, CustomIndicator>();

/** Identifies one computation: the version, the settings and the bars sent. */
export function signature(
  type: string,
  params: Record<string, number>,
  bars: OhlcvBar[],
): string {
  const last = bars.at(-1);
  return [
    type,
    JSON.stringify(params),
    bars.length,
    bars[0]?.time ?? "",
    last?.time ?? "",
    last?.close ?? "",
    last?.volume ?? "",
  ].join("|");
}

export function storeResult(key: string, values: Record<string, Series>): void {
  results.set(key, values);
  if (results.size > 200) {
    const oldest = results.keys().next().value;
    if (oldest !== undefined) results.delete(oldest);
  }
}

export const hasResult = (key: string) => results.has(key);

export function definitionOf(item: CustomIndicator): IndicatorDefinition {
  return {
    id: item.type,
    name: {
      en: `${item.name} (Python v${item.version})`,
      ko: `${item.name} (Python v${item.version})`,
    },
    short: item.name.slice(0, 12),
    category: "custom",
    pane: item.pane,
    inputs: item.inputs.map((input) => ({
      key: input.key,
      label: { en: input.key, ko: input.key },
      default: input.default,
      min: -1e9,
      max: 1e9,
      // Whole-number settings count as look-backs for warm-up, like the built-ins.
      ...(Number.isInteger(input.default) ? {} : { step: 0.01 }),
    })),
    outputs: item.outputs.map((key, index) => ({
      key,
      label: key,
      color: PALETTE[index % PALETTE.length],
    })),
    compute: (bars, params) =>
      results.get(signature(item.type, params, bars)) ??
      Object.fromEntries(
        item.outputs.map((key) => [key, bars.map(() => null) as Series]),
      ),
  };
}

/** Makes saved versions known to the charts and the indicator list. */
export function registerCustom(items: CustomIndicator[]): void {
  for (const item of items) {
    registered.set(item.type, item);
    INDICATOR_BY_ID.set(item.type, definitionOf(item));
  }
}

export const customIndicator = (type: string) => registered.get(type);

/** The latest version of each indicator, for the indicator list. */
export function latestCustom(): CustomIndicator[] {
  const latest = new Map<string, CustomIndicator>();
  for (const item of registered.values()) {
    const current = latest.get(item.indicator_id);
    if (!current || current.version < item.version) {
      latest.set(item.indicator_id, item);
    }
  }
  return [...latest.values()].sort((a, b) => a.name.localeCompare(b.name));
}
