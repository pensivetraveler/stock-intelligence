/**
 * Pine drawings (ADR 0040): `line.*`, `label.*`, `box.*`, `table.*` and `linefill.*`
 * objects a script creates and changes as it runs, plus what the chart draws for
 * `plotshape`, `bgcolor`, `fill` and strategy fills. A script sees its own objects
 * only; what is drawn is their state after the last bar, as on TradingView.
 */
import {
  num,
  PineArray,
  PinePoint,
  PineRef,
  PineValueError,
  type Value,
} from "./pine-objects";

// --- what the chart draws -----------------------------------------------------------

export type Extend = "none" | "left" | "right" | "both";
export type StrokeStyle =
  "solid" | "dashed" | "dotted" | "arrow_left" | "arrow_right" | "arrow_both";

/** Times are epoch seconds; a time past the last bar is extrapolated by bar length. */
export interface PineLine {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  extend: Extend;
  color: string;
  width: number;
  style: StrokeStyle;
}

export interface PineLabel {
  time: number;
  price: number;
  /** "above"/"below": `yloc.abovebar`/`belowbar`, placed off the bar's high or low. */
  yloc: "price" | "above" | "below";
  text: string;
  style: string;
  color: string;
  textColor: string;
  size: number;
  align: "left" | "center" | "right";
}

export interface PineBox {
  left: number;
  right: number;
  top: number;
  bottom: number;
  extend: Extend;
  borderColor: string;
  borderWidth: number;
  borderStyle: StrokeStyle;
  bgColor: string;
  text: string;
  textColor: string;
  textSize: number;
  halign: "left" | "center" | "right";
  valign: "top" | "center" | "bottom";
}

export interface PineTableCell {
  column: number;
  row: number;
  text: string;
  textColor: string;
  bgColor: string | null;
  size: number;
  halign: "left" | "center" | "right";
}

export interface PineTable {
  position: string;
  columns: number;
  rows: number;
  bgColor: string | null;
  frameColor: string | null;
  frameWidth: number;
  borderColor: string | null;
  borderWidth: number;
  cells: PineTableCell[];
}

export interface PineShape {
  time: number;
  /** The price the shape is placed by: the bar's high/low, the value, or none. */
  price: number | null;
  location: "above" | "below" | "absolute" | "top" | "bottom";
  shape: string;
  text: string;
  color: string;
  textColor: string;
  size: number;
}

export interface PineFillPoint {
  time: number;
  y1: number;
  y2: number;
  color: string;
}

export type PineFill =
  | { kind: "series"; points: PineFillPoint[] }
  | { kind: "band"; y1: number; y2: number; color: string };

/** A strategy fill on the chart: a backtest fill, never an order (ADR 0040). */
export interface PineTradeMark {
  time: number;
  price: number;
  side: "buy" | "sell";
  label: string;
  /** The fill depended on an assumed order of prices inside the bar. */
  assumed: boolean;
}

export interface PineGraphics {
  backgrounds: { time: number; color: string }[];
  barColors: { time: number; color: string }[];
  fills: PineFill[];
  shapes: PineShape[];
  lines: PineLine[];
  labels: PineLabel[];
  boxes: PineBox[];
  linefills: { a: PineLine; b: PineLine; color: string }[];
  tables: PineTable[];
  trades: PineTradeMark[];
}

export const emptyGraphics = (): PineGraphics => ({
  backgrounds: [],
  barColors: [],
  fills: [],
  shapes: [],
  lines: [],
  labels: [],
  boxes: [],
  linefills: [],
  tables: [],
  trades: [],
});

