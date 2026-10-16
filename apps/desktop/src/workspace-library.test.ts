import { describe, expect, it } from "vitest";

import {
  activeWorkspace,
  applyTemplate,
  LAYOUT_TEMPLATES,
  LIBRARY_STORAGE_KEY,
  loadLibrary,
  parseLibrary,
  removeWorkspace,
  renameWorkspace,
  saveAs,
  saveLibrary,
  updateActiveLayout,
  validateName,
} from "./workspace-library";
import { DEFAULT_WORKSPACE, WORKSPACE_STORAGE_KEY } from "./workspace-state";

function memoryStorage(initial: Record<string, string> = {}): Storage {
  const values = new Map(Object.entries(initial));
  return {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: () => null,
    removeItem: (key) => void values.delete(key),
    setItem: (key, value) => void values.set(key, value),
  };
}

const newId = () => "id-new";

describe("workspace library", () => {
  it("carries a v1 layout over as the Default workspace", () => {
    const v1 = { ...DEFAULT_WORKSPACE, syncTimeRange: false };
    const library = loadLibrary(
      memoryStorage({ [WORKSPACE_STORAGE_KEY]: JSON.stringify(v1) }),
      newId,
    );

    expect(library.workspaces).toEqual([
      { id: "id-new", name: "Default", layout: v1 },
    ]);
  });

  it("round-trips through storage and rejects a library whose active ID is unknown", () => {
    const storage = memoryStorage();
    const library = saveAs(
      loadLibrary(storage, () => "a"),
      "Review",
      "b",
    );
    saveLibrary(storage, library);

    expect(loadLibrary(storage, newId)).toEqual(library);
    expect(parseLibrary({ ...library, activeId: "missing" })).toBeNull();
    expect(
      loadLibrary(memoryStorage({ [LIBRARY_STORAGE_KEY]: "{" }), newId)
        .workspaces[0].name,
    ).toBe("Default");
  });

  it("saves a copy that later edits to the original do not change", () => {
    const original = loadLibrary(null, () => "a");
    const copied = saveAs(original, "  Copy  ", "b");
    const edited = updateActiveLayout(copied, {
      ...activeWorkspace(copied).layout,
      syncTimeRange: false,
    });

    expect(activeWorkspace(copied).name).toBe("Copy");
    expect(edited.workspaces[0].layout).toEqual(DEFAULT_WORKSPACE);
    expect(edited.workspaces[1].layout.syncTimeRange).toBe(false);
  });

  it("validates names and keeps the last workspace", () => {
    const library = saveAs(
      loadLibrary(null, () => "a"),
      "Second",
      "b",
    );

    expect(validateName(" ", [])).toBe("Enter a name");
    expect(validateName("x".repeat(61), [])).toMatch(/at most/);
    expect(() => saveAs(library, "second", "c")).toThrow(/already exists/);
    expect(() => renameWorkspace(library, "b", "default")).toThrow(
      /already exists/,
    );
    expect(renameWorkspace(library, "b", "Second").workspaces[1].name).toBe(
      "Second",
    );

    const one = removeWorkspace(library, "b");
    expect(one.activeId).toBe("a");
    expect(removeWorkspace(one, "a")).toBe(one);
  });
});

describe("layout templates", () => {
  it("copy timeframes and hours but keep each chart's instrument and date", () => {
    const layout = {
      ...DEFAULT_WORKSPACE,
      charts: [
        { ...DEFAULT_WORKSPACE.charts[0], instrumentId: "us:aapl:xnas:2026" },
        { ...DEFAULT_WORKSPACE.charts[1], sessionDate: "2026-11-27" },
      ] as typeof DEFAULT_WORKSPACE.charts,
    };
    const review = LAYOUT_TEMPLATES.find((item) => item.id === "review")!;

    const applied = applyTemplate(layout, review);
    applied.charts[0].timeframe = "1m";

    expect(applied.charts[0].instrumentId).toBe("us:aapl:xnas:2026");
    expect(applied.charts[1]).toMatchObject({
      sessionDate: "2026-11-27",
      timeframe: "60m",
    });
    expect(applied.syncTimeRange).toBe(true);
    expect(review.charts[0].timeframe).toBe("5m");
  });
});
