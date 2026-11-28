/**
 * Display theme (디자인-siw-3 r3): "a" neutral chrome (default) or "b" docked tiles.
 * The choice lives in localStorage like the language and is set on <html data-theme>;
 * tokens.css carries both sets of values. Charts listen for THEME_EVENT to recolour.
 */
export type ThemeId = "a" | "b";

export const THEMES: readonly ThemeId[] = ["a", "b"];
export const THEME_EVENT = "siw-theme";
const KEY = "siw.theme";

function storage(): Storage | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

/** The theme on screen now ("a" unless "b" was chosen). */
export function currentTheme(): ThemeId {
  return document.documentElement.dataset.theme === "b" ? "b" : "a";
}

/** Before the first paint: put the saved choice on <html>; unknown or missing values mean "a". */
export function applyStoredTheme(): void {
  let saved: string | null = null;
  try {
    saved = storage()?.getItem(KEY) ?? null;
  } catch {
    saved = null;
  }
  document.documentElement.dataset.theme = saved === "b" ? "b" : "a";
}

/** Switch now, keep the choice, and tell the charts. */
export function applyTheme(theme: ThemeId): void {
  document.documentElement.dataset.theme = theme;
  try {
    storage()?.setItem(KEY, theme);
  } catch {
    // Not kept, but still applied for this run.
  }
  window.dispatchEvent(new Event(THEME_EVENT));
}
