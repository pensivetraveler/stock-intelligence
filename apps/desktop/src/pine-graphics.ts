/**
 * Draws a Pine script's drawings (ADR 0040) on the chart: backgrounds and fills under
 * the plots; lines, boxes, labels, shapes, tables and strategy fills above them. It
 * is a lightweight-charts series primitive, so it scrolls and zooms with the pane of
 * the series it is attached to.
 */
import type {
  AutoscaleInfo,
  IChartApiBase,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  SeriesType,
  Time,
  UTCTimestamp,
} from "lightweight-charts";

import type {
  Extend,
  PineGraphics,
  PineLabel,
  PineShape,
  PineTable,
  StrokeStyle,
} from "./pine-drawings";

type Target = Parameters<IPrimitivePaneRenderer["draw"]>[0];
type Context = CanvasRenderingContext2D;

const FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
const BUY = "#2962ff";
const SELL = "#e91e63";

interface Geometry {
  x: (time: number) => number | null;
  y: (price: number) => number | null;
  spacing: number;
  width: number;
  height: number;
}

function dash(context: Context, style: StrokeStyle, width: number): void {
  if (style === "dashed") context.setLineDash([6 * width, 4 * width]);
  else if (style === "dotted") context.setLineDash([width, 3 * width]);
  else context.setLineDash([]);
}

function arrowHead(
  context: Context,
  fromX: number,
  fromY: number,
  toX: number,
  toY: number,
  size: number,
) {
  const angle = Math.atan2(toY - fromY, toX - fromX);
  context.beginPath();
  context.moveTo(toX, toY);
  context.lineTo(
    toX - size * Math.cos(angle - Math.PI / 7),
    toY - size * Math.sin(angle - Math.PI / 7),
  );
  context.moveTo(toX, toY);
  context.lineTo(
    toX - size * Math.cos(angle + Math.PI / 7),
    toY - size * Math.sin(angle + Math.PI / 7),
  );
  context.stroke();
}

/** A segment stretched to the pane's edges by `extend`. */
function extended(
  x1: number,
  y1: number,
  x2: number,
  y2: number,
  extend: Extend,
  width: number,
): [number, number, number, number] {
  if (extend === "none" || x1 === x2) return [x1, y1, x2, y2];
  const slope = (y2 - y1) / (x2 - x1);
  const [lx, ly, rx, ry] = x1 <= x2 ? [x1, y1, x2, y2] : [x2, y2, x1, y1];
  const atX = (x: number) => ly + slope * (x - lx);
  const left = extend === "left" || extend === "both" ? -10 : lx;
  const right = extend === "right" || extend === "both" ? width + 10 : rx;
  return [
    left,
    left === lx ? ly : atX(left),
    right,
    right === rx ? ry : atX(right),
  ];
}

function textLines(
  context: Context,
  text: string,
  x: number,
  top: number,
  size: number,
  align: CanvasTextAlign,
) {
  context.textAlign = align;
  context.textBaseline = "top";
  text
    .split("\n")
    .forEach((line, k) => context.fillText(line, x, top + k * (size + 2)));
}

function textBlock(
  context: Context,
  text: string,
  size: number,
): { width: number; height: number } {
  const lines = text.split("\n");
  context.font = `${size}px ${FONT}`;
  const width = Math.max(
    0,
    ...lines.map((line) => context.measureText(line).width),
  );
  return { width, height: lines.length * (size + 2) - 2 };
}

