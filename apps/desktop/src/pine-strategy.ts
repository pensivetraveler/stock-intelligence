/**
 * Pine strategies as backtests on the chart (ADR 0040): a broker emulator that fills
 * `strategy.entry/order/exit/close` orders on later bars of the same chart. Fills are
 * backtest fills, kept in memory for the chart only; no order ever reaches a broker
 * and nothing is written to a ledger.
 *
 * Fill rules (TradingView's defaults):
 * - an order placed on a bar is filled from the next bar on: market orders at its
 *   open, limit and stop orders when the bar's range reaches their price;
 *   `process_orders_on_close=true` or `immediately=true` fills market orders at
 *   the close of the bar that placed them;
 * - inside a bar, prices are assumed to move open → the nearer of high/low → the
 *   other → close. OHLC bars do not record that order, so when two or more fills
 *   inside one bar depend on it, those fills are marked `assumed`.
 *
 * Money and quantities are fixed-point decimals (10 digits), not floating point.
 */
import type { OhlcvBar } from "./indicators";

// --- fixed-point decimals -----------------------------------------------------------

export type Fixed = bigint;
const DIGITS = 10;
const SCALE = 10n ** BigInt(DIGITS);

/** A number as a decimal, rounded to 10 digits (bar prices come as numbers). */
export function fx(value: number): Fixed {
  if (!Number.isFinite(value)) return 0n;
  const text = value.toFixed(DIGITS);
  const negative = text.startsWith("-");
  const [whole, fraction = ""] = (negative ? text.slice(1) : text).split(".");
  const units = BigInt(whole) * SCALE + BigInt(fraction.padEnd(DIGITS, "0"));
  return negative ? -units : units;
}

const half = (divisor: bigint) => (divisor < 0n ? -divisor : divisor) / 2n;

function roundDiv(a: bigint, b: bigint): bigint {
  if (b === 0n) return 0n;
  const negative = a < 0n !== b < 0n;
  const absA = a < 0n ? -a : a;
  const absB = b < 0n ? -b : b;
  const q = (absA + half(absB)) / absB;
  return negative ? -q : q;
}

export const mul = (a: Fixed, b: Fixed): Fixed => roundDiv(a * b, SCALE);
export const div = (a: Fixed, b: Fixed): Fixed => roundDiv(a * SCALE, b);
export const toNumber = (a: Fixed): number => Number(a) / Number(SCALE);
const floorUnits = (a: Fixed): Fixed => (a / SCALE) * SCALE; // whole shares, toward 0

/** A decimal as text with `decimals` digits (rounded half away from zero). */
export function toText(a: Fixed, decimals = 2): string {
  const step = 10n ** BigInt(DIGITS - decimals);
  const rounded = roundDiv(a, step);
  const negative = rounded < 0n;
  const digits = (negative ? -rounded : rounded)
    .toString()
    .padStart(decimals + 1, "0");
  const whole = digits.slice(0, digits.length - decimals);
  const fraction = digits.slice(digits.length - decimals);
  return `${negative ? "-" : ""}${whole}${decimals > 0 ? `.${fraction}` : ""}`;
}

// --- settings and report ------------------------------------------------------------

export interface StrategySettings {
  initialCapital: number;
  qtyType: "fixed" | "cash" | "percent_of_equity";
  qtyValue: number;
  commissionType: "percent" | "cash_per_contract" | "cash_per_order";
  commissionValue: number;
  /** Slippage in ticks, added against each market and stop fill. */
  slippage: number;
  pyramiding: number;
  processOnClose: boolean;
  closeEntriesRule: "FIFO" | "ANY";
  currency: string;
}

export const DEFAULT_STRATEGY: StrategySettings = {
  initialCapital: 100_000,
  qtyType: "fixed",
  qtyValue: 1,
  commissionType: "percent",
  commissionValue: 0,
  slippage: 0,
  pyramiding: 0,
  processOnClose: false,
  closeEntriesRule: "FIFO",
  currency: "USD",
};

