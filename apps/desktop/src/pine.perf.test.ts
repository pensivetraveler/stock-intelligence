// Timing of the Pine interpreter on long charts. Opt-in: SIW_PINE_PERF=1 pnpm vitest run src/pine.perf.test.ts
// Prints a table (median of several runs per script and bar count); it asserts nothing about speed.
import { describe, it } from "vitest";

import { ema, rsi, type OhlcvBar } from "./indicators";
import {
  compilePine,
  PINE_EXAMPLE,
  PINE_STRATEGY_EXAMPLE,
  runPine,
} from "./pine";

const SIZES = [1_200, 5_000, 13_200, 50_000];
const RUNS = 5;

function bars(count: number): OhlcvBar[] {
  return Array.from({ length: count }, (_, i) => {
    const close = 100 + 10 * Math.sin(i / 37) + 3 * Math.sin(i / 5) + i * 0.001;
    return {
      time: 1_700_000_000 + i * 60,
      open: close - 0.3,
      high: close + 0.8,
      low: close - 0.8,
      close,
      volume: 1000 + (i % 97) * 13,
    };
  });
}

const SCRIPTS: Record<string, string> = {
  emaCross: PINE_EXAMPLE,
  strategy: PINE_STRATEGY_EXAMPLE,
  taHeavy: `//@version=5
indicator("Heavy")
r = ta.rsi(close, 14)
[m, s, h] = ta.macd(close, 12, 26, 9)
[mid, up, lo] = ta.bb(close, 20, 2)
a = ta.atr(14)
k = ta.stoch(close, high, low, 14)
hh = ta.highest(high, 50)
ll = ta.lowest(low, 50)
plot(r)
plot(h)
plot((close - lo) / (up - lo))
plot(a)
plot(k)
plot(hh - ll)
plotshape(ta.crossover(m, s), style=shape.triangleup)
`,
  loop50: `//@version=5
indicator("Loop")
total = 0.0
for i = 0 to 49
    total += nz(close[i])
plot(total / 50)
`,
  arrayWindow: `//@version=5
indicator("Array")
var float[] xs = array.new<float>()
array.push(xs, close)
if array.size(xs) > 100
    array.shift(xs)
plot(array.avg(xs))
`,
  drawings: `//@version=5
indicator("Draw", overlay=true)
line.new(bar_index - 1, low, bar_index, high, color=color.red)
label.new(bar_index, high, str.tostring(close))
`,
};

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)];
}

function time(fn: () => unknown): number {
  fn(); // warm-up
  const times: number[] = [];
  for (let i = 0; i < RUNS; i += 1) {
    const start = performance.now();
    fn();
    times.push(performance.now() - start);
  }
  return median(times);
}

describe.skipIf(!process.env.SIW_PINE_PERF)("Pine timing", () => {
  it("prints run times on long charts", () => {
    const rows: Record<string, Record<string, string>> = {};
    for (const [name, source] of Object.entries(SCRIPTS)) {
      const program = compilePine(source);
      rows[`${name} (compile)`] = {
        any: `${time(() => compilePine(source)).toFixed(2)} ms`,
      };
      rows[name] = {};
      for (const size of SIZES) {
        const data = bars(size);
        try {
          rows[name][size] =
            `${time(() => runPine(program, data, {})).toFixed(1)} ms`;
        } catch (error) {
          rows[name][size] = `error: ${String(error).slice(0, 40)}`;
        }
      }
    }
    rows["built-in ema+rsi"] = {};
    for (const size of SIZES) {
      const closes = bars(size).map((bar) => bar.close);
      rows["built-in ema+rsi"][size] = `${time(() => {
        ema(closes, 9);
        ema(closes, 21);
        rsi(closes, 14);
      }).toFixed(2)} ms`;
    }
    console.table(rows);
  }, 600_000);
});
