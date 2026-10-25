import { describe, expect, it } from "vitest";

import { ema, renderIndicators, sma, type OhlcvBar } from "./indicators";
import {
  compilePine,
  monthlyBars,
  pineDefinition,
  PineError,
  pineRequests,
  runPine,
  type PineContext,
} from "./pine";
import { PineArray } from "./pine-objects";
import { fx, mul, toText } from "./pine-strategy";

const closes = (data: OhlcvBar[]) => data.map((bar) => bar.close);

function bars(count: number, start = 1_700_000_000, step = 60): OhlcvBar[] {
  return Array.from({ length: count }, (_, i) => {
    const close = 100 + 10 * Math.sin(i / 5) + i * 0.1;
    return {
      time: start + i * step,
      open: close - 0.5,
      high: close + 1,
      low: close - 1,
      close,
      volume: 1000 + i,
    };
  });
}

const run = (
  source: string,
  data = bars(30),
  params = {},
  context?: PineContext,
) => runPine(compilePine(source), data, params, context);

describe("Pine language", () => {
  it("runs switch, for … in, typed arrays, methods, defaults and continued lines", () => {
    const data = bars(20);
    const source = `//@version=5
indicator("P")
kind = input.string("EMA", "Type", options=["SMA", "EMA"])
ma(float src, simple int len = 3) =>
    switch kind
        "SMA" => ta.sma(src, len)
        => ta.ema(src, len)
var float[] xs = array.new<float>()
xs.push(close)
if xs.size() > 3
    xs.shift()
total = 0.0
for x in xs
    total += x
method twice(float v) => v * 2
longer = close +
  1
plot(ma(close))
plot(total)
plot(twice(1.5))
plot(longer)`;
    const result = run(source, data);
    expect(result.values.plot0.map((v) => v?.toFixed(9))).toEqual(
      ema(closes(data), 3).map((v) => v?.toFixed(9)),
    );
    const sum = data[5].close + data[4].close + data[3].close;
    expect(result.values.plot1[5]).toBeCloseTo(sum);
    expect(result.values.plot2[0]).toBe(3);
    expect(result.values.plot3[2]).toBeCloseTo(data[2].close + 1);
    const asSma = run(source, data, { type: 0 });
    expect(asSma.values.plot0[10]).toBeCloseTo(sma(closes(data), 3)[10]!);
    expect(compilePine(source).inputs[0]).toMatchObject({
      key: "type",
      default: 1,
      options: [
        { value: 0, label: "SMA" },
        { value: 1, label: "EMA" },
      ],
    });
  });

  it("has arrays and strings like Pine, with errors at the line", () => {
    const result = run(`indicator("A")
xs = array.from(3, 1, 2)
array.sort(xs)
label = str.format("{0} / {1,number,#.##}", array.join(xs, "-"), 2.345)
plot(array.get(xs, -1))
plot(array.median(xs))
plot(str.length(label))
plot(str.tostring(1234.5, "#,###.00") == "1,234.50" ? 1 : 0)
plot(("a" + "b") == "ab" ? 1 : 0)`);
    expect(result.values.plot0[0]).toBe(3);
    expect(result.values.plot1[0]).toBe(2);
    expect(result.values.plot2[0]).toBe("1-2-3 / 2.35".length);
    expect(result.values.plot3[0]).toBe(1);
    expect(result.values.plot4[0]).toBe(1);
    try {
      run(`indicator("A")\nxs = array.new_float(2)\nplot(array.get(xs, 5))`);
      throw new Error("no error");
    } catch (error) {
      expect(error).toBeInstanceOf(PineError);
      expect((error as PineError).message).toMatch(/out of bounds/);
      expect((error as PineError).line).toBe(3);
    }
  });

  it("refuses what it cannot run", () => {
    expect(() =>
      compilePine(
        `indicator("X")\nstrategy.entry("L", strategy.long)\nplot(close)`,
      ),
    ).not.toThrow(); // caught when it runs
    expect(() =>
      run(`indicator("X")\nstrategy.entry("L", strategy.long)\nplot(close)`),
    ).toThrow(/needs strategy/);
    expect(() =>
      compilePine(
        `strategy("X")\nstrategy.risk.max_drawdown(10, strategy.percent_of_equity)`,
      ),
    ).toThrow(/strategy.risk/);
    expect(() => compilePine(`indicator("X")\nimport user/lib/1`)).toThrow(
      /libraries/,
    );
    expect(() => compilePine(`indicator("X")\nx = 1`)).toThrow(/draws nothing/);
  });
});