/** A closed or open backtest trade; amounts are decimal text. */
export interface StrategyTrade {
  entryId: string;
  exitId: string | null;
  direction: "long" | "short";
  qty: string;
  entryTime: number;
  entryPrice: string;
  exitTime: number | null;
  exitPrice: string | null;
  profit: string;
  commission: string;
  assumed: boolean;
}

export interface StrategyReport {
  currency: string;
  initialCapital: string;
  netProfit: string;
  netProfitPercent: string;
  grossProfit: string;
  grossLoss: string;
  profitFactor: string | null;
  closedTrades: number;
  winTrades: number;
  lossTrades: number;
  winRate: string | null;
  maxDrawdown: string;
  commission: string;
  openPosition: { size: string; avgPrice: string; openProfit: string } | null;
  trades: StrategyTrade[];
  /** Fills that depended on the assumed order of prices inside a bar. */
  assumedFills: number;
  /** Orders the emulator skipped, e.g. a quantity below one share. */
  skippedOrders: number;
}

// --- the emulator -------------------------------------------------------------------

interface Order {
  id: string;
  kind: "entry" | "order" | "exit" | "close";
  direction: 1 | -1;
  qty: Fixed | null; // null: the default quantity (entries) or everything (exits)
  qtyPercent: number | null;
  limit: number | null;
  stop: number | null;
  /** exits: ticks from the entry price (profit/loss), and a trailing stop. */
  profitTicks: number | null;
  lossTicks: number | null;
  trailPoints: number | null;
  trailPrice: number | null;
  trailOffset: number | null;
  trailBest: number | null;
  fromEntry: string | null;
  comment: string;
  placedBar: number;
  /** Market orders filled at this bar's close (process_orders_on_close / immediately). */
  onClose: boolean;
}

interface Lot {
  entryId: string;
  direction: 1 | -1;
  qty: Fixed;
  price: Fixed;
  time: number;
  bar: number;
  commission: Fixed; // still unassigned entry commission for this lot
  assumed: boolean;
}

interface Fill {
  price: number;
  /** Where on the bar's path: 0 = open, 3 = close; 1–2 inside the bar. */
  at: number;
}

export class StrategyBroker {
  private orders: Order[] = [];
  private lots: Lot[] = [];
  private readonly closed: StrategyTrade[] = [];
  readonly marks: {
    time: number;
    price: number;
    side: "buy" | "sell";
    label: string;
    assumed: boolean;
  }[] = [];
  private netProfit: Fixed = 0n;
  private grossProfit: Fixed = 0n;
  private grossLoss: Fixed = 0n;
  private commissionTotal: Fixed = 0n;
  private peak: Fixed;
  private maxDrawdown: Fixed = 0n;
  private assumedFills = 0;
  private skipped = 0;
  private barFills: {
    mark: number;
    trade: StrategyTrade | null;
    lot: Lot | null;
  }[] = [];
  private lastClose = 0;
  private wins = 0;
  private losses = 0;

  constructor(
    readonly settings: StrategySettings,
    private readonly tick: number,
    private readonly decimals: number,
  ) {
    this.peak = fx(settings.initialCapital);
  }

  // --- what the script sees -----------------------------------------------------

  get positionSize(): number {
    return toNumber(
      this.lots.reduce((sum, lot) => sum + lot.qty * BigInt(lot.direction), 0n),
    );
  }

  get positionAvgPrice(): number | null {
    const qty = this.lots.reduce((sum, lot) => sum + lot.qty, 0n);
    if (qty === 0n) return null;
    return toNumber(
      div(
        this.lots.reduce((sum, lot) => sum + mul(lot.qty, lot.price), 0n),
        qty,
      ),
    );
  }

  private openProfitAt(price: number): Fixed {
    return this.lots.reduce(
      (sum, lot) =>
        sum + mul(lot.qty, fx(price) - lot.price) * BigInt(lot.direction),
      0n,
    );
  }