/** The same drawings with every time passed through `map` (e.g. to another timeframe). */
export function mapGraphicsTimes(
  graphics: PineGraphics,
  map: (time: number) => number,
): PineGraphics {
  return {
    backgrounds: graphics.backgrounds.map((item) => ({
      ...item,
      time: map(item.time),
    })),
    barColors: graphics.barColors.map((item) => ({
      ...item,
      time: map(item.time),
    })),
    fills: graphics.fills.map((fill) =>
      fill.kind === "series"
        ? {
            ...fill,
            points: fill.points.map((point) => ({
              ...point,
              time: map(point.time),
            })),
          }
        : fill,
    ),
    shapes: graphics.shapes.map((item) => ({ ...item, time: map(item.time) })),
    lines: graphics.lines.map((item) => ({
      ...item,
      x1: map(item.x1),
      x2: map(item.x2),
    })),
    labels: graphics.labels.map((item) => ({ ...item, time: map(item.time) })),
    boxes: graphics.boxes.map((item) => ({
      ...item,
      left: map(item.left),
      right: map(item.right),
    })),
    linefills: graphics.linefills.map((item) => ({
      ...item,
      a: { ...item.a, x1: map(item.a.x1), x2: map(item.a.x2) },
      b: { ...item.b, x1: map(item.b.x1), x2: map(item.b.x2) },
    })),
    tables: graphics.tables,
    trades: graphics.trades.map((item) => ({ ...item, time: map(item.time) })),
  };
}

export const hasGraphics = (graphics: PineGraphics): boolean =>
  Object.values(graphics).some((list) => list.length > 0);

/** Font sizes in pixels for `size.*`. */
export function sizeOf(value: Value | undefined, fallback = 12): number {
  switch (value) {
    case "size.tiny":
      return 8;
    case "size.small":
      return 10;
    case "size.normal":
      return 12;
    case "size.large":
      return 15;
    case "size.huge":
      return 20;
    case "size.auto":
      return fallback;
    default: {
      const pixels = num(value ?? null);
      return pixels !== null && pixels > 0 ? Math.min(pixels, 40) : fallback;
    }
  }
}

// --- objects a script holds ---------------------------------------------------------

type XLoc = "bar_index" | "bar_time";

interface LineObject {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  xloc: XLoc;
  extend: Extend;
  color: string;
  width: number;
  style: StrokeStyle;
}

interface LabelObject {
  x: number;
  y: number;
  xloc: XLoc;
  yloc: "price" | "above" | "below";
  text: string;
  style: string;
  color: string;
  textColor: string;
  size: number;
  align: "left" | "center" | "right";
}

interface BoxObject {
  left: number;
  top: number;
  right: number;
  bottom: number;
  xloc: XLoc;
  extend: Extend;
  borderColor: string;
  borderWidth: number;
  borderStyle: StrokeStyle;
  bgColor: string;
  text: string;
  textColor: string;
  textSize: number;
  halign: "left" | "center" | "right";
  valign: "top" | "center" | "bottom";
}

interface TableObject {
  position: string;
  columns: number;
  rows: number;
  bgColor: string | null;
  frameColor: string | null;
  frameWidth: number;
  borderColor: string | null;
  borderWidth: number;
  cells: Map<string, PineTableCell>;
}

interface LinefillObject {
  line1: number;
  line2: number;
  color: string;
}

/** How many of each drawing a script may keep; older ones are deleted, as on TradingView. */
export interface DrawingLimits {
  line: number;
  label: number;
  box: number;
}

const BLUE = "#2962ff";

const fail = (message: string): never => {
  throw new PineValueError(message);
};

const str = (value: Value | undefined, fallback = ""): string =>
  typeof value === "string" ? value : fallback;
const colorOf = (value: Value | undefined, fallback: string): string =>
  typeof value === "string"
    ? value
    : value === null
      ? "rgba(0, 0, 0, 0)"
      : fallback;
const numberOf = (value: Value | undefined, what: string): number => {
  const n = num(value ?? null);
  return n === null ? fail(`${what} is na`) : n;
};

function extendOf(value: Value | undefined): Extend {
  switch (value) {
    case "extend.left":
      return "left";
    case "extend.right":
      return "right";
    case "extend.both":
      return "both";
    default:
      return "none";
  }
}

function styleOf(value: Value | undefined): StrokeStyle {
  const name = str(value).replace(/^(line|hline)\.style_/, "");
  return (
    (
      ["dashed", "dotted", "arrow_left", "arrow_right", "arrow_both"] as const
    ).find((item) => item === name) ?? "solid"
  );
}

function yloc(value: Value | undefined): "price" | "above" | "below" {
  return value === "yloc.abovebar"
    ? "above"
    : value === "yloc.belowbar"
      ? "below"
      : "price";
}

function halign(value: Value | undefined): "left" | "center" | "right" {
  const name = str(value);
  return name.endsWith("left")
    ? "left"
    : name.endsWith("right")
      ? "right"
      : "center";
}

