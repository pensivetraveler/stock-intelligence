import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";

import { applyStoredTheme } from "./theme-choice";

// One source, two builds (ADR 0037 §7): `vite build --mode agent` for SIW Agent (Tauri)
// and `--mode research` for SIW Research (Electron). The other app's screen is a
// constant-false branch, so it is left out of the bundle entirely.
const isAgent = import.meta.env.VITE_SIW_APP === "agent";
document.title = isAgent ? "SIW Agent" : "SIW Research";
// The saved theme goes on <html> before the first paint (디자인-siw-3 r3).
applyStoredTheme();

const Screen = isAgent
  ? lazy(async () => ({
      default: (await import("./agent/AgentApp")).AgentApp,
    }))
  : lazy(async () => ({ default: (await import("./App")).App }));

const root = document.getElementById("root");
if (!root) {
  throw new Error("Root element not found");
}

createRoot(root).render(
  <StrictMode>
    <Suspense fallback={null}>
      <Screen />
    </Suspense>
  </StrictMode>,
);