  /** `strategy.*` variables at the bar's close. */
  variables(): Record<string, number | null> {
    const openProfit = this.openProfitAt(this.lastClose);
    const equity =
      fx(this.settings.initialCapital) + this.netProfit + openProfit;
    return {
      "strategy.position_size": this.positionSize,
      "strategy.position_avg_price": this.positionAvgPrice,
      "strategy.equity": toNumber(equity),
      "strategy.netprofit": toNumber(this.netProfit),
      "strategy.openprofit": toNumber(openProfit),
      "strategy.grossprofit": toNumber(this.grossProfit),
      "strategy.grossloss": toNumber(this.grossLoss),
      "strategy.initial_capital": this.settings.initialCapital,
      "strategy.closedtrades": this.closed.length,
      "strategy.opentrades": this.lots.length,
      "strategy.wintrades": this.wins,
      "strategy.losstrades": this.losses,
      "strategy.eventrades": this.closed.length - this.wins - this.losses,
      "strategy.max_drawdown": toNumber(this.maxDrawdown),
      "strategy.position_entry_name": null,
    };
  }

  closedTrade(index: number): StrategyTrade | null {
    return this.closed[index] ?? null;
  }

  openLot(index: number): {
    entryId: string;
    qty: number;
    price: number;
    bar: number;
    time: number;
    direction: 1 | -1;
  } | null {
    const lot = this.lots[index];
    return lot
      ? {
          entryId: lot.entryId,
          qty: toNumber(lot.qty),
          price: toNumber(lot.price),
          bar: lot.bar,
          time: lot.time,
          direction: lot.direction,
        }
      : null;
  }

  // --- orders from the script -----------------------------------------------------

  private place(order: Order): void {
    // An order with the same id replaces the pending one, as on TradingView.
    this.orders = this.orders.filter(
      (item) => !(item.id === order.id && item.kind === order.kind),
    );
    this.orders.push(order);
  }

  private base(bar: number): Omit<Order, "id" | "kind" | "direction"> {
    return {
      qty: null,
      qtyPercent: null,
      limit: null,
      stop: null,
      profitTicks: null,
      lossTicks: null,
      trailPoints: null,
      trailPrice: null,
      trailOffset: null,
      trailBest: null,
      fromEntry: null,
      comment: "",
      placedBar: bar,
      onClose: false,
    };
  }

  entry(
    kind: "entry" | "order",
    id: string,
    direction: 1 | -1,
    qty: number | null,
    limit: number | null,
    stop: number | null,
    comment: string,
    bar: number,
  ): void {
    this.place({
      ...this.base(bar),
      id,
      kind,
      direction,
      qty: qty === null ? null : fx(qty),
      limit,
      stop,
      comment,
      onClose: this.settings.processOnClose && limit === null && stop === null,
    });
  }

  exit(
    id: string,
    fromEntry: string | null,
    qty: number | null,
    qtyPercent: number | null,
    levels: {
      profit: number | null;
      limit: number | null;
      loss: number | null;
      stop: number | null;
      trailPoints: number | null;
      trailPrice: number | null;
      trailOffset: number | null;
    },
    comment: string,
    bar: number,
  ): void {
    this.place({
      ...this.base(bar),
      id,
      kind: "exit",
      direction: 1,
      qty: qty === null ? null : fx(qty),
      qtyPercent,
      limit: levels.limit,
      stop: levels.stop,
      profitTicks: levels.profit,
      lossTicks: levels.loss,
      trailPoints: levels.trailPoints,
      trailPrice: levels.trailPrice,
      trailOffset: levels.trailOffset,
      fromEntry,
      comment,
    });
  }

  close(
    entryId: string | null,
    comment: string,
    qty: number | null,
    qtyPercent: number | null,
    immediately: boolean,
    bar: number,
    closePrice: number,
    time: number,
  ): void {
    if (!this.lots.some((lot) => entryId === null || lot.entryId === entryId))
      return;
    const order: Order = {
      ...this.base(bar),
      id: entryId ?? "__all",
      kind: "close",
      direction: 1,
      qty: qty === null ? null : fx(qty),
      qtyPercent,
      fromEntry: entryId,
      comment,
      onClose: immediately || this.settings.processOnClose,
    };
    if (immediately) {
      this.exitLots(order, closePrice, time);
      return;
    }
    this.place(order);
  }