function valign(value: Value | undefined): "top" | "center" | "bottom" {
  const name = str(value);
  return name.endsWith("top")
    ? "top"
    : name.endsWith("bottom")
      ? "bottom"
      : "center";
}

const xlocOf = (value: Value | undefined): XLoc =>
  value === "xloc.bar_time" ? "bar_time" : "bar_index";

/** A point's x in the given xloc (`chart.point` carries both an index and a time). */
function pointX(point: PinePoint, xloc: XLoc): number {
  const x = xloc === "bar_time" ? point.time : point.index;
  return x === null
    ? fail("the chart point has no " + (xloc === "bar_time" ? "time" : "index"))
    : x;
}

/** Objects created by a script, by id, oldest first per kind. */
export class DrawingStore {
  private nextId = 1;
  private readonly lines = new Map<number, LineObject>();
  private readonly labels = new Map<number, LabelObject>();
  private readonly boxes = new Map<number, BoxObject>();
  private readonly tables = new Map<number, TableObject>();
  private readonly linefills = new Map<number, LinefillObject>();

  constructor(private readonly limits: DrawingLimits) {}

  private add<T>(
    map: Map<number, T>,
    kind: PineRef["kind"],
    item: T,
    limit: number,
  ): PineRef {
    const id = this.nextId++;
    map.set(id, item);
    while (map.size > limit) {
      const oldest = map.keys().next().value as number;
      map.delete(oldest);
    }
    return new PineRef(kind, id);
  }

  private of<T>(
    map: Map<number, T>,
    ref: Value | undefined,
    kind: string,
  ): T | null {
    if (ref === null || ref === undefined) return null; // na: TradingView ignores the call
    if (!(ref instanceof PineRef) || ref.kind !== kind)
      return fail(`expected a ${kind}`);
    return map.get(ref.id) ?? null; // deleted: ignored
  }

  /** Runs `line.*`, `label.*`, `box.*`, `table.*`, `linefill.*`; undefined if not one. */
  call(
    name: string,
    args: Value[],
    bar: number,
    time: number,
  ): Value | undefined {
    const dot = name.indexOf(".");
    const kind = name.slice(0, dot);
    const method = name.slice(dot + 1);
    switch (kind) {
      case "line":
        return this.line(method, args);
      case "label":
        return this.label(method, args, bar, time);
      case "box":
        return this.box(method, args);
      case "table":
        return this.table(method, args);
      case "linefill":
        return this.linefill(method, args);
      default:
        return undefined;
    }
  }

