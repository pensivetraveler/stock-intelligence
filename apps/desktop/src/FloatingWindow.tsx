import {
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";

export interface WindowGeometry {
  x: number;
  y: number;
  width: number;
  height: number;
}

interface FloatingWindowProps {
  title: string;
  /** Saved position and size; the window keeps itself inside the viewport. */
  geometry: WindowGeometry;
  onGeometryChange: (geometry: WindowGeometry) => void;
  actions?: ReactNode;
  children: ReactNode;
}

const MIN_WIDTH = 240;
const MIN_HEIGHT = 160;

function clamp(geometry: WindowGeometry): WindowGeometry {
  const width = Math.max(
    MIN_WIDTH,
    Math.min(geometry.width, window.innerWidth),
  );
  const height = Math.max(
    MIN_HEIGHT,
    Math.min(geometry.height, window.innerHeight),
  );
  return {
    width,
    height,
    x: Math.max(0, Math.min(geometry.x, window.innerWidth - width)),
    y: Math.max(0, Math.min(geometry.y, window.innerHeight - height)),
  };
}

/**
 * A window that floats over the charts: drag it by its title bar and resize it from
 * the bottom-right corner. Position and size are reported when a drag or resize ends.
 */
export function FloatingWindow({
  title,
  geometry,
  onGeometryChange,
  actions,
  children,
}: FloatingWindowProps) {
  const [box, setBox] = useState(() => clamp(geometry));
  const drag = useRef<{
    mode: "move" | "resize";
    startX: number;
    startY: number;
    origin: WindowGeometry;
  } | null>(null);
  const latest = useRef(box);
  latest.current = box;

  useEffect(() => {
    const onResize = () => setBox((current) => clamp(current));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const start =
    (mode: "move" | "resize") => (event: ReactPointerEvent<HTMLElement>) => {
      if (event.button !== 0) return;
      if (
        mode === "move" &&
        (event.target as HTMLElement).closest("button, input, select")
      ) {
        return; // Clicks on the title bar's buttons are not drags.
      }
      event.preventDefault();
      event.currentTarget.setPointerCapture?.(event.pointerId);
      drag.current = {
        mode,
        startX: event.clientX,
        startY: event.clientY,
        origin: latest.current,
      };
    };

  const move = (event: ReactPointerEvent<HTMLElement>) => {
    const current = drag.current;
    if (!current) return;
    const dx = event.clientX - current.startX;
    const dy = event.clientY - current.startY;
    setBox(
      clamp(
        current.mode === "move"
          ? {
              ...current.origin,
              x: current.origin.x + dx,
              y: current.origin.y + dy,
            }
          : {
              ...current.origin,
              width: current.origin.width + dx,
              height: current.origin.height + dy,
            },
      ),
    );
  };

  const end = () => {
    if (drag.current) {
      drag.current = null;
      onGeometryChange(latest.current);
    }
  };

  return (
    <section
      className="floating-window"
      role="dialog"
      aria-label={title}
      style={{
        left: box.x,
        top: box.y,
        width: box.width,
        height: box.height,
      }}
    >
      <header
        className="floating-title"
        onPointerDown={start("move")}
        onPointerMove={move}
        onPointerUp={end}
        onPointerCancel={end}
      >
        <span>{title}</span>
        <span className="floating-actions">{actions}</span>
      </header>
      <div className="floating-body">{children}</div>
      <span
        className="floating-resize"
        aria-hidden="true"
        onPointerDown={start("resize")}
        onPointerMove={move}
        onPointerUp={end}
        onPointerCancel={end}
      />
    </section>
  );
}