describe("Pine drawings", () => {
  it("keeps lines, labels, boxes and tables, newest within the limits", () => {
    const data = bars(10);
    const { graphics } = run(
      `indicator("D", overlay=true, max_lines_count=2)
line.new(bar_index - 1, low, bar_index, high, color=color.red)
if barstate.islast
    label.new(bar_index, high, "Last " + str.tostring(close, "#.##"), yloc=yloc.abovebar)
    box.new(bar_index - 2, high, bar_index + 2, low, bgcolor=color.new(color.blue, 80))
var t = table.new(position.top_right, 2, 1)
table.cell(t, 0, 0, "A")
table.cell(t, 1, 0, "B", bgcolor=color.green)
plot(close)`,
      data,
    );
    const last = data[data.length - 1];
    expect(graphics.lines).toHaveLength(2);
    expect(graphics.lines[1]).toMatchObject({
      x1: data[8].time,
      x2: last.time,
      y2: last.high,
      color: "#f23645",
    });
    expect(graphics.labels).toEqual([
      expect.objectContaining({
        time: last.time,
        price: last.high,
        yloc: "above",
        text: `Last ${Number(last.close.toFixed(2))}`,
      }),
    ]);
    // A box reaching two bars past the last bar ends at an extrapolated time.
    expect(graphics.boxes[0]).toMatchObject({
      left: data[7].time,
      right: last.time + 120,
      bgColor: "rgba(41, 98, 255, 0.2)",
    });
    expect(graphics.tables).toHaveLength(1);
    expect(graphics.tables[0].cells.map((cell) => cell.text)).toEqual([
      "A",
      "B",
    ]);
  });

  it("collects shapes, backgrounds, bar colours and fills", () => {
    const data = bars(10);
    const source = `indicator("G", overlay=true)
p1 = plot(high)
p2 = plot(low, display=display.none)
fill(p1, p2, color=color.new(color.green, 90))
plotshape(bar_index % 2 == 0, style=shape.triangleup, location=location.belowbar, text="x")
bgcolor(bar_index % 3 == 0 ? color.new(color.red, 80) : na)
barcolor(bar_index == 5 ? color.yellow : na)
plotarrow(bar_index == 4 ? 1 : bar_index == 6 ? -1 : 0)`;
    const { graphics } = run(source, data);
    const triangles = graphics.shapes.filter(
      (item) => item.shape === "shape.triangleup",
    );
    expect(triangles).toHaveLength(5);
    expect(triangles[0]).toMatchObject({
      location: "below",
      price: data[0].low,
      text: "x",
    });
    expect(
      graphics.shapes.filter((item) => item.shape.startsWith("shape.arrow")),
    ).toHaveLength(2);
    expect(graphics.backgrounds.map((item) => item.time)).toEqual(
      [0, 3, 6, 9].map((i) => data[i].time),
    );
    expect(graphics.barColors).toEqual([
      { time: data[5].time, color: "#ffeb3b" },
    ]);
    expect(graphics.fills).toHaveLength(1);
    const fill = graphics.fills[0];
    expect(fill.kind === "series" && fill.points[3]).toMatchObject({
      y1: data[3].high,
      y2: data[3].low,
      color: "rgba(8, 153, 129, 0.1)",
    });
    expect(pineDefinition(source).outputs[1].hidden).toBe(true);
  });
});