  private line(method: string, a: Value[]): Value | undefined {
    if (method === "new") {
      let item: LineObject;
      if (a[0] instanceof PinePoint && a[1] instanceof PinePoint) {
        // line.new(first_point, second_point, xloc, extend, color, style, width)
        const xloc = xlocOf(a[2]);
        item = {
          x1: pointX(a[0], xloc),
          y1: numberOf(a[0].price, "the point's price"),
          x2: pointX(a[1], xloc),
          y2: numberOf(a[1].price, "the point's price"),
          xloc,
          extend: extendOf(a[3]),
          color: colorOf(a[4], BLUE),
          style: styleOf(a[5]),
          width: num(a[6] ?? null) ?? 1,
        };
      } else {
        item = {
          x1: numberOf(a[0], "x1"),
          y1: numberOf(a[1], "y1"),
          x2: numberOf(a[2], "x2"),
          y2: numberOf(a[3], "y2"),
          xloc: xlocOf(a[4]),
          extend: extendOf(a[5]),
          color: colorOf(a[6], BLUE),
          style: styleOf(a[7]),
          width: num(a[8] ?? null) ?? 1,
        };
      }
      return this.add(this.lines, "line", item, this.limits.line);
    }
    if (method === "all") {
      return new PineArray(
        [...this.lines.keys()].map((id) => new PineRef("line", id)),
      );
    }
    const item = this.of(this.lines, a[0], "line");
    if (method === "delete") {
      if (a[0] instanceof PineRef) this.lines.delete(a[0].id);
      return null;
    }
    if (!item) return null;
    switch (method) {
      case "copy":
        return this.add(this.lines, "line", { ...item }, this.limits.line);
      case "set_x1":
        item.x1 = numberOf(a[1], "x1");
        return null;
      case "set_x2":
        item.x2 = numberOf(a[1], "x2");
        return null;
      case "set_y1":
        item.y1 = numberOf(a[1], "y1");
        return null;
      case "set_y2":
        item.y2 = numberOf(a[1], "y2");
        return null;
      case "set_xy1":
        item.x1 = numberOf(a[1], "x");
        item.y1 = numberOf(a[2], "y");
        return null;
      case "set_xy2":
        item.x2 = numberOf(a[1], "x");
        item.y2 = numberOf(a[2], "y");
        return null;
      case "set_first_point":
        if (a[1] instanceof PinePoint) {
          item.x1 = pointX(a[1], item.xloc);
          item.y1 = numberOf(a[1].price, "the point's price");
        }
        return null;
      case "set_second_point":
        if (a[1] instanceof PinePoint) {
          item.x2 = pointX(a[1], item.xloc);
          item.y2 = numberOf(a[1].price, "the point's price");
        }
        return null;
      case "set_xloc":
        item.x1 = numberOf(a[1], "x1");
        item.x2 = numberOf(a[2], "x2");
        item.xloc = xlocOf(a[3]);
        return null;
      case "set_extend":
        item.extend = extendOf(a[1]);
        return null;
      case "set_color":
        item.color = colorOf(a[1], item.color);
        return null;
      case "set_style":
        item.style = styleOf(a[1]);
        return null;
      case "set_width":
        item.width = num(a[1] ?? null) ?? item.width;
        return null;
      case "get_x1":
        return item.x1;
      case "get_x2":
        return item.x2;
      case "get_y1":
        return item.y1;
      case "get_y2":
        return item.y2;
      case "get_price": {
        const x = numberOf(a[1], "x");
        if (item.x2 === item.x1) return item.y1;
        return (
          item.y1 + ((item.y2 - item.y1) * (x - item.x1)) / (item.x2 - item.x1)
        );
      }
      default:
        return fail(`line.${method}() is not supported`);
    }
  }

  private label(
    method: string,
    a: Value[],
    bar: number,
    time: number,
  ): Value | undefined {
    if (method === "new") {
      let item: LabelObject;
      if (a[0] instanceof PinePoint) {
        // label.new(point, text, xloc, yloc, color, style, textcolor, size, textalign)
        const xloc = xlocOf(a[2]);
        item = {
          x: pointX(a[0], xloc),
          y: num(a[0].price) ?? 0,
          xloc,
          yloc: yloc(a[3]),
          text: str(a[1]),
          color: colorOf(a[4], BLUE),
          style: str(a[5], "label.style_label_down"),
          textColor: colorOf(a[6], "#ffffff"),
          size: sizeOf(a[7]),
          align: halign(a[8]),
        };
      } else {
        const xloc = xlocOf(a[3]);
        item = {
          x: num(a[0] ?? null) ?? (xloc === "bar_time" ? time : bar),
          y: num(a[1] ?? null) ?? 0,
          xloc,
          text: str(a[2]),
          yloc: yloc(a[4]),
          color: colorOf(a[5], BLUE),
          style: str(a[6], "label.style_label_down"),
          textColor: colorOf(a[7], "#ffffff"),
          size: sizeOf(a[8]),
          align: halign(a[9]),
        };
      }
      return this.add(this.labels, "label", item, this.limits.label);
    }
    if (method === "all") {
      return new PineArray(
        [...this.labels.keys()].map((id) => new PineRef("label", id)),
      );
    }
    if (method === "delete") {
      if (a[0] instanceof PineRef) this.labels.delete(a[0].id);
      return null;
    }
    const item = this.of(this.labels, a[0], "label");
    if (!item) return null;
    switch (method) {
      case "copy":
        return this.add(this.labels, "label", { ...item }, this.limits.label);
      case "set_x":
        item.x = numberOf(a[1], "x");
        return null;
      case "set_y":
        item.y = numberOf(a[1], "y");
        return null;
      case "set_xy":
        item.x = numberOf(a[1], "x");
        item.y = numberOf(a[2], "y");
        return null;
      case "set_point":
        if (a[1] instanceof PinePoint) {
          item.x = pointX(a[1], item.xloc);
          item.y = num(a[1].price) ?? item.y;
        }
        return null;
      case "set_xloc":
        item.x = numberOf(a[1], "x");
        item.xloc = xlocOf(a[2]);
        return null;
      case "set_yloc":
        item.yloc = yloc(a[1]);
        return null;
      case "set_text":
        item.text = str(a[1]);
        return null;
      case "set_color":
        item.color = colorOf(a[1], item.color);
        return null;
      case "set_textcolor":
        item.textColor = colorOf(a[1], item.textColor);
        return null;
      case "set_style":
        item.style = str(a[1], item.style);
        return null;
      case "set_size":
        item.size = sizeOf(a[1]);
        return null;
      case "set_textalign":
        item.align = halign(a[1]);
        return null;
      case "set_tooltip":
      case "set_text_font_family":
        return null;
      case "get_x":
        return item.x;
      case "get_y":
        return item.y;
      case "get_text":
        return item.text;
      default:
        return fail(`label.${method}() is not supported`);
    }
  }

