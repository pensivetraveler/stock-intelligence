import {
  useEffect,
  useState,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  type WheelEvent as ReactWheelEvent,
} from "react";

import {
  durationText,
  fibLevels,
  measure,
  POINTS_NEEDED,
  type Anchor,
  type Drawing,
  type DrawingTool,
} from "./drawings";
import { useI18n } from "./i18n";

/** Converts between chart values and pixels inside the layer. */
export interface Projection {
  toX: (time: number) => number | null;
  toY: (price: number) => number | null;
  /** Nearest bar time and price under a pixel (snapped when the magnet is on). */
  toAnchor: (x: number, y: number) => Anchor | null;
}

interface DrawingLayerProps {
  width: number;
  height: number;
  projection: Projection;
  /** Changes whenever the chart scrolls, zooms or resizes, to redraw. */
  redraw: number;
  drawings: Drawing[];
  tool: DrawingTool;
  instrumentId: string;
  /** Seconds per bar, for the measure tool. */
  barSeconds: number;
  formatPrice: (price: number) => string;
  /** False while clicks must reach the chart (e.g. picking a replay start). */
  selectable?: boolean;
  onCreate: (drawing: Omit<Drawing, "id">) => void;
  onDelete: (id: string) => void;
  onToolDone: () => void;
  onZoom: (fromTime: number, toTime: number) => void;
}

/**
 * A drawing's click area sits above the chart; hand wheel events to whatever lies under
 * it, so scrolling over a drawing still zooms the chart.
 */
function passWheel(event: ReactWheelEvent<SVGElement>) {
  const target = event.currentTarget;
  const previous = target.style.pointerEvents;
  target.style.pointerEvents = "none";
  const below = document.elementFromPoint(event.clientX, event.clientY);
  target.style.pointerEvents = previous;
  if (below && below !== target) {
    below.dispatchEvent(new WheelEvent("wheel", event.nativeEvent));
  }
}

const COLORS = {
  trend: "#5b8def",
  hline: "#f0b44c",
  fib: "#8e9bb3",
  text: "#e8edf4",
  selected: "#ffffff",
};

/**
 * SVG layer over the chart. With a drawing tool chosen it takes the clicks (so the chart
 * does not pan); with the cursor it lets clicks through except on a drawing, which it
 * selects for deletion.
 */
