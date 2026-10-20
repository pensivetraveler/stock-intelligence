/**
 * Bar replay: the charts act as if "now" were the replay time. Only bars that had
 * closed by then are shown and fed to indicators, the legend and markers, so nothing
 * after the replay time can be seen or used.
 *
 * The replay clock is market time (a bar counts once its interval has ended). It does
 * not model a feed's publication delay; that is a property of the data, not of replay.
 */

import type { PriceBar } from "@siw/contracts/types";

export type ReplayPhase = "off" | "choosing" | "running";

export interface ReplayState {
  phase: ReplayPhase;
  /** Epoch seconds; bars ending after it are hidden. */
  time: number | null;
  playing: boolean;
  /** Steps per second while playing. */
  speed: number;
}

export const REPLAY_SPEEDS = [1, 2, 5, 10] as const;

export const REPLAY_OFF: ReplayState = {
  phase: "off",
  time: null,
  playing: false,
  speed: 1,
};

function seconds(utc: string): number {
  return Math.floor(Date.parse(utc) / 1000);
}

/** The bars that had closed at `time`; all of them when replay is off. */
export function barsAsOf(bars: PriceBar[], time: number | null): PriceBar[] {
  if (time === null) return bars;
  return bars.filter((bar) => seconds(bar.interval_end_utc) <= time);
}

export function barEnd(bar: PriceBar): number {
  return seconds(bar.interval_end_utc);
}

/**
 * The next replay time: one step later, but never past `last` (the end of the
 * latest loaded bar). Returns null when replay is already at the end.
 */
export function stepForward(
  time: number,
  stepSeconds: number,
  last: number | null,
): number | null {
  if (last !== null && time >= last) return null;
  const next = time + stepSeconds;
  return last === null ? next : Math.min(next, last);
}

export function stepBack(
  time: number,
  stepSeconds: number,
  first: number | null,
): number {
  const previous = time - stepSeconds;
  return first === null ? previous : Math.max(previous, first);
}
