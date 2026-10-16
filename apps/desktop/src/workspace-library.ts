import {
  DEFAULT_WORKSPACE,
  parseWorkspace,
  WORKSPACE_STORAGE_KEY,
  type ChartConfig,
  type WorkspaceState,
} from "./workspace-state";

export interface SavedWorkspace {
  id: string;
  name: string;
  layout: WorkspaceState;
}

/** Named workspaces on this device; the active one is saved as it changes. */
export interface WorkspaceLibrary {
  version: 2;
  activeId: string;
  workspaces: SavedWorkspace[];
}

export const LIBRARY_STORAGE_KEY = "siw.workspaces.v2";
export const MAX_NAME_LENGTH = 60;

export interface LayoutTemplate {
  id: "terminal" | "review";
  name: string;
  description: string;
  sync: boolean;
  charts: [
    Pick<ChartConfig, "timeframe" | "sessionScope">,
    Pick<ChartConfig, "timeframe" | "sessionScope">,
  ];
}

/** Workspace layouts from the spec: a trading terminal and a trade review layout. */
export const LAYOUT_TEMPLATES: readonly LayoutTemplate[] = [
  {
    id: "terminal",
    name: "Trading Terminal",
    description: "1m and 15m with extended hours, charts move independently",
    sync: false,
    charts: [
      { timeframe: "1m", sessionScope: "extended" },
      { timeframe: "15m", sessionScope: "extended" },
    ],
  },
  {
    id: "review",
    name: "Trade Review",
    description: "5m and 60m regular session, visible time range synced",
    sync: true,
    charts: [
      { timeframe: "5m", sessionScope: "regular" },
      { timeframe: "60m", sessionScope: "regular" },
    ],
  },
];

export function createLibrary(
  layout: WorkspaceState,
  id: string,
): WorkspaceLibrary {
  return {
    version: 2,
    activeId: id,
    workspaces: [{ id, name: "Default", layout }],
  };
}

export function parseLibrary(value: unknown): WorkspaceLibrary | null {
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const library = value as Record<string, unknown>;
  if (
    library.version !== 2 ||
    typeof library.activeId !== "string" ||
    !Array.isArray(library.workspaces) ||
    library.workspaces.length === 0
  ) {
    return null;
  }
  const workspaces: SavedWorkspace[] = [];
  for (const item of library.workspaces as unknown[]) {
    const saved = item as Record<string, unknown> | null;
    const layout = parseWorkspace(saved?.layout);
    if (
      layout === null ||
      typeof saved?.id !== "string" ||
      typeof saved.name !== "string" ||
      validateName(saved.name, []) !== null
    ) {
      return null;
    }
    workspaces.push({ id: saved.id, name: saved.name, layout });
  }
  if (!workspaces.some((item) => item.id === library.activeId)) {
    return null;
  }
  return { version: 2, activeId: library.activeId, workspaces };
}

/** Loads the library, carrying a v1 single-layout save over as "Default". */
export function loadLibrary(
  storage: Storage | null,
  newId: () => string,
): WorkspaceLibrary {
  try {
    const raw = storage?.getItem(LIBRARY_STORAGE_KEY);
    const library = raw ? parseLibrary(JSON.parse(raw)) : null;
    if (library) {
      return library;
    }
    const legacy = storage?.getItem(WORKSPACE_STORAGE_KEY);
    const layout = legacy ? parseWorkspace(JSON.parse(legacy)) : null;
    return createLibrary(layout ?? DEFAULT_WORKSPACE, newId());
  } catch {
    return createLibrary(DEFAULT_WORKSPACE, newId());
  }
}

export function saveLibrary(
  storage: Storage | null,
  library: WorkspaceLibrary,
): void {
  try {
    storage?.setItem(LIBRARY_STORAGE_KEY, JSON.stringify(library));
  } catch {
    // Unavailable storage only means the library is not kept between sessions.
  }
}

export function activeWorkspace(library: WorkspaceLibrary): SavedWorkspace {
  return (
    library.workspaces.find((item) => item.id === library.activeId) ??
    library.workspaces[0]
  );
}

export function updateActiveLayout(
  library: WorkspaceLibrary,
  layout: WorkspaceState,
): WorkspaceLibrary {
  return {
    ...library,
    workspaces: library.workspaces.map((item) =>
      item.id === library.activeId ? { ...item, layout } : item,
    ),
  };
}

/** Returns an error message, or null when the name is usable. */
export function validateName(
  name: string,
  takenNames: string[],
): string | null {
  const trimmed = name.trim();
  if (trimmed.length === 0) {
    return "Enter a name";
  }
  if (trimmed.length > MAX_NAME_LENGTH) {
    return `Use at most ${MAX_NAME_LENGTH} characters`;
  }
  if (
    takenNames.some(
      (taken) => taken.trim().toLowerCase() === trimmed.toLowerCase(),
    )
  ) {
    return "A workspace with this name already exists";
  }
  return null;
}

function cloneLayout(layout: WorkspaceState): WorkspaceState {
  return {
    ...layout,
    charts: [{ ...layout.charts[0] }, { ...layout.charts[1] }],
  };
}

/** Saves a copy of the active layout under a new name and switches to it. */
export function saveAs(
  library: WorkspaceLibrary,
  name: string,
  id: string,
): WorkspaceLibrary {
  const error = validateName(
    name,
    library.workspaces.map((item) => item.name),
  );
  if (error) {
    throw new Error(error);
  }
  const copy: SavedWorkspace = {
    id,
    name: name.trim(),
    layout: cloneLayout(activeWorkspace(library).layout),
  };
  return {
    ...library,
    activeId: id,
    workspaces: [...library.workspaces, copy],
  };
}

export function switchTo(
  library: WorkspaceLibrary,
  id: string,
): WorkspaceLibrary {
  return library.workspaces.some((item) => item.id === id)
    ? { ...library, activeId: id }
    : library;
}

export function renameWorkspace(
  library: WorkspaceLibrary,
  id: string,
  name: string,
): WorkspaceLibrary {
  const others = library.workspaces
    .filter((item) => item.id !== id)
    .map((item) => item.name);
  const error = validateName(name, others);
  if (error) {
    throw new Error(error);
  }
  return {
    ...library,
    workspaces: library.workspaces.map((item) =>
      item.id === id ? { ...item, name: name.trim() } : item,
    ),
  };
}

/** Deletes a workspace; the last one cannot be deleted. */
export function removeWorkspace(
  library: WorkspaceLibrary,
  id: string,
): WorkspaceLibrary {
  const remaining = library.workspaces.filter((item) => item.id !== id);
  if (
    remaining.length === 0 ||
    remaining.length === library.workspaces.length
  ) {
    return library;
  }
  const activeId = library.activeId === id ? remaining[0].id : library.activeId;
  return { ...library, activeId, workspaces: remaining };
}

/**
 * Applies a template's timeframes, hours and sync to a layout. Each chart keeps its
 * own instrument and date, and the result shares no objects with the template.
 */
export function applyTemplate(
  layout: WorkspaceState,
  template: LayoutTemplate,
): WorkspaceState {
  return {
    version: 1,
    syncTimeRange: template.sync,
    charts: [
      { ...layout.charts[0], ...template.charts[0] },
      { ...layout.charts[1], ...template.charts[1] },
    ],
  };
}