  cancel(id: string | null): void {
    this.orders = this.orders.filter((order) => id !== null && order.id !== id);
  }

  // --- filling ------------------------------------------------------------------

  private slip(price: number, buying: boolean): number {
    const offset = this.settings.slippage * this.tick;
    return buying ? price + offset : price - offset;
  }

  private commissionFor(qty: Fixed, price: Fixed): Fixed {
    const value = fx(this.settings.commissionValue);
    switch (this.settings.commissionType) {
      case "cash_per_contract":
        return mul(qty, value);
      case "cash_per_order":
        return value;
      default:
        return roundDiv(mul(mul(qty, price), value), 100n);
    }
  }

  private equityAt(price: number): Fixed {
    return (
      fx(this.settings.initialCapital) +
      this.netProfit +
      this.openProfitAt(price)
    );
  }

  private defaultQty(price: number): Fixed | null {
    const value = fx(this.settings.qtyValue);
    switch (this.settings.qtyType) {
      case "cash":
        return floorUnits(div(value, fx(price)));
      case "percent_of_equity":
        // Whole shares, as for stocks.
        return floorUnits(
          div(roundDiv(mul(this.equityAt(price), value), 100n), fx(price)),
        );
      default:
        return value;
    }
  }

  private positionDirection(): 1 | -1 | 0 {
    const size = this.lots.reduce(
      (sum, lot) => sum + lot.qty * BigInt(lot.direction),
      0n,
    );
    return size > 0n ? 1 : size < 0n ? -1 : 0;
  }

  private positionQty(): Fixed {
    return this.lots.reduce((sum, lot) => sum + lot.qty, 0n);
  }

  /** Fills an entry/order at `price`; returns false when nothing was filled. */
  private fillEntry(
    order: Order,
    price: number,
    time: number,
    bar: number,
  ): boolean {
    const qty = order.qty ?? this.defaultQty(price);
    if (qty === null || qty <= 0n) {
      this.skipped += 1;
      return false;
    }
    const direction = this.positionDirection();
    if (order.kind === "entry") {
      if (direction === -order.direction) {
        // An entry against the position closes it first, then opens the new one.
        this.exitLots(
          {
            ...order,
            kind: "close",
            fromEntry: null,
            qty: null,
            qtyPercent: null,
          },
          price,
          time,
        );
      } else if (direction === order.direction) {
        const open = this.lots.filter(
          (lot) => lot.direction === order.direction,
        ).length;
        if (open >= Math.max(1, this.settings.pyramiding)) return false;
      }
    } else if (direction === -order.direction) {
      // strategy.order: a raw order that reduces the opposite position first.
      const reduce = qty < this.positionQty() ? qty : this.positionQty();
      this.exitLots(
        {
          ...order,
          kind: "close",
          fromEntry: null,
          qty: reduce,
          qtyPercent: null,
        },
        price,
        time,
      );
      const rest = qty - reduce;
      if (rest <= 0n) return true;
      return this.addLot(order, rest, price, time, bar);
    }
    return this.addLot(order, qty, price, time, bar);
  }

  private addLot(
    order: Order,
    qty: Fixed,
    price: number,
    time: number,
    bar: number,
  ): boolean {
    const filled = fx(price);
    const commission = this.commissionFor(qty, filled);
    this.commissionTotal += commission;
    this.netProfit -= commission;
    const lot: Lot = {
      entryId: order.id,
      direction: order.direction,
      qty,
      price: filled,
      time,
      bar,
      commission,
      assumed: false,
    };
    this.lots.push(lot);
    this.marks.push({
      time,
      price,
      side: order.direction === 1 ? "buy" : "sell",
      label: order.comment || order.id,
      assumed: false,
    });
    this.barFills.push({ mark: this.marks.length - 1, trade: null, lot });
    return true;
  }