/** A shape centred at (x, y): `shape.*` names and label styles share these. */
function shape(
  context: Context,
  name: string,
  x: number,
  y: number,
  size: number,
  color: string,
) {
  const r = size / 2;
  context.fillStyle = color;
  context.strokeStyle = color;
  context.lineWidth = Math.max(1, size / 8);
  context.setLineDash([]);
  context.beginPath();
  switch (
    name.replace(/^(shape|label\.style)\./, "").replace(/^label\.style_/, "")
  ) {
    case "triangleup":
      context.moveTo(x, y - r);
      context.lineTo(x + r, y + r);
      context.lineTo(x - r, y + r);
      context.closePath();
      context.fill();
      return;
    case "triangledown":
      context.moveTo(x, y + r);
      context.lineTo(x + r, y - r);
      context.lineTo(x - r, y - r);
      context.closePath();
      context.fill();
      return;
    case "arrowup":
      context.moveTo(x, y - r);
      context.lineTo(x + r, y);
      context.lineTo(x + r / 3, y);
      context.lineTo(x + r / 3, y + r);
      context.lineTo(x - r / 3, y + r);
      context.lineTo(x - r / 3, y);
      context.lineTo(x - r, y);
      context.closePath();
      context.fill();
      return;
    case "arrowdown":
      context.moveTo(x, y + r);
      context.lineTo(x + r, y);
      context.lineTo(x + r / 3, y);
      context.lineTo(x + r / 3, y - r);
      context.lineTo(x - r / 3, y - r);
      context.lineTo(x - r / 3, y);
      context.lineTo(x - r, y);
      context.closePath();
      context.fill();
      return;
    case "circle":
      context.arc(x, y, r, 0, Math.PI * 2);
      context.fill();
      return;
    case "square":
      context.fillRect(x - r, y - r, size, size);
      return;
    case "diamond":
      context.moveTo(x, y - r);
      context.lineTo(x + r, y);
      context.lineTo(x, y + r);
      context.lineTo(x - r, y);
      context.closePath();
      context.fill();
      return;
    case "cross":
      context.moveTo(x - r, y);
      context.lineTo(x + r, y);
      context.moveTo(x, y - r);
      context.lineTo(x, y + r);
      context.stroke();
      return;
    case "flag":
      context.moveTo(x - r, y + r);
      context.lineTo(x - r, y - r);
      context.lineTo(x + r, y - r / 2);
      context.lineTo(x - r, y);
      context.stroke();
      context.fill();
      return;
    default:
      // xcross, and anything unknown
      context.moveTo(x - r, y - r);
      context.lineTo(x + r, y + r);
      context.moveTo(x + r, y - r);
      context.lineTo(x - r, y + r);
      context.stroke();
  }
}

function drawLabel(context: Context, label: PineLabel, g: Geometry) {
  const x = g.x(label.time);
  const base = g.y(label.price);
  if (x === null || base === null) return;
  const style = label.style.replace("label.style_", "");
  const size = label.size;
  const box = textBlock(context, label.text, size);
  const pad = 4;
  const w = box.width + pad * 2;
  const h = box.height + pad * 2;
  const pointer = 6;
  const gap = label.yloc === "price" ? 0 : 4;
  context.font = `${size}px ${FONT}`;
  if (style === "none" || style === "text_outline") {
    context.fillStyle = label.textColor;
    textLines(context, label.text, x, base - box.height / 2, size, label.align);
    return;
  }
  const shapes = [
    "xcross",
    "cross",
    "triangleup",
    "triangledown",
    "flag",
    "circle",
    "arrowup",
    "arrowdown",
    "square",
    "diamond",
  ];
  if (shapes.includes(style)) {
    const below =
      label.yloc === "below" || style === "triangleup" || style === "arrowup";
    const cy =
      label.yloc === "price"
        ? base
        : base + (below ? gap + size / 2 : -gap - size / 2);
    shape(context, style, x, cy, size, label.color);
    if (label.text) {
      context.fillStyle =
        label.textColor === "#ffffff" ? label.color : label.textColor;
      textLines(
        context,
        label.text,
        x,
        below ? cy + size / 2 + 2 : cy - size / 2 - 2 - box.height,
        size,
        "center",
      );
    }
    return;
  }
  // Bubbles: label_down sits above its point, label_up below, left/right beside it.
  let left = x - w / 2;
  let top: number;
  context.fillStyle = label.color;
  context.beginPath();
  if (
    style === "label_up" ||
    (label.yloc === "below" && style === "label_down")
  ) {
    top = base + gap + pointer;
    context.moveTo(x, base + gap);
    context.lineTo(x + pointer, top);
    context.lineTo(x - pointer, top);
  } else if (style === "label_left") {
    left = x + pointer;
    top = base - h / 2;
    context.moveTo(x, base);
    context.lineTo(left, base - pointer);
    context.lineTo(left, base + pointer);
  } else if (style === "label_right") {
    left = x - pointer - w;
    top = base - h / 2;
    context.moveTo(x, base);
    context.lineTo(left + w, base - pointer);
    context.lineTo(left + w, base + pointer);
  } else if (style === "label_center") {
    top = base - h / 2;
  } else {
    top = base - gap - pointer - h;
    context.moveTo(x, base - gap);
    context.lineTo(x + pointer, top + h);
    context.lineTo(x - pointer, top + h);
  }
  context.closePath();
  context.fill();
  context.beginPath();
  context.roundRect?.(left, top, w, h, 3);
  if (!context.roundRect) context.rect(left, top, w, h);
  context.fill();
  context.fillStyle = label.textColor;
  const textX =
    label.align === "left"
      ? left + pad
      : label.align === "right"
        ? left + w - pad
        : left + w / 2;
  textLines(context, label.text, textX, top + pad, size, label.align);
}