  private box(method: string, a: Value[]): Value | undefined {
    if (method === "new") {
      let item: BoxObject;
      const style = (offset: number, xlocAt: number) => ({
        borderColor: colorOf(a[offset], BLUE),
        borderWidth: num(a[offset + 1] ?? null) ?? 1,
        borderStyle: styleOf(a[offset + 2]),
        extend: extendOf(a[offset + 3]),
        xloc: xlocOf(a[xlocAt]),
        bgColor: colorOf(a[offset + 5], "rgba(41, 98, 255, 0.2)"),
        text: str(a[offset + 6]),
        textSize: sizeOf(a[offset + 7]),
        textColor: colorOf(a[offset + 8], "#ffffff"),
        halign: halign(a[offset + 9]),
        valign: valign(a[offset + 10]),
      });
      if (a[0] instanceof PinePoint && a[1] instanceof PinePoint) {
        // box.new(top_left, bottom_right, border_color, …)
        const look = style(2, 6);
        item = {
          left: pointX(a[0], look.xloc),
          top: numberOf(a[0].price, "top"),
          right: pointX(a[1], look.xloc),
          bottom: numberOf(a[1].price, "bottom"),
          ...look,
        };
      } else {
        item = {
          left: numberOf(a[0], "left"),
          top: numberOf(a[1], "top"),
          right: numberOf(a[2], "right"),
          bottom: numberOf(a[3], "bottom"),
          ...style(4, 8),
        };
      }
      return this.add(this.boxes, "box", item, this.limits.box);
    }
    if (method === "all") {
      return new PineArray(
        [...this.boxes.keys()].map((id) => new PineRef("box", id)),
      );
    }
    if (method === "delete") {
      if (a[0] instanceof PineRef) this.boxes.delete(a[0].id);
      return null;
    }
    const item = this.of(this.boxes, a[0], "box");
    if (!item) return null;
    switch (method) {
      case "copy":
        return this.add(this.boxes, "box", { ...item }, this.limits.box);
      case "set_left":
        item.left = numberOf(a[1], "left");
        return null;
      case "set_right":
        item.right = numberOf(a[1], "right");
        return null;
      case "set_top":
        item.top = numberOf(a[1], "top");
        return null;
      case "set_bottom":
        item.bottom = numberOf(a[1], "bottom");
        return null;
      case "set_lefttop":
        item.left = numberOf(a[1], "left");
        item.top = numberOf(a[2], "top");
        return null;
      case "set_rightbottom":
        item.right = numberOf(a[1], "right");
        item.bottom = numberOf(a[2], "bottom");
        return null;
      case "set_top_left_point":
        if (a[1] instanceof PinePoint) {
          item.left = pointX(a[1], item.xloc);
          item.top = numberOf(a[1].price, "top");
        }
        return null;
      case "set_bottom_right_point":
        if (a[1] instanceof PinePoint) {
          item.right = pointX(a[1], item.xloc);
          item.bottom = numberOf(a[1].price, "bottom");
        }
        return null;
      case "set_border_color":
        item.borderColor = colorOf(a[1], item.borderColor);
        return null;
      case "set_border_width":
        item.borderWidth = num(a[1] ?? null) ?? item.borderWidth;
        return null;
      case "set_border_style":
        item.borderStyle = styleOf(a[1]);
        return null;
      case "set_bgcolor":
        item.bgColor = colorOf(a[1], item.bgColor);
        return null;
      case "set_extend":
        item.extend = extendOf(a[1]);
        return null;
      case "set_text":
        item.text = str(a[1]);
        return null;
      case "set_text_color":
        item.textColor = colorOf(a[1], item.textColor);
        return null;
      case "set_text_size":
        item.textSize = sizeOf(a[1]);
        return null;
      case "set_text_halign":
        item.halign = halign(a[1]);
        return null;
      case "set_text_valign":
        item.valign = valign(a[1]);
        return null;
      case "set_text_wrap":
      case "set_text_font_family":
      case "set_xloc":
        return null;
      case "get_left":
        return item.left;
      case "get_right":
        return item.right;
      case "get_top":
        return item.top;
      case "get_bottom":
        return item.bottom;
      default:
        return fail(`box.${method}() is not supported`);
    }
  }

