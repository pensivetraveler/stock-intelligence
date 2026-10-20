import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DrawingLayer, type Projection } from "./DrawingLayer";
import type { Drawing, DrawingTool } from "./drawings";

// One pixel per second across, and price 200 at the top falling one per pixel down.
// jsdom places the layer at (0, 0), so client coordinates equal layer pixels.
const projection: Projection = {
  toX: (time) => time,
  toY: (price) => 200 - price,
  toAnchor: (x, y) => ({ time: Math.round(x), price: 200 - y }),
};

function setup(tool: DrawingTool, drawings: Drawing[] = [], selectable = true) {
  const handlers = {
    onCreate: vi.fn(),
    onDelete: vi.fn(),
    onToolDone: vi.fn(),
    onZoom: vi.fn(),
  };
  const view = render(
    <DrawingLayer
      width={400}
      height={200}
      projection={projection}
      redraw={0}
      drawings={drawings}
      tool={tool}
      instrumentId="us:aapl:xnas:2026"
      barSeconds={60}
      formatPrice={(price) => price.toFixed(2)}
      selectable={selectable}
      {...handlers}
    />,
  );
  const layer = screen.getByLabelText("Drawings");
  const click = (x: number, y: number) =>
    fireEvent.pointerDown(layer, { button: 0, clientX: x, clientY: y });
  return { ...handlers, click, layer, view };
}

describe("DrawingLayer", () => {
  it("draws a trend line from two clicks and returns to the cursor", () => {
    const { click, onCreate, onToolDone } = setup("trend");
    click(60, 100);
    expect(onCreate).not.toHaveBeenCalled();
    click(180, 50);
    expect(onCreate).toHaveBeenCalledWith({
      kind: "trend",
      instrumentId: "us:aapl:xnas:2026",
      points: [
        { time: 60, price: 100 },
        { time: 180, price: 150 },
      ],
    });
    expect(onToolDone).toHaveBeenCalled();
  });

  it("places a horizontal line with one click", () => {
    const { click, onCreate } = setup("hline");
    click(10, 40);
    expect(onCreate).toHaveBeenCalledWith({
      kind: "hline",
      instrumentId: "us:aapl:xnas:2026",
      points: [{ time: 10, price: 160 }],
    });
  });

  it("asks for a note and saves it on Enter", () => {
    const { click, onCreate } = setup("text");
    click(30, 70);
    const input = screen.getByLabelText("Note text, then Enter");
    fireEvent.change(input, { target: { value: "  breakout  " } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onCreate).toHaveBeenCalledWith({
      kind: "text",
      instrumentId: "us:aapl:xnas:2026",
      points: [{ time: 30, price: 130 }],
      text: "breakout",
    });
  });

  it("measures without saving anything", () => {
    const { click, onCreate } = setup("measure");
    click(0, 100);
    click(300, 90);
    expect(onCreate).not.toHaveBeenCalled();
    expect(
      screen.getByText(/\+10\.00 \(\+10\.00%\) · 5 bars · 5m/),
    ).toBeTruthy();
  });

  it("zooms to the dragged time range", () => {
    const { click, onZoom } = setup("zoom");
    click(240, 10);
    click(60, 10);
    expect(onZoom).toHaveBeenCalledWith(60, 240);
  });

  it("selects a drawing with the cursor and deletes it", () => {
    const drawing: Drawing = {
      id: "d1",
      kind: "hline",
      instrumentId: "us:aapl:xnas:2026",
      points: [{ time: 10, price: 150 }],
    };
    const { onDelete } = setup("cursor", [drawing]);
    fireEvent.pointerDown(
      screen.getByRole("button", { name: "Horizontal line" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Delete drawing" }));
    expect(onDelete).toHaveBeenCalledWith("d1");
  });

  it("leaves drawings unclickable while clicks must reach the chart", () => {
    const drawing: Drawing = {
      id: "d1",
      kind: "hline",
      instrumentId: "us:aapl:xnas:2026",
      points: [{ time: 10, price: 150 }],
    };
    setup("cursor", [drawing], false);
    expect(
      screen.queryByRole("button", { name: "Horizontal line" }),
    ).not.toBeInTheDocument();
  });

  it("lets clicks through to the chart in cursor mode", () => {
    const { click, onCreate, layer } = setup("cursor");
    expect(layer).toHaveClass("passive");
    click(10, 10);
    expect(onCreate).not.toHaveBeenCalled();
  });
});