function drawShape(context: Context, item: PineShape, g: Geometry) {
  const x = g.x(item.time);
  if (x === null) return;
  const size = Math.max(6, item.size);
  let y: number | null;
  const gap = 4;
  switch (item.location) {
    case "top":
      y = gap + size / 2;
      break;
    case "bottom":
      y = g.height - gap - size / 2;
      break;
    default: {
      const at = item.price === null ? null : g.y(item.price);
      if (at === null) return;
      y =
        item.location === "above"
          ? at - gap - size / 2
          : item.location === "below"
            ? at + gap + size / 2
            : at;
    }
  }
  const below = item.location === "below" || item.location === "bottom";
  if (item.shape.startsWith("char:")) {
    context.font = `${size + 2}px ${FONT}`;
    context.fillStyle = item.color;
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.fillText(item.shape.slice(5), x, y);
  } else if (
    item.shape === "shape.labelup" ||
    item.shape === "shape.labeldown"
  ) {
    drawLabel(
      context,
      {
        time: item.time,
        price: item.price ?? 0,
        yloc: "price",
        text: item.text,
        style:
          item.shape === "shape.labelup"
            ? "label.style_label_up"
            : "label.style_label_down",
        color: item.color,
        textColor: item.textColor === item.color ? "#ffffff" : item.textColor,
        size: 11,
        align: "center",
      },
      { ...g, y: () => y },
    );
    return;
  } else {
    shape(context, item.shape, x, y, size, item.color);
  }
  if (item.text) {
    context.font = `11px ${FONT}`;
    context.fillStyle = item.textColor;
    const box = textBlock(context, item.text, 11);
    textLines(
      context,
      item.text,
      x,
      below ? y + size / 2 + 2 : y - size / 2 - 2 - box.height,
      11,
      "center",
    );
  }
}

