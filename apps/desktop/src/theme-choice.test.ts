import { afterEach, describe, expect, it, vi } from "vitest";

import {
  applyStoredTheme,
  applyTheme,
  currentTheme,
  THEME_EVENT,
} from "./theme-choice";
import { chartChrome, CHART_THEMES } from "./theme";

afterEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.theme;
});

describe("theme choice (디자인-siw-3 r3)", () => {
  it("starts on theme a when nothing or something unknown is saved", () => {
    applyStoredTheme();
    expect(document.documentElement.dataset.theme).toBe("a");
    localStorage.setItem("siw.theme", "z");
    applyStoredTheme();
    expect(currentTheme()).toBe("a");
  });

  it("switches, keeps the choice and tells the charts", () => {
    const heard = vi.fn();
    window.addEventListener(THEME_EVENT, heard);
    applyTheme("b");
    window.removeEventListener(THEME_EVENT, heard);
    expect(document.documentElement.dataset.theme).toBe("b");
    expect(localStorage.getItem("siw.theme")).toBe("b");
    expect(heard).toHaveBeenCalledTimes(1);
    expect(chartChrome()).toEqual(CHART_THEMES.b);
  });
});