  private table(method: string, a: Value[]): Value | undefined {
    if (method === "new") {
      const columns = numberOf(a[1], "columns");
      const rows = numberOf(a[2], "rows");
      if (columns < 1 || rows < 1 || columns * rows > 1000) {
        return fail("a table has 1 to 1000 cells");
      }
      const table: TableObject = {
        position: str(a[0], "position.top_right"),
        columns,
        rows,
        bgColor: typeof a[3] === "string" ? a[3] : null,
        frameColor: typeof a[4] === "string" ? a[4] : null,
        frameWidth: num(a[5] ?? null) ?? 0,
        borderColor: typeof a[6] === "string" ? a[6] : null,
        borderWidth: num(a[7] ?? null) ?? 0,
        cells: new Map(),
      };
      // One table per position, as on TradingView.
      for (const [id, other] of this.tables) {
        if (other.position === table.position) this.tables.delete(id);
      }
      return this.add(this.tables, "table", table, 40);
    }
    if (method === "all") {
      return new PineArray(
        [...this.tables.keys()].map((id) => new PineRef("table", id)),
      );
    }
    if (method === "delete") {
      if (a[0] instanceof PineRef) this.tables.delete(a[0].id);
      return null;
    }
    const table = this.of(this.tables, a[0], "table");
    if (!table) return null;
    const cellAt = (
      columnValue: Value | undefined,
      rowValue: Value | undefined,
    ) => {
      const column = numberOf(columnValue, "column");
      const row = numberOf(rowValue, "row");
      if (
        column < 0 ||
        column >= table.columns ||
        row < 0 ||
        row >= table.rows
      ) {
        return fail(
          `cell (${column}, ${row}) is outside the ${table.columns}×${table.rows} table`,
        );
      }
      const key = `${column},${row}`;
      let cell = table.cells.get(key);
      if (!cell) {
        cell = {
          column,
          row,
          text: "",
          textColor: "#d1d4dc",
          bgColor: null,
          size: 12,
          halign: "center",
        };
        table.cells.set(key, cell);
      }
      return cell;
    };
    switch (method) {
      case "cell": {
        // table.cell(table_id, column, row, text, width, height, text_color,
        //            text_halign, text_valign, text_size, bgcolor)
        const cell = cellAt(a[1], a[2]);
        cell.text = str(a[3]);
        if (typeof a[6] === "string") cell.textColor = a[6];
        cell.halign = a[7] === undefined ? cell.halign : halign(a[7]);
        if (a[9] !== undefined) cell.size = sizeOf(a[9]);
        cell.bgColor = typeof a[10] === "string" ? a[10] : cell.bgColor;
        return null;
      }
      case "cell_set_text":
        cellAt(a[1], a[2]).text = str(a[3]);
        return null;
      case "cell_set_text_color":
        cellAt(a[1], a[2]).textColor = colorOf(a[3], "#d1d4dc");
        return null;
      case "cell_set_bgcolor": {
        const cell = cellAt(a[1], a[2]);
        cell.bgColor = typeof a[3] === "string" ? a[3] : null;
        return null;
      }
      case "cell_set_text_size":
        cellAt(a[1], a[2]).size = sizeOf(a[3]);
        return null;
      case "cell_set_text_halign":
        cellAt(a[1], a[2]).halign = halign(a[3]);
        return null;
      case "cell_set_text_valign":
      case "cell_set_width":
      case "cell_set_height":
      case "cell_set_tooltip":
      case "cell_set_text_font_family":
      case "merge_cells":
        return null;
      case "clear":
        table.cells.clear();
        return null;
      case "set_position":
        table.position = str(a[1], table.position);
        return null;
      case "set_bgcolor":
        table.bgColor = typeof a[1] === "string" ? a[1] : null;
        return null;
      case "set_frame_color":
        table.frameColor = typeof a[1] === "string" ? a[1] : null;
        return null;
      case "set_frame_width":
        table.frameWidth = num(a[1] ?? null) ?? table.frameWidth;
        return null;
      case "set_border_color":
        table.borderColor = typeof a[1] === "string" ? a[1] : null;
        return null;
      case "set_border_width":
        table.borderWidth = num(a[1] ?? null) ?? table.borderWidth;
        return null;
      default:
        return fail(`table.${method}() is not supported`);
    }
  }