describe("Pine request.security", () => {
  // Three 5-minute sessions of 4 bars; daily bars final at each day's last bar end.
  const day = 86_400;
  const start = 1_700_006_400; // a 00:00 UTC boundary plus nothing: just a base
  const chart: OhlcvBar[] = [0, 1, 2].flatMap((d) =>
    [0, 1, 2, 3].map((k) => ({
      time: start + d * day + k * 300,
      open: 10 + d,
      high: 11 + d,
      low: 9 + d,
      close: 10 + d + k * 0.1,
      volume: 100,
    })),
  );
  const ends = chart.map((bar) => bar.time + 300);
  const daily = {
    bars: [0, 1, 2].map((d) => ({
      time: start + d * day,
      open: 10 + d,
      high: 11 + d,
      low: 9 + d,
      close: 10.3 + d,
      volume: 400,
    })),
    availableAt: [0, 1, 2].map((d) => start + d * day + 1200),
  };
  const context: PineContext = {
    timeZone: "UTC",
    symbol: "AAA",
    timeframe: "5m",
    ends,
    requests: { "|1d": daily },
  };

  it("shows a larger bar's value only once it was final", () => {
    const result = run(
      `indicator("R")
d = request.security(syminfo.tickerid, "D", close)
plot(d)`,
      chart,
      {},
      context,
    );
    // Each day's close shows from that day's last 5-minute bar (when the day is final).
    expect(result.values.plot0).toEqual([
      null,
      null,
      null,
      10.3,
      10.3,
      10.3,
      10.3,
      11.3,
      11.3,
      11.3,
      11.3,
      12.3,
    ]);
    expect(result.missing).toEqual([]);
  });

  it("runs the expression on the other bars, with gaps and lookahead on x[1]", () => {
    const result = run(
      `indicator("R")
avg = request.security("AAA", "D", ta.sma(close, 2))
gaps = request.security("AAA", "1D", close, gaps=barmerge.gaps_on)
prev = request.security("AAA", "D", close[1], lookahead=barmerge.lookahead_on)
plot(avg)
plot(gaps)
plot(prev)`,
      chart,
      {},
      context,
    );
    expect(result.values.plot0[7]).toBeCloseTo((10.3 + 11.3) / 2);
    expect(result.values.plot1.filter((v) => v !== null)).toEqual([
      10.3, 11.3, 12.3,
    ]);
    // lookahead_on with close[1]: yesterday's close from the first bar of the day.
    expect(result.values.plot2.slice(4, 8)).toEqual([10.3, 10.3, 10.3, 10.3]);
    expect(() =>
      compilePine(
        `indicator("R")\nx = request.security("AAA", "D", close, lookahead=barmerge.lookahead_on)\nplot(x)`,
      ),
    ).toThrow(/future/);
  });

  it("gives smaller bars inside each chart bar as an array", () => {
    const minutes = {
      bars: chart.flatMap((bar) =>
        [0, 1, 2, 3, 4].map((m) => ({
          ...bar,
          time: bar.time + m * 60,
          close: bar.close + m,
        })),
      ),
      availableAt: chart.flatMap((bar) =>
        [1, 2, 3, 4, 5].map((m) => bar.time + m * 60),
      ),
    };
    const result = run(
      `indicator("L")
xs = request.security_lower_tf(syminfo.tickerid, "1", close)
plot(array.size(xs))
plot(array.size(xs) > 0 ? array.last(xs) : na)`,
      chart,
      {},
      { ...context, requests: { "|1m": minutes } },
    );
    expect(result.values.plot0).toEqual(chart.map(() => 5));
    expect(result.values.plot1[0]).toBeCloseTo(chart[0].close + 4);
  });

  it("lists the data a script needs and reports what is missing", () => {
    const source = `indicator("R")
tf = input.timeframe("60", "Timeframe")
a = request.security("NASDAQ:MSFT", tf, close)
b = request.security(syminfo.tickerid, "W", close)
c = request.security(syminfo.tickerid, timeframe.period, close)
plot(a + b + c)`;
    expect(
      pineRequests(source, {}, { symbol: "AAA", timeframe: "5m" }),
    ).toEqual([
      { key: "MSFT|60m", symbol: "MSFT", timeframe: "60m" },
      { key: "|1w", symbol: "", timeframe: "1w" },
    ]);
    const timeframe = compilePine(source).inputs[0];
    const daily = timeframe.options!.find(
      (option) => option.label === "1D",
    )!.value;
    expect(
      pineRequests(
        source,
        { timeframe: daily },
        { symbol: "AAA", timeframe: "5m" },
      )[0],
    ).toMatchObject({
      key: "MSFT|1d",
    });
    const result = run(source, chart, {}, { ...context, requests: {} });
    expect(result.missing).toEqual(["MSFT|60m", "|1w"]);
    expect(result.values.plot0.every((value) => value === null)).toBe(true);
    expect(() =>
      compilePine(
        `indicator("R")\nx = request.security(syminfo.tickerid, "7", close)\nplot(x)`,
      ),
    ).toThrow(/not supported/);
  });

  it("builds months from daily bars", () => {
    const days = {
      bars: [
        {
          time: Date.UTC(2026, 0, 30) / 1000,
          open: 1,
          high: 2,
          low: 0.5,
          close: 1.5,
          volume: 10,
        },
        {
          time: Date.UTC(2026, 1, 2) / 1000,
          open: 1.5,
          high: 3,
          low: 1,
          close: 2.5,
          volume: 20,
        },
        {
          time: Date.UTC(2026, 1, 3) / 1000,
          open: 2.5,
          high: 2.8,
          low: 0.8,
          close: 2,
          volume: 30,
        },
      ],
      availableAt: [1, 2, 3],
    };
    const months = monthlyBars(days, "UTC");
    expect(months.bars).toHaveLength(2);
    expect(months.bars[1]).toMatchObject({
      open: 1.5,
      high: 3,
      low: 0.8,
      close: 2,
      volume: 50,
    });
    expect(months.availableAt).toEqual([1, 3]);
  });
});

