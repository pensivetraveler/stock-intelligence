/** Date helpers for period reviews (ADR 0042, step 4a); dates are YYYY-MM-DD. */

const DAY_MS = 86_400_000;

function iso(time: number): string {
  return new Date(time).toISOString().slice(0, 10);
}

function at(date: string): number {
  return Date.parse(`${date}T00:00:00Z`);
}

export type Preset = "thisWeek" | "lastWeek" | "thisMonth" | "lastMonth";

/** The dates of a preset period around `today` (weeks start on Monday). */
export function presetRange(preset: Preset, today: string): [string, string] {
  const now = at(today);
  const weekday = (new Date(now).getUTCDay() + 6) % 7; // Monday = 0
  const monday = now - weekday * DAY_MS;
  if (preset === "thisWeek") return [iso(monday), today];
  if (preset === "lastWeek") {
    return [iso(monday - 7 * DAY_MS), iso(monday - DAY_MS)];
  }
  const [year, month] = today.split("-").map(Number) as [number, number];
  const firstOfMonth = Date.UTC(year, month - 1, 1);
  if (preset === "thisMonth") return [iso(firstOfMonth), today];
  const firstOfLast = Date.UTC(year, month - 2, 1);
  return [iso(firstOfLast), iso(firstOfMonth - DAY_MS)];
}

/** Every date from `first` to `last`, inclusive. */
export function daysBetween(first: string, last: string): string[] {
  const days = [];
  for (let time = at(first); time <= at(last); time += DAY_MS) {
    days.push(iso(time));
  }
  return days;
}

/** Monday = 0 … Sunday = 6. */
export function weekdayOf(date: string): number {
  return (new Date(at(date)).getUTCDay() + 6) % 7;
}

/**
 * Chart times for the cumulative curve: the closing time (or the closing day), nudged
 * by a second when two trades share one, since the chart needs rising times.
 */
export function curveTimes(
  points: { closed_on: string; closed_at_utc: string | null }[],
): number[] {
  const times: number[] = [];
  for (const point of points) {
    let time = Math.floor(
      (point.closed_at_utc
        ? Date.parse(point.closed_at_utc)
        : at(point.closed_on)) / 1000,
    );
    const previous = times[times.length - 1];
    if (previous !== undefined && time <= previous) time = previous + 1;
    times.push(time);
  }
  return times;
}

export interface SuggestionInput {
  action: "add" | "change" | "remove";
  item_id: string | null;
  scope: string;
  kind: string;
  value: string | null;
  text: string;
}

export interface ItemInput {
  item_id?: string;
  scope: string;
  kind: string;
  value: string | null;
  text: string;
}

/**
 * The principles with the chosen suggestions applied, for the investor to check in the
 * editor before saving: a change replaces its item, a remove drops it, an add is appended.
 * A change or remove naming an unknown item is added instead of being lost.
 */
export function applySuggestions(
  items: ItemInput[],
  suggestions: SuggestionInput[],
): { scope: string; kind: string; value: string | null; text: string }[] {
  const next = items.map((item) => ({ ...item }));
  for (const suggestion of suggestions) {
    const index = next.findIndex(
      (item) =>
        item.item_id !== undefined && item.item_id === suggestion.item_id,
    );
    const replacement = {
      scope: suggestion.scope,
      kind: suggestion.kind,
      value: suggestion.kind === "text" ? null : suggestion.value,
      text: suggestion.text,
    };
    if (suggestion.action === "remove" && index >= 0) {
      next.splice(index, 1);
    } else if (suggestion.action === "change" && index >= 0) {
      next[index] = { ...replacement, item_id: next[index]!.item_id };
    } else if (suggestion.action !== "remove") {
      next.push(replacement);
    }
  }
  return next.map(({ scope, kind, value, text }) => ({
    scope,
    kind,
    value,
    text,
  }));
}
