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
  - Otherwise it starts `uv run uvicorn …` from the repository found in this order (2026-10-22 개정, `repo-location.cjs`):
    1. `SIW_REPO_DIR` — 지정했으면 최종값이다. 틀려도 다른 폴더로 조용히 바꾸지 않고 오류를 보인다.
    2. 사용자가 고른 폴더 — 앱 사용자 데이터의 `settings.json`(`~/Library/Application Support/siw-electron/settings.json`)에 경로만 저장한다.
    3. 패키징할 때 `siw-config.json`에 기록한 폴더.
  - 셋 다 유효하지 않으면(`services/api/main.py` 없음, 예: 저장소를 옮김) 첫 실행 때 폴더 선택 창을 띄운다. 앱 메뉴의 "저장소 폴더 변경…"으로 나중에 바꿀 수 있고, 다음 실행부터 적용된다. API가 이미 응답하면 저장소를 찾지 않는다.
  - 저장 파일은 경로만 담고 키는 담지 않는다.
  - `uv` is looked up in the usual install places, because apps opened from Finder do not get the shell's `PATH`.
  - On quit, the shell stops only an API it started itself.
  - The API's output goes to `~/Library/Logs/siw-electron/api.log`.
- **Security:** context isolation and the sandbox are on, Node integration is off, and there is no preload script. Links that leave the app open in the system browser, and navigation away from `siw://app` is blocked. The shell adds no trading, file access or key handling.
- **Build:**
  - `npm run package` produces the `.app` and `npm run dist` the `.dmg` (x64). Both run in `apps/electron`. 설정은 `package.json`의 `build`에서 `electron-builder.config.cjs`로 옮겼다.
  - **아이콘(2026-10-22):** `assets/icon.png`(1024×1024)를 `scripts/make-icon.cjs`(`npm run icon`, 의존성 없음)로 그렸다. 어두운 둥근 사각형에 캔들 4개와 상승선을 그린 독자 도안이며 브랜드·상표 요소는 없다. electron-builder가 `.icns`로 바꾼다.
  - **서명·공증(준비만, 미수행):** 환경 변수가 없으면 지금처럼 서명하지 않는다(`identity: null`).
    - `CSC_NAME`(키체인의 "Developer ID Application" 인증서 이름) 또는 `CSC_LINK`+`CSC_KEY_PASSWORD`(.p12)가 있으면 hardened runtime으로 서명한다. 권한은 `assets/entitlements.mac.plist`의 `com.apple.security.cs.allow-jit` 하나다.
    - 서명할 때 `APPLE_API_KEY`+`APPLE_API_KEY_ID`+`APPLE_API_ISSUER`, `APPLE_ID`+`APPLE_APP_SPECIFIC_PASSWORD`+`APPLE_TEAM_ID`, `APPLE_KEYCHAIN_PROFILE` 중 하나가 갖춰지면 공증한다.
    - 저장소에는 인증서·비밀번호·키를 두지 않는다. 사용자 절차는 `apps/electron/SIGNING.md`에 있다.
  - Output goes to `apps/electron/release/` (ignored by git).
  - The binary was built with Electron 45.0.0 and electron-builder 26.15.3.

## Consequences

- **Not self-contained:** the package still needs this repository and its Python environment. 저장소를 옮기면 다시 패키징하지 않고 폴더 선택 창이나 `SIW_REPO_DIR`로 새 위치를 알려 준다.
- **Gatekeeper:** 서명하지 않은 빌드는 첫 실행 때 경고가 뜬다. 사용자가 Apple Developer ID를 제공해 서명·공증해야 사라진다.
- **Tauri:** stays the long-term plan in the spec; the two shells share the same screen. 2026-10-23 Rust 설치 후 Tauri `.app` 빌드와 실행을 처음 확인했다. Tauri 앱은 아직 API를 직접 띄우지 않는다.

## Revisit when

- The app must run without this repository: bundle the API (for example with PyInstaller) and its data paths.
- It is distributed to others: 실제 서명·공증 수행과 확인, universal(arm64) 빌드, auto-update.