  /** Closes lots (all, or those of one entry) at `price`. */
  private exitLots(order: Order, price: number, time: number): void {
    let candidates = this.lots.filter(
      (lot) => order.fromEntry === null || lot.entryId === order.fromEntry,
    );
    if (candidates.length === 0) return;
    if (this.settings.closeEntriesRule === "FIFO" && order.fromEntry !== null) {
      // FIFO: an exit for one entry still closes the oldest lots first.
      candidates = this.lots.slice(0, candidates.length);
    }
    const available = candidates.reduce((sum, lot) => sum + lot.qty, 0n);
    let remaining =
      order.qty !== null
        ? order.qty < available
          ? order.qty
          : available
        : order.qtyPercent !== null
          ? roundDiv(available * fx(order.qtyPercent), fx(100))
          : available;
    if (remaining <= 0n) return;
    const filled = fx(price);
    const direction = candidates[0].direction;
    for (const lot of candidates) {
      if (remaining <= 0n) break;
      const qty = lot.qty < remaining ? lot.qty : remaining;
      const share =
        lot.qty === 0n ? 0n : roundDiv(lot.commission * qty, lot.qty);
      const exitCommission = this.commissionFor(qty, filled);
      this.commissionTotal += exitCommission;
      const gross = mul(qty, filled - lot.price) * BigInt(lot.direction);
      const profit = gross - share - exitCommission;
      this.netProfit += gross - exitCommission;
      if (profit > 0n) {
        this.grossProfit += profit;
        this.wins += 1;
      } else if (profit < 0n) {
        this.grossLoss += -profit;
        this.losses += 1;
      }
      const trade: StrategyTrade = {
        entryId: lot.entryId,
        exitId: order.kind === "close" ? null : order.id,
        direction: lot.direction === 1 ? "long" : "short",
        qty: toText(qty, 4).replace(/\.?0+$/, ""),
        entryTime: lot.time,
        entryPrice: toText(lot.price, this.decimals),
        exitTime: time,
        exitPrice: toText(filled, this.decimals),
        profit: toText(profit),
        commission: toText(share + exitCommission),
        assumed: lot.assumed,
      };
      this.closed.push(trade);
      this.barFills.push({ mark: -1, trade, lot: null });
      lot.qty -= qty;
      lot.commission -= share;
      remaining -= qty;
    }
    this.lots = this.lots.filter((lot) => lot.qty > 0n);
    this.marks.push({
      time,
      price,
      side: direction === 1 ? "sell" : "buy",
      label: order.comment || (order.kind === "exit" ? order.id : "Close"),
      assumed: false,
    });
    this.barFills[this.barFills.length - 1].mark = this.marks.length - 1;
  }

  /** The exit's limit and stop prices for the current position, or null. */
  private exitLevels(
    order: Order,
  ): { limit: number | null; stop: number | null } | null {
    const lots = this.lots.filter(
      (lot) => order.fromEntry === null || lot.entryId === order.fromEntry,
    );
    if (lots.length === 0) return null;
    const direction = lots[0].direction;
    const qty = lots.reduce((sum, lot) => sum + lot.qty, 0n);
    const entry = toNumber(
      div(
        lots.reduce((sum, lot) => sum + mul(lot.qty, lot.price), 0n),
        qty,
      ),
    );
    const limit =
      order.limit ??
      (order.profitTicks === null
        ? null
        : entry + direction * order.profitTicks * this.tick);
    let stop =
      order.stop ??
      (order.lossTicks === null
        ? null
        : entry - direction * order.lossTicks * this.tick);
    if (order.trailBest !== null && order.trailOffset !== null) {
      const trail = order.trailBest - direction * order.trailOffset * this.tick;
      stop =
        stop === null
          ? trail
          : direction === 1
            ? Math.max(stop, trail)
            : Math.min(stop, trail);
    }
    return { limit, stop };
  }

