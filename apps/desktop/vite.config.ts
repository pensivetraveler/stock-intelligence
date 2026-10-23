import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `--mode agent` builds SIW Agent into dist-agent (Tauri); any other mode builds
// SIW Research into dist-research (Electron, and the web dev server).
export default defineConfig(({ mode }) => {
  const app = mode === "agent" ? "agent" : "research";
  return {
    plugins: [react()],
    clearScreen: false,
    define: { "import.meta.env.VITE_SIW_APP": JSON.stringify(app) },
    build: { outDir: app === "agent" ? "dist-agent" : "dist-research" },
    server: {
      host: "127.0.0.1",
      port: 5173,
      strictPort: true,
    },
    test: {
      environment: "jsdom",
      setupFiles: "./src/test-setup.ts",
    },
  };
});
