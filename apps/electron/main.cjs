// Electron shell for Stock Intelligence Workstation (ADR 0028): "SIW Research", the
// research and backtesting app. Order execution belongs to the Tauri app (ADR 0036).
//
// The screen is the SIW Research build (apps/desktop/dist-research), served from a
// custom "siw://app" origin, which the local API allows. Read-only like the web app:
// the shell adds no trading, keys or file access.
const {
  app,
  BrowserWindow,
  dialog,
  Menu,
  MenuItem,
  net,
  protocol,
  shell,
} = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const { ensureApi, healthy } = require("./api-process.cjs");
const {
  findRepoDir,
  isRepoDir,
  rememberRepoDir,
} = require("./repo-location.cjs");

const UI_DIR = path.join(__dirname, "ui");
const CONFIG = (() => {
  try {
    return JSON.parse(
      fs.readFileSync(path.join(__dirname, "siw-config.json"), "utf8"),
    );
  } catch {
    return {};
  }
})();
const API_URL =
  process.env.SIW_API_URL || CONFIG.apiUrl || "http://127.0.0.1:8765";

protocol.registerSchemesAsPrivileged([
  {
    scheme: "siw",
    privileges: {
      standard: true,
      secure: true,
      supportFetchAPI: true,
      corsEnabled: true,
    },
  },
]);

let apiChild = null;
const logPath = () => path.join(app.getPath("logs"), "api.log");
const log = (text) => {
  try {
    fs.appendFileSync(logPath(), text);
  } catch {
    // logging must never stop the app
  }
};

function serveUi() {
  protocol.handle("siw", (request) => {
    const url = new URL(request.url);
    const relative =
      decodeURIComponent(url.pathname).replace(/^\/+/, "") || "index.html";
    const file = path.normalize(path.join(UI_DIR, relative));
    // Only files inside the built screen; unknown paths get the app (single page).
    const inside = file.startsWith(UI_DIR + path.sep) && fs.existsSync(file);
    return net.fetch(
      pathToFileURL(inside ? file : path.join(UI_DIR, "index.html")).toString(),
    );
  });
}

function createWindow() {
  const window = new BrowserWindow({
    width: 1480,
    height: 920,
    minWidth: 960,
    minHeight: 640,
    backgroundColor: "#10131a",
    title: "SIW Research — 전략 연구·백테스트",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  // Keep the app's own title (SIW Research) instead of the shared page title.
  window.on("page-title-updated", (event) => event.preventDefault());
  // Links out of the app (e.g. chart attribution) open in the browser, not in the app.
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });
  window.webContents.on("will-navigate", (event, url) => {
    if (!url.startsWith("siw://app/")) {
      event.preventDefault();
      if (/^https?:\/\//.test(url)) shell.openExternal(url);
    }
  });
  window.loadURL("siw://app/index.html");
  return window;
}

/** Asks for the repository folder until a valid one is picked or the person cancels. */
async function pickRepoDir(reason) {
  for (;;) {
    const { canceled, filePaths } = await dialog.showOpenDialog({
      title: "저장소 폴더 선택",
      message: reason,
      buttonLabel: "이 폴더 사용",
      properties: ["openDirectory"],
    });
    if (canceled || filePaths.length === 0) return null;
    if (isRepoDir(filePaths[0])) {
      rememberRepoDir(app.getPath("userData"), filePaths[0]);
      return filePaths[0];
    }
    await dialog.showMessageBox({
      type: "warning",
      message: "이 폴더에는 services/api/main.py가 없습니다.",
      detail: "Stock Intelligence Workstation 저장소의 최상위 폴더를 고르세요.",
    });
  }
}

/** The repository to start the API from, asking for it when none is known. */
async function resolveRepoDir() {
  const found = findRepoDir({
    envDir: process.env.SIW_REPO_DIR,
    userDataDir: app.getPath("userData"),
    configDir: CONFIG.repoDir,
  });
  if (found.repoDir) return found.repoDir;
  // A wrong SIW_REPO_DIR is reported as it is, not replaced by a picked folder.
  if (found.source === "env") return process.env.SIW_REPO_DIR;
  return pickRepoDir(
    "로컬 API를 띄울 저장소 폴더를 찾지 못했습니다. 저장소 폴더를 골라 주세요. 고른 위치는 기억합니다.",
  );
}

async function startApi() {
  // An API that already answers needs no repository.
  if (await healthy(API_URL)) return;
  try {
    const repoDir = await resolveRepoDir();
    const result = await ensureApi({ apiUrl: API_URL, repoDir, log });
    apiChild = result.process;
  } catch (error) {
    // The screen still opens; it shows its own "cannot reach the API" state.
    dialog.showErrorBox(
      "Local API",
      `${error instanceof Error ? error.message : String(error)}\n\nLog: ${logPath()}`,
    );
  }
}

/** Adds "저장소 폴더 변경…" to the app menu; the new folder is used from the next launch. */
function addRepoMenuItem() {
  const menu = Menu.getApplicationMenu();
  const appMenu = menu?.items.find((item) => item.role === "appmenu")?.submenu;
  if (!menu || !appMenu) return;
  appMenu.insert(
    1,
    new MenuItem({
      label: "저장소 폴더 변경…",
      click: async () => {
        const picked = await pickRepoDir(
          "로컬 API를 띄울 저장소 폴더를 고르세요. 다음 실행부터 적용됩니다.",
        );
        if (picked) {
          await dialog.showMessageBox({
            message: "저장소 폴더를 저장했습니다.",
            detail: `${picked}\n\n이미 실행 중인 API는 그대로 둡니다. 앱을 다시 실행하면 이 폴더에서 API를 띄웁니다.`,
          });
        }
      },
    }),
  );
  Menu.setApplicationMenu(menu);
}

app.whenReady().then(async () => {
  serveUi();
  addRepoMenuItem();
  await startApi();
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  // Stop only an API this app started; one started elsewhere keeps running.
  if (apiChild && apiChild.exitCode === null) apiChild.kill("SIGINT");
});