  private linefill(method: string, a: Value[]): Value | undefined {
    if (method === "new") {
      if (!(a[0] instanceof PineRef) || !(a[1] instanceof PineRef)) return null;
      return this.add(
        this.linefills,
        "linefill",
        {
          line1: a[0].id,
          line2: a[1].id,
          color: colorOf(a[2], "rgba(41, 98, 255, 0.2)"),
        },
        500,
      );
    }
    if (method === "all") {
      return new PineArray(
        [...this.linefills.keys()].map((id) => new PineRef("linefill", id)),
      );
    }
    if (method === "delete") {
      if (a[0] instanceof PineRef) this.linefills.delete(a[0].id);
      return null;
    }
    const item = this.of(this.linefills, a[0], "linefill");
    if (!item) return null;
    switch (method) {
      case "set_color":
        item.color = colorOf(a[1], item.color);
        return null;
      case "get_line1":
        return new PineRef("line", item.line1);
      case "get_line2":
        return new PineRef("line", item.line2);
      default:
        return fail(`linefill.${method}() is not supported`);
    }
  }

  /**
   * The objects as the chart draws them. `timeOf` turns an x (a bar index or a time in
   * ms) into epoch seconds; `barAt` gives the bar for `yloc.abovebar`/`belowbar`.
   */
  snapshot(
    timeOf: (x: number, xloc: XLoc) => number,
    barAt: (time: number) => { high: number; low: number } | null,
  ): Pick<PineGraphics, "lines" | "labels" | "boxes" | "linefills" | "tables"> {
    const lineOf = (item: LineObject): PineLine => ({
      x1: timeOf(item.x1, item.xloc),
      y1: item.y1,
      x2: timeOf(item.x2, item.xloc),
      y2: item.y2,
      extend: item.extend,
      color: item.color,
      width: item.width,
      style: item.style,
    });
    const lines = new Map(
      [...this.lines].map(([id, item]) => [id, lineOf(item)]),
    );
    return {
      lines: [...lines.values()],
      labels: [...this.labels.values()].map((item) => {
        const time = timeOf(item.x, item.xloc);
        const bar = item.yloc === "price" ? null : barAt(time);
        return {
          time,
          price: bar ? (item.yloc === "above" ? bar.high : bar.low) : item.y,
          yloc: item.yloc,
          text: item.text,
          style: item.style,
          color: item.color,
          textColor: item.textColor,
          size: item.size,
          align: item.align,
        };
      }),
      boxes: [...this.boxes.values()].map((item) => ({
        left: timeOf(item.left, item.xloc),
        right: timeOf(item.right, item.xloc),
        top: item.top,
        bottom: item.bottom,
        extend: item.extend,
        borderColor: item.borderColor,
        borderWidth: item.borderWidth,
        borderStyle: item.borderStyle,
        bgColor: item.bgColor,
        text: item.text,
        textColor: item.textColor,
        textSize: item.textSize,
        halign: item.halign,
        valign: item.valign,
      })),
      linefills: [...this.linefills.values()].flatMap((item) => {
        const a = lines.get(item.line1);
        const b = lines.get(item.line2);
        return a && b ? [{ a, b, color: item.color }] : [];
      }),
      tables: [...this.tables.values()].map((table) => ({
        position: table.position,
        columns: table.columns,
        rows: table.rows,
        bgColor: table.bgColor,
        frameColor: table.frameColor,
        frameWidth: table.frameWidth,
        borderColor: table.borderColor,
        borderWidth: table.borderWidth,
        cells: [...table.cells.values()],
      })),
    };
  }
}