describe("Pine strategies", () => {
  const ohlc = (rows: [number, number, number, number][]): OhlcvBar[] =>
    rows.map(([open, high, low, close], i) => ({
      time: 1_700_000_000 + i * 60,
      open,
      high,
      low,
      close,
      volume: 100,
    }));
  const data = ohlc([
    [10, 11, 9, 10],
    [10, 12, 9.5, 11],
    [11, 13, 10.5, 12],
    [12, 12.5, 8, 9],
    [9, 10, 8.5, 9.5],
    [9.5, 11, 9, 10.5],
  ]);

  it("fills on later bars and reports decimal results", () => {
    const source = `strategy("S", initial_capital=1000, default_qty_value=10, commission_type=strategy.commission.percent, commission_value=0.1)
if bar_index == 0
    strategy.entry("L", strategy.long)
if bar_index == 1
    strategy.exit("X", "L", limit=12.8, stop=8.5)
plot(strategy.position_size)`;
    const result = run(source, data, {}, { timeZone: "UTC", priceDecimals: 2 });
    // The entry placed on bar 0 fills at bar 1's open; the exit's limit on bar 2.
    expect(result.values.plot0.slice(0, 4)).toEqual([0, 10, 0, 0]);
    const report = result.strategy!;
    expect(report).toMatchObject({
      closedTrades: 1,
      winTrades: 1,
      netProfit: "27.77",
      commission: "0.23",
      assumedFills: 0,
    });
    expect(report.trades[0]).toMatchObject({
      entryId: "L",
      exitId: "X",
      direction: "long",
      qty: "10",
      entryPrice: "10.00",
      exitPrice: "12.80",
      profit: "27.77",
    });
    expect(
      result.graphics.trades.map((mark) => [mark.side, mark.price]),
    ).toEqual([
      ["buy", 10],
      ["sell", 12.8],
    ]);
    // Without the later bars, the same orders stay open: no future values are used.
    const cut = run(
      source,
      data.slice(0, 2),
      {},
      { timeZone: "UTC", priceDecimals: 2 },
    );
    expect(cut.values.plot0).toEqual([0, 10]);
    expect(cut.strategy!.closedTrades).toBe(0);
    expect(cut.strategy!.openPosition).toMatchObject({
      size: "10",
      avgPrice: "10.00",
    });
  });

  it("marks fills that depend on the order of prices inside a bar", () => {
    const result = run(
      `strategy("S")
if bar_index == 1
    strategy.entry("L", strategy.long, stop=12)
    strategy.exit("X", "L", limit=12.9)
plot(close)`,
      data,
      {},
      { timeZone: "UTC", priceDecimals: 2 },
    );
    const report = result.strategy!;
    expect(report.trades[0]).toMatchObject({
      entryPrice: "12.00",
      exitPrice: "12.90",
      assumed: true,
    });
    expect(report.assumedFills).toBe(2);
  });

  it("fills on the close, reverses positions and sizes by equity", () => {
    const onClose = run(
      `strategy("S", process_orders_on_close=true)
if bar_index == 0
    strategy.entry("L", strategy.long)
plot(strategy.position_size)`,
      data,
    );
    // Filled at bar 0's close, after the script ran on it: seen from bar 1 on.
    expect(onClose.values.plot0.slice(0, 2)).toEqual([0, 1]);
    expect(onClose.strategy!.trades[0].entryPrice).toBe("10.00");
    const reverse = run(
      `strategy("S", default_qty_type=strategy.percent_of_equity, default_qty_value=50, initial_capital=1000)
if bar_index == 0
    strategy.entry("L", strategy.long)
if bar_index == 2
    strategy.entry("S", strategy.short)
plot(strategy.position_size)`,
      data,
    );
    // 50% of 1000 at 10 is 50 shares; the short at bar 3's open first closes the long.
    expect(reverse.values.plot0.slice(0, 4)).toEqual([
      0,
      50,
      50,
      expect.any(Number),
    ]);
    expect(reverse.values.plot0[3]).toBeLessThan(0);
    expect(reverse.strategy!.trades[0]).toMatchObject({
      direction: "long",
      exitPrice: "12.00",
    });
  });

  it("does decimal arithmetic on amounts", () => {
    expect(toText(mul(fx(0.1), fx(0.2)), 4)).toBe("0.0200");
    expect(toText(fx(0.1) + fx(0.2), 10)).toBe("0.3000000000");
  });
});