  /** Where on a path segment from `from` to `to` an order is triggered, if it is. */
  private trigger(
    order: Order,
    from: number,
    to: number,
    at: number,
  ): Fill | null {
    const low = Math.min(from, to);
    const high = Math.max(from, to);
    const reach = (
      price: number | null,
      buying: boolean,
      kind: "limit" | "stop",
    ): Fill | null => {
      if (price === null) return null;
      // At the open (at = 0, from = to) a gap through the price fills at the open.
      if (at === 0) {
        const through =
          kind === "limit"
            ? buying
              ? from <= price
              : from >= price
            : buying
              ? from >= price
              : from <= price;
        return through
          ? { price: kind === "stop" ? this.slip(from, buying) : from, at }
          : null;
      }
      return price >= low && price <= high
        ? { price: kind === "stop" ? this.slip(price, buying) : price, at }
        : null;
    };
    if (order.kind === "entry" || order.kind === "order") {
      const buying = order.direction === 1;
      if (order.limit === null && order.stop === null) {
        return at === 0 ? { price: this.slip(from, buying), at } : null;
      }
      return (
        reach(order.limit, buying, "limit") ?? reach(order.stop, buying, "stop")
      );
    }
    if (order.kind === "close") {
      return at === 0
        ? { price: this.slip(from, this.positionDirection() === -1), at }
        : null;
    }
    const levels = this.exitLevels(order);
    if (!levels) return null;
    const buying = this.positionDirection() === -1;
    const byLimit = reach(levels.limit, buying, "limit");
    const byStop = reach(levels.stop, buying, "stop");
    if (byLimit && byStop) {
      // Both inside one segment: the one nearer the segment's start comes first.
      return Math.abs((levels.limit ?? 0) - from) <=
        Math.abs((levels.stop ?? 0) - from)
        ? byLimit
        : byStop;
    }
    return byLimit ?? byStop;
  }

  private updateTrail(order: Order, price: number): void {
    if (order.kind !== "exit" || order.trailOffset === null) return;
    const lots = this.lots.filter(
      (lot) => order.fromEntry === null || lot.entryId === order.fromEntry,
    );
    if (lots.length === 0) return;
    const direction = lots[0].direction;
    const entry = toNumber(lots[0].price);
    const activation =
      order.trailPrice ??
      (order.trailPoints === null
        ? entry
        : entry + direction * order.trailPoints * this.tick);
    const better = (a: number, b: number) => (direction === 1 ? a > b : a < b);
    if (order.trailBest === null) {
      if (direction === 1 ? price >= activation : price <= activation)
        order.trailBest = price;
    } else if (better(price, order.trailBest)) {
      order.trailBest = price;
    }
  }

  private execute(order: Order, fill: Fill, time: number, bar: number): void {
    if (order.kind === "entry" || order.kind === "order") {
      this.fillEntry(order, fill.price, time, bar);
    } else {
      this.exitLots(order, fill.price, time);
    }
  }

  /** Fills orders placed on earlier bars, along this bar's assumed path. */
  beforeBar(bar: number, data: OhlcvBar): void {
    this.barFills = [];
    const upFirst =
      Math.abs(data.high - data.open) <= Math.abs(data.open - data.low);
    const path = upFirst
      ? [data.open, data.high, data.low, data.close]
      : [data.open, data.low, data.high, data.close];
    let insideFills = 0;
    const guard = { count: 0 };
    for (let at = 0; at < 3; at += 1) {
      const from = at === 0 ? path[0] : path[at];
      const to = at === 0 ? path[0] : path[at + 1];
      for (;;) {
        if ((guard.count += 1) > 1000) break;
        const live = this.orders.filter(
          (order) => order.placedBar < bar && !order.onClose,
        );
        for (const order of live) this.updateTrail(order, from);
        let best: { order: Order; fill: Fill } | null = null;
        for (const order of live) {
          const fill = this.trigger(order, from, to, at);
          if (!fill) continue;
          const distance = Math.abs(fill.price - from);
          if (!best || distance < Math.abs(best.fill.price - from))
            best = { order, fill };
        }
        if (!best) break;
        const { order, fill } = best;
        if (order.kind !== "exit")
          this.orders = this.orders.filter((item) => item !== order);
        const before = this.barFills.length;
        this.execute(order, fill, data.time, bar);
        if (order.kind === "exit" && this.barFills.length > before) {
          this.orders = this.orders.filter((item) => item !== order);
        }
        if (at > 0 && this.barFills.length > before) insideFills += 1;
        if (this.barFills.length === before) {
          // Not filled (e.g. pyramiding is full): drop it rather than retrying.
          this.orders = this.orders.filter((item) => item !== order);
        }
      }
      if (at > 0) for (const order of this.orders) this.updateTrail(order, to);
    }
    // Exits whose entries are gone, and closes with nothing to close, are dropped.
    this.orders = this.orders.filter(
      (order) =>
        (order.kind !== "exit" && order.kind !== "close") ||
        this.lots.length > 0 ||
        this.orders.some(
          (other) => other.kind === "entry" || other.kind === "order",
        ),
    );
    if (insideFills >= 2) this.markAssumed();
  }