export function DrawingLayer({
  width,
  height,
  projection,
  redraw,
  drawings,
  tool,
  instrumentId,
  barSeconds,
  formatPrice,
  selectable = true,
  onCreate,
  onDelete,
  onToolDone,
  onZoom,
}: DrawingLayerProps) {
  const { t } = useI18n();
  const [draft, setDraft] = useState<Anchor[]>([]);
  const [hover, setHover] = useState<Anchor | null>(null);
  const [measured, setMeasured] = useState<[Anchor, Anchor] | null>(null);
  const [textAt, setTextAt] = useState<Anchor | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  void redraw; // Read so a scroll or zoom re-renders the layer with fresh pixels.

  useEffect(() => {
    setDraft([]);
    setTextAt(null);
    if (tool !== "cursor") setMeasured(null);
  }, [tool]);

  useEffect(() => {
    if (selected === null) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Delete" || event.key === "Backspace") {
        onDelete(selected);
        setSelected(null);
      }
      if (event.key === "Escape") setSelected(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selected, onDelete]);

  const local = (event: ReactPointerEvent<SVGSVGElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    return projection.toAnchor(
      event.clientX - box.left,
      event.clientY - box.top,
    );
  };

  const place = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (tool === "cursor" || event.button !== 0) return;
    event.preventDefault();
    const anchor = local(event);
    if (!anchor) return;
    if (tool === "text") {
      setTextAt(anchor);
      return;
    }
    const points = [...draft, anchor];
    if (points.length < POINTS_NEEDED[tool]) {
      setDraft(points);
      return;
    }
    setDraft([]);
    if (tool === "measure") {
      setMeasured([points[0], points[1]]);
    } else if (tool === "zoom") {
      const [from, to] = [points[0].time, points[1].time].sort((a, b) => a - b);
      if (to > from) onZoom(from, to);
    } else {
      onCreate({ kind: tool, instrumentId, points });
    }
    onToolDone();
  };

  const x = (time: number) => projection.toX(time);
  const y = (price: number) => projection.toY(price);

  const shape = (
    drawing: Pick<Drawing, "kind" | "points" | "text">,
    key: string,
    isSelected: boolean,
  ): ReactNode => {
    const color = isSelected ? COLORS.selected : COLORS[drawing.kind];
    const [a, b] = drawing.points;
    if (drawing.kind === "hline") {
      const py = y(a.price);
      if (py === null) return null;
      return (
        <g key={key}>
          <line x1={0} x2={width} y1={py} y2={py} stroke={color} />
          <text x={width - 6} y={py - 4} textAnchor="end" fill={color}>
            {formatPrice(a.price)}
          </text>
        </g>
      );
    }
    if (drawing.kind === "text") {
      const px = x(a.time);
      const py = y(a.price);
      if (px === null || py === null) return null;
      return (
        <text key={key} x={px} y={py} fill={color} className="drawing-text">
          {drawing.text}
        </text>
      );
    }
    const x1 = x(a.time);
    const y1 = y(a.price);
    const x2 = b ? x(b.time) : null;
    const y2 = b ? y(b.price) : null;
    if (x1 === null || y1 === null || x2 === null || y2 === null) return null;
    if (drawing.kind === "trend") {
      return <line key={key} x1={x1} y1={y1} x2={x2} y2={y2} stroke={color} />;
    }
    // Fibonacci retracement
    const left = Math.min(x1, x2);
    return (
      <g key={key}>
        <line
          x1={x1}
          y1={y1}
          x2={x2}
          y2={y2}
          stroke={color}
          strokeDasharray="4 4"
        />
        {fibLevels(a, b).map((level) => {
          const ly = y(level.price);
          if (ly === null) return null;
          return (
            <g key={level.ratio}>
              <line
                x1={left}
                x2={width}
                y1={ly}
                y2={ly}
                stroke={isSelected ? COLORS.selected : fibColor(level.ratio)}
              />
              <text x={left + 4} y={ly - 3} fill={fibColor(level.ratio)}>
                {level.ratio} ({formatPrice(level.price)})
              </text>
            </g>
          );
        })}
      </g>
    );
  };

  const hitArea = (drawing: Drawing): ReactNode => {
    const [a, b] = drawing.points;
    const select = (event: ReactPointerEvent) => {
      event.stopPropagation();
      setSelected(drawing.id);
    };
    const common = {
      className: "drawing-hit",
      onPointerDown: select,
      onWheel: passWheel,
      "aria-label": t(`draw.kind.${drawing.kind}`),
      role: "button",
    } as const;
    if (drawing.kind === "hline") {
      const py = y(a.price);
      return py === null ? null : (
        <line key={drawing.id} {...common} x1={0} x2={width} y1={py} y2={py} />
      );
    }
    const x1 = x(a.time);
    const y1 = y(a.price);
    if (x1 === null || y1 === null) return null;
    if (drawing.kind === "text") {
      return (
        <rect
          key={drawing.id}
          {...common}
          x={x1 - 4}
          y={y1 - 16}
          width={Math.max(40, (drawing.text?.length ?? 0) * 8)}
          height={22}
        />
      );
    }
    const x2 = b ? x(b.time) : null;
    const y2 = b ? y(b.price) : null;
    if (x2 === null || y2 === null) return null;
    return (
      <line key={drawing.id} {...common} x1={x1} y1={y1} x2={x2} y2={y2} />
    );
  };

  const preview =
    tool !== "cursor" && draft.length > 0 && hover ? [draft[0], hover] : null;
  const selectedDrawing = drawings.find((item) => item.id === selected);
  const selectedPoint = selectedDrawing?.points[0];
  const deleteX = selectedPoint ? x(selectedPoint.time) : null;
  const deleteY = selectedPoint ? y(selectedPoint.price) : null;

  return (
    <div className="drawing-layer">
      <svg
        width={width}
        height={height}
        className={
          tool === "cursor" ? "drawing-svg passive" : "drawing-svg active"
        }
        aria-label={t("draw.layer")}
        onPointerDown={place}
        onPointerMove={(event) => {
          if (tool !== "cursor") setHover(local(event));
        }}
        onPointerLeave={() => setHover(null)}
      >
        {drawings.map((drawing) => (
          <g key={drawing.id}>
            {shape(drawing, `${drawing.id}:shape`, drawing.id === selected)}
            {tool === "cursor" && selectable ? hitArea(drawing) : null}
          </g>
        ))}

        {preview && (tool === "trend" || tool === "fib")
          ? shape({ kind: tool, points: preview }, "preview", false)
          : null}
        {preview && (tool === "measure" || tool === "zoom")
          ? box(preview, tool)
          : null}
        {measured ? box(measured, "measure") : null}
      </svg>

      {selectedDrawing && deleteX !== null && deleteY !== null ? (
        <button
          type="button"
          className="drawing-delete"
          style={{ left: deleteX + 6, top: deleteY - 26 }}
          aria-label={t("draw.delete")}
          onClick={() => {
            onDelete(selectedDrawing.id);
            setSelected(null);
          }}
        >
          ✕
        </button>
      ) : null}

      {textAt ? (
        <TextInput
          left={x(textAt.time) ?? 0}
          top={y(textAt.price) ?? 0}
          label={t("draw.textPrompt")}
          onDone={(text) => {
            if (text.trim()) {
              onCreate({
                kind: "text",
                instrumentId,
                points: [textAt],
                text: text.trim().slice(0, 200),
              });
            }
            setTextAt(null);
            onToolDone();
          }}
        />
      ) : null}
    </div>
  );

  function box(points: Anchor[], kind: "measure" | "zoom"): ReactNode {
    const [a, b] = points;
    const x1 = x(a.time);
    const x2 = x(b.time);
    const y1 = kind === "zoom" ? 0 : y(a.price);
    const y2 = kind === "zoom" ? height : y(b.price);
    if (x1 === null || x2 === null || y1 === null || y2 === null) return null;
    const left = Math.min(x1, x2);
    const top = Math.min(y1, y2);
    if (kind === "zoom") {
      return (
        <rect
          key="zoom"
          className="zoom-box"
          x={left}
          y={0}
          width={Math.abs(x2 - x1)}
          height={height}
        />
      );
    }
    const result = measure(a, b, barSeconds);
    const rising = result.change >= 0;
    const label = `${result.change >= 0 ? "+" : ""}${formatPrice(result.change)}${
      result.percent === null
        ? ""
        : ` (${result.percent >= 0 ? "+" : ""}${result.percent.toFixed(2)}%)`
    } · ${t("draw.bars", { n: result.bars })} · ${durationText(result.seconds)}`;
    return (
      <g key="measure" className={rising ? "measure up" : "measure down"}>
        <rect
          x={left}
          y={top}
          width={Math.abs(x2 - x1)}
          height={Math.abs(y2 - y1)}
        />
        <text x={left + 4} y={Math.max(12, top - 6)}>
          {label}
        </text>
      </g>
    );
  }
}

function fibColor(ratio: number): string {
  if (ratio === 0 || ratio === 1) return "#8e9bb3";
  if (ratio === 0.5) return "#4fb38b";
  if (ratio === 0.618) return "#f0b44c";
  return "#6f8ccf";
}

function TextInput({
  left,
  top,
  label,
  onDone,
}: {
  left: number;
  top: number;
  label: string;
  onDone: (text: string) => void;
}) {
  const [value, setValue] = useState("");
  return (
    <input
      autoFocus
      className="drawing-input"
      style={{ left, top: top - 14 }}
      aria-label={label}
      placeholder={label}
      maxLength={200}
      value={value}
      onChange={(event) => setValue(event.target.value)}
      onKeyDown={(event) => {
        if (event.key === "Enter") onDone(value);
        if (event.key === "Escape") onDone("");
      }}
      onBlur={() => onDone(value)}
    />
  );
}