describe("Pine on the chart", () => {
  it("passes drawings, strategy results and run errors to the chart", () => {
    const data = bars(10);
    const [drawn, broken] = renderIndicators(data, [
      {
        key: "a",
        type: "pine",
        params: {},
        pine: `strategy("S", overlay=true)\nif bar_index == 1\n    strategy.entry("L", strategy.long)\nplotshape(true)`,
      },
      {
        key: "b",
        type: "pine",
        params: {},
        pine: `indicator("B")\nxs = array.new_float(1)\nplot(array.get(xs, 3))`,
      },
    ]);
    expect(drawn.graphics?.shapes).toHaveLength(10);
    expect(drawn.strategy?.openPosition).not.toBeNull();
    expect(drawn.lines).toEqual([]);
    expect(broken.pineError).toMatch(/out of bounds.*\(line 3\)/);
    expect(new PineArray([1]).items).toEqual([1]);
  });
});

describe("Pine ta.* additions", () => {
  it("never read a later bar", () => {
    const source = `indicator("T")
[st, dir] = ta.supertrend(3, 10)
[p, m, adx] = ta.dmi(14, 14)
[mid, up, lo] = ta.kc(close, 20, 1.5)
plot(ta.pivothigh(3, 3))
plot(ta.pivotlow(close, 2, 2))
plot(st)
plot(adx)
plot(ta.sar(0.02, 0.02, 0.2))
plot(ta.linreg(close, 10, 0))
plot(ta.correlation(close, volume, 10))
plot(ta.percentrank(close, 10))
plot(ta.highestbars(high, 5))
plot(ta.alma(close, 9, 0.85, 6))
plot(ta.mfi(hlc3, 14))
plot(ta.obv)
plot(up)`;
    const data = bars(80);
    const full = run(source, data).values;
    const cut = run(source, data.slice(0, 50)).values;
    for (const key of Object.keys(full)) {
      expect(cut[key]).toEqual(full[key].slice(0, 50));
    }
    // A pivot is reported on the bar that confirms it, `right` bars after the pivot.
    const pivots = full.plot0
      .map((value, i) => (value === null ? null : i))
      .filter((i): i is number => i !== null);
    for (const i of pivots) expect(full.plot0[i]).toBe(data[i - 3].high);
  });
});