  private markAssumed(): void {
    for (const fill of this.barFills) {
      if (fill.mark >= 0 && !this.marks[fill.mark].assumed) {
        this.marks[fill.mark].assumed = true;
        this.assumedFills += 1;
      }
      if (fill.trade) fill.trade.assumed = true;
      if (fill.lot) fill.lot.assumed = true;
    }
  }

  /** Fills market orders due at this bar's close, then marks equity and drawdown. */
  afterBar(bar: number, data: OhlcvBar): void {
    const due = this.orders.filter(
      (order) => order.onClose && order.placedBar === bar,
    );
    for (const order of due) {
      this.orders = this.orders.filter((item) => item !== order);
      this.execute(order, { price: data.close, at: 3 }, data.time, bar);
    }
    // Market orders placed on close but not filled wait for the next bar's open.
    for (const order of this.orders) {
      if (order.onClose && order.placedBar < bar) order.onClose = false;
    }
    this.lastClose = data.close;
    const equity = this.equityAt(data.close);
    if (equity > this.peak) this.peak = equity;
    const drawdown = this.peak - equity;
    if (drawdown > this.maxDrawdown) this.maxDrawdown = drawdown;
  }

  report(): StrategyReport {
    const initial = fx(this.settings.initialCapital);
    const closed = this.closed.length;
    const openProfit = this.openProfitAt(this.lastClose);
    const qty = this.positionQty();
    return {
      currency: this.settings.currency,
      initialCapital: toText(initial),
      netProfit: toText(this.netProfit),
      netProfitPercent:
        initial === 0n
          ? "0.00"
          : toText(roundDiv(this.netProfit * 100n * SCALE, initial)),
      grossProfit: toText(this.grossProfit),
      grossLoss: toText(this.grossLoss),
      profitFactor:
        this.grossLoss === 0n
          ? null
          : toText(div(this.grossProfit, this.grossLoss), 3),
      closedTrades: closed,
      winTrades: this.wins,
      lossTrades: this.losses,
      winRate:
        closed === 0
          ? null
          : toText(roundDiv(BigInt(this.wins) * 100n * SCALE, BigInt(closed))),
      maxDrawdown: toText(this.maxDrawdown),
      commission: toText(this.commissionTotal),
      openPosition:
        qty === 0n
          ? null
          : {
              size: String(this.positionSize),
              avgPrice: (this.positionAvgPrice ?? 0).toFixed(this.decimals),
              openProfit: toText(openProfit),
            },
      trades: [
        ...this.closed,
        ...this.lots.map((lot) => ({
          entryId: lot.entryId,
          exitId: null,
          direction:
            lot.direction === 1 ? ("long" as const) : ("short" as const),
          qty: toText(lot.qty, 4).replace(/\.?0+$/, ""),
          entryTime: lot.time,
          entryPrice: toText(lot.price, this.decimals),
          exitTime: null,
          exitPrice: null,
          profit: toText(
            mul(lot.qty, fx(this.lastClose) - lot.price) *
              BigInt(lot.direction),
          ),
          commission: toText(lot.commission),
          assumed: lot.assumed,
        })),
      ],
      assumedFills: this.assumedFills,
      skippedOrders: this.skipped,
    };
  }
}
