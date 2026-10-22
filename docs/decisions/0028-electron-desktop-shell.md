# ADR 0028 — Electron desktop shell

- **Status:** accepted
- **Date:** 2026-10-22

## Context

The planned native shell is Tauri (Phase 0), but it has never been built: `cargo` is missing on this machine (PROJECT_STATE). On 2026-10-22 the user asked for an Electron package.

## Decision

- **Package:** `apps/electron` is an Electron shell for the same screen build as the web app (`apps/desktop/dist`). It is kept out of the pnpm workspace (`!apps/electron`) and has its own npm lockfile, so CI never downloads Electron.
- **Serving the screen:** the screen is served from a custom privileged scheme, `siw://app`. It is registered as standard, secure, fetch-capable and CORS-enabled, so the screen behaves like a normal web origin. The API always allows that origin, even when `SIW_CORS_ORIGINS` replaces the defaults: no web page can send a `siw://` origin. Realtime WebSocket connections use the same origin check.
- **API:** not bundled, because it needs this repository's Python environment, `.env` and the OS keychain.
  - On launch, the shell uses an API that already answers on 127.0.0.1:8765.
  - Otherwise it starts `uv run uvicorn …` from the repository recorded in `siw-config.json` at packaging time. That file holds only a path, never keys.
  - `uv` is looked up in the usual install places, because apps opened from Finder do not get the shell's `PATH`.
  - On quit, the shell stops only an API it started itself.
  - The API's output goes to `~/Library/Logs/siw-electron/api.log`.
- **Security:** context isolation and the sandbox are on, Node integration is off, and there is no preload script. Links that leave the app open in the system browser, and navigation away from `siw://app` is blocked. The shell adds no trading, file access or key handling.
- **Build:**
  - `npm run package` produces the `.app` and `npm run dist` the `.dmg` (x64, unsigned: `mac.identity: null`). Both run in `apps/electron`.
  - Output goes to `apps/electron/release/` (ignored by git).
  - The binary was built with Electron 45.0.0 and electron-builder 26.15.3.

## Consequences

- **Not self-contained:** the package depends on this repository staying where it was built. Moving the repository needs a rebuild, or `SIW_REPO_DIR` at launch.
- **Gatekeeper:** an unsigned app gets warnings on first launch. Signing and notarization need an Apple Developer ID.
- **Tauri:** stays the long-term plan in the spec; the two shells share the same screen.

## Revisit when

- The app must run without this repository: bundle the API (for example with PyInstaller) and its data paths.
- It is distributed to others: code signing and notarization, an app icon, and auto-update.