function drawTable(context: Context, table: PineTable, g: Geometry) {
  const widths = Array.from({ length: table.columns }, () => 0);
  const heights = Array.from({ length: table.rows }, () => 0);
  const pad = 4;
  for (const cell of table.cells) {
    const box = textBlock(context, cell.text, cell.size);
    widths[cell.column] = Math.max(widths[cell.column], box.width + pad * 2);
    heights[cell.row] = Math.max(heights[cell.row], box.height + pad * 2);
  }
  const width = widths.reduce((a, b) => a + b, 0);
  const height = heights.reduce((a, b) => a + b, 0);
  if (width === 0 || height === 0) return;
  const margin = 8;
  const position = table.position.replace("position.", "");
  const left = position.endsWith("left")
    ? margin
    : position.endsWith("right")
      ? g.width - width - margin
      : (g.width - width) / 2;
  const top = position.startsWith("top")
    ? margin
    : position.startsWith("bottom")
      ? g.height - height - margin
      : (g.height - height) / 2;
  if (table.bgColor) {
    context.fillStyle = table.bgColor;
    context.fillRect(left, top, width, height);
  }
  const columnX = (column: number) =>
    left + widths.slice(0, column).reduce((a, b) => a + b, 0);
  const rowY = (row: number) =>
    top + heights.slice(0, row).reduce((a, b) => a + b, 0);
  for (const cell of table.cells) {
    const x = columnX(cell.column);
    const y = rowY(cell.row);
    const w = widths[cell.column];
    const h = heights[cell.row];
    if (cell.bgColor) {
      context.fillStyle = cell.bgColor;
      context.fillRect(x, y, w, h);
    }
    context.font = `${cell.size}px ${FONT}`;
    context.fillStyle = cell.textColor;
    const textX =
      cell.halign === "left"
        ? x + pad
        : cell.halign === "right"
          ? x + w - pad
          : x + w / 2;
    textLines(context, cell.text, textX, y + pad, cell.size, cell.halign);
  }
  context.setLineDash([]);
  if (table.borderColor && table.borderWidth > 0) {
    context.strokeStyle = table.borderColor;
    context.lineWidth = table.borderWidth;
    for (const cell of table.cells) {
      context.strokeRect(
        columnX(cell.column),
        rowY(cell.row),
        widths[cell.column],
        heights[cell.row],
      );
    }
  }
  if (table.frameColor && table.frameWidth > 0) {
    context.strokeStyle = table.frameColor;
    context.lineWidth = table.frameWidth;
    context.strokeRect(left, top, width, height);
  }
}

class Renderer implements IPrimitivePaneRenderer {
  constructor(
    private readonly owner: PineGraphicsPrimitive,
    private readonly layer: "under" | "over",
  ) {}

  draw(target: Target): void {
    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      const g = this.owner.geometry(mediaSize.width, mediaSize.height);
      if (!g) return;
      context.save();
      try {
        if (this.layer === "under") this.owner.drawUnder(context, g);
        else this.owner.drawOver(context, g);
      } finally {
        context.restore();
      }
    });
  }
}

class View implements IPrimitivePaneView {
  private readonly drawer: Renderer;
  constructor(
    owner: PineGraphicsPrimitive,
    private readonly layer: "under" | "over",
  ) {
    this.drawer = new Renderer(owner, layer);
  }

  zOrder() {
    return this.layer === "under" ? ("bottom" as const) : ("top" as const);
  }

  renderer() {
    return this.drawer;
  }
}

export class PineGraphicsPrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApiBase<Time> | null = null;
  private series: ISeriesApi<SeriesType, Time> | null = null;
  private requestUpdate: (() => void) | null = null;
  private readonly views: IPrimitivePaneView[];

  /**
   * `times`: the chart's bar times, to place times between or beyond them;
   * `autoscale`: widen the price scale to the drawings (a pane with no plots).
   */
  constructor(
    private graphics: PineGraphics,
    private readonly times: () => number[],
    private readonly autoscale = false,
  ) {
    this.views = [new View(this, "under"), new View(this, "over")];
  }

  attached(param: SeriesAttachedParameter<Time, SeriesType>): void {
    this.chart = param.chart;
    this.series = param.series;
    this.requestUpdate = param.requestUpdate;
  }

  detached(): void {
    this.chart = null;
    this.series = null;
    this.requestUpdate = null;
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this.views;
  }

  autoscaleInfo(): AutoscaleInfo | null {
    if (!this.autoscale) return null;
    const prices = [
      ...this.graphics.lines.flatMap((line) => [line.y1, line.y2]),
      ...this.graphics.boxes.flatMap((box) => [box.top, box.bottom]),
      ...this.graphics.labels.map((label) => label.price),
      ...this.graphics.fills.flatMap((fill) =>
        fill.kind === "band"
          ? [fill.y1, fill.y2]
          : fill.points.flatMap((point) => [point.y1, point.y2]),
      ),
      ...this.graphics.shapes.flatMap((item) =>
        item.price === null ? [] : [item.price],
      ),
    ].filter((value) => Number.isFinite(value));
    if (prices.length === 0) return null;
    return {
      priceRange: {
        minValue: Math.min(...prices),
        maxValue: Math.max(...prices),
      },
    };
  }

  update(graphics: PineGraphics): void {
    this.graphics = graphics;
    this.requestUpdate?.();
  }

  /** Coordinates for drawing; times between bars are interpolated, later ones extrapolated. */
  geometry(width: number, height: number): Geometry | null {
    const chart = this.chart;
    const series = this.series;
    if (!chart || !series) return null;
    const scale = chart.timeScale();
    const spacing = scale.options().barSpacing;
    const times = this.times();
    const step = (() => {
      const gaps: number[] = [];
      for (let k = Math.max(1, times.length - 20); k < times.length; k += 1)
        gaps.push(times[k] - times[k - 1]);
      gaps.sort((a, b) => a - b);
      return gaps[Math.floor(gaps.length / 2)] || 60;
    })();
    const at = (time: number) => scale.timeToCoordinate(time as UTCTimestamp);
    const x = (time: number): number | null => {
      const exact = at(time);
      if (exact !== null) return exact;
      if (times.length === 0) return null;
      let low = 0;
      let high = times.length - 1;
      if (time > times[high]) {
        const last = at(times[high]);
        return last === null
          ? null
          : last + ((time - times[high]) / step) * spacing;
      }
      if (time < times[0]) {
        const first = at(times[0]);
        return first === null
          ? null
          : first - ((times[0] - time) / step) * spacing;
      }
      while (low + 1 < high) {
        const middle = (low + high) >> 1;
        if (times[middle] <= time) low = middle;
        else high = middle;
      }
      const a = at(times[low]);
      const b = at(times[high]);
      if (a === null || b === null) return a ?? b;
      return (
        a +
        ((b - a) * (time - times[low])) / Math.max(1, times[high] - times[low])
      );
    };
    const y = (price: number) => series.priceToCoordinate(price);
    return { x, y, spacing, width, height };
  }

  drawUnder(context: Context, g: Geometry): void {
    const { backgrounds, fills, linefills } = this.graphics;
    for (const item of backgrounds) {
      const x = g.x(item.time);
      if (x === null || x < -g.spacing || x > g.width + g.spacing) continue;
      context.fillStyle = item.color;
      context.fillRect(x - g.spacing / 2, 0, g.spacing, g.height);
    }
    for (const fill of fills) {
      if (fill.kind === "band") {
        const y1 = g.y(fill.y1);
        const y2 = g.y(fill.y2);
        if (y1 === null || y2 === null) continue;
        context.fillStyle = fill.color;
        context.fillRect(0, Math.min(y1, y2), g.width, Math.abs(y2 - y1));
        continue;
      }
      for (let k = 0; k + 1 < fill.points.length; k += 1) {
        const a = fill.points[k];
        const b = fill.points[k + 1];
        const ax = g.x(a.time);
        const bx = g.x(b.time);
        if (ax === null || bx === null || bx < 0 || ax > g.width) continue;
        const coords = [g.y(a.y1), g.y(b.y1), g.y(b.y2), g.y(a.y2)];
        if (coords.some((value) => value === null)) continue;
        context.fillStyle = a.color;
        context.beginPath();
        context.moveTo(ax, coords[0]!);
        context.lineTo(bx, coords[1]!);
        context.lineTo(bx, coords[2]!);
        context.lineTo(ax, coords[3]!);
        context.closePath();
        context.fill();
      }
    }
    for (const item of linefills) {
      const points = [item.a, item.b].map((line) => {
        const x1 = g.x(line.x1);
        const x2 = g.x(line.x2);
        const y1 = g.y(line.y1);
        const y2 = g.y(line.y2);
        return x1 === null || x2 === null || y1 === null || y2 === null
          ? null
          : extended(x1, y1, x2, y2, line.extend, g.width);
      });
      if (!points[0] || !points[1]) continue;
      const [a, b] = points;
      context.fillStyle = item.color;
      context.beginPath();
      context.moveTo(a[0], a[1]);
      context.lineTo(a[2], a[3]);
      context.lineTo(b[2], b[3]);
      context.lineTo(b[0], b[1]);
      context.closePath();
      context.fill();
    }
  }

  drawOver(context: Context, g: Geometry): void {
    const { boxes, lines, labels, shapes, tables, trades } = this.graphics;
    for (const box of boxes) {
      let left = g.x(box.left);
      let right = g.x(box.right);
      const top = g.y(box.top);
      const bottom = g.y(box.bottom);
      if (left === null || right === null || top === null || bottom === null)
        continue;
      if (box.extend === "left" || box.extend === "both")
        left = Math.min(left, right, 0);
      if (box.extend === "right" || box.extend === "both")
        right = Math.max(left, right, g.width);
      const x = Math.min(left, right);
      const w = Math.abs(right - left);
      const y = Math.min(top, bottom);
      const h = Math.abs(bottom - top);
      context.fillStyle = box.bgColor;
      context.fillRect(x, y, w, h);
      if (box.borderWidth > 0) {
        context.strokeStyle = box.borderColor;
        context.lineWidth = box.borderWidth;
        dash(context, box.borderStyle, box.borderWidth);
        context.strokeRect(x, y, w, h);
      }
      if (box.text) {
        context.font = `${box.textSize}px ${FONT}`;
        context.fillStyle = box.textColor;
        const block = textBlock(context, box.text, box.textSize);
        const tx =
          box.halign === "left"
            ? x + 4
            : box.halign === "right"
              ? x + w - 4
              : x + w / 2;
        const ty =
          box.valign === "top"
            ? y + 4
            : box.valign === "bottom"
              ? y + h - 4 - block.height
              : y + (h - block.height) / 2;
        textLines(context, box.text, tx, ty, box.textSize, box.halign);
      }
    }
    for (const line of lines) {
      const x1 = g.x(line.x1);
      const x2 = g.x(line.x2);
      const y1 = g.y(line.y1);
      const y2 = g.y(line.y2);
      if (x1 === null || x2 === null || y1 === null || y2 === null) continue;
      const [ax, ay, bx, by] = extended(x1, y1, x2, y2, line.extend, g.width);
      context.strokeStyle = line.color;
      context.lineWidth = line.width;
      dash(context, line.style, line.width);
      context.beginPath();
      context.moveTo(ax, ay);
      context.lineTo(bx, by);
      context.stroke();
      context.setLineDash([]);
      const head = 6 + line.width * 2;
      if (line.style === "arrow_right" || line.style === "arrow_both")
        arrowHead(context, x1, y1, x2, y2, head);
      if (line.style === "arrow_left" || line.style === "arrow_both")
        arrowHead(context, x2, y2, x1, y1, head);
    }
    for (const item of shapes) drawShape(context, item, g);
    for (const label of labels) drawLabel(context, label, g);
    for (const trade of trades) {
      const x = g.x(trade.time);
      const y = g.y(trade.price);
      if (x === null || y === null || x < -20 || x > g.width + 20) continue;
      const buy = trade.side === "buy";
      const color = buy ? BUY : SELL;
      shape(
        context,
        buy ? "triangleup" : "triangledown",
        x,
        buy ? y + 8 : y - 8,
        8,
        color,
      );
      context.font = `10px ${FONT}`;
      context.fillStyle = color;
      context.textAlign = "center";
      context.textBaseline = buy ? "top" : "bottom";
      context.fillText(
        `${trade.label}${trade.assumed ? " *" : ""}`,
        x,
        buy ? y + 14 : y - 14,
      );
    }
    for (const table of tables) drawTable(context, table, g);
  }
}
