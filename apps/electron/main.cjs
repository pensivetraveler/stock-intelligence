// Electron shell for Stock Intelligence Workstation (ADR 0028).
//
// The screen is the same build as the web app (apps/desktop/dist), served from a
// custom "siw://app" origin, which the local API allows. Read-only like the web app:
// the shell adds no trading, keys or file access.
const {
  app,
  BrowserWindow,
  dialog,
  net,
  protocol,
  shell,
} = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const { ensureApi } = require("./api-process.cjs");

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
  process.env.SIW_API_URL || CONFIG.apiUrl || "http://127.0.0.1:8000";
const REPO_DIR = process.env.SIW_REPO_DIR || CONFIG.repoDir || null;

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
    title: "Stock Intelligence Workstation",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
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

app.whenReady().then(async () => {
  serveUi();
  try {
    const result = await ensureApi({ apiUrl: API_URL, repoDir: REPO_DIR, log });
    apiChild = result.process;
  } catch (error) {
    // The screen still opens; it shows its own "cannot reach the API" state.
    dialog.showErrorBox(
      "Local API",
      `${error instanceof Error ? error.message : String(error)}\n\nLog: ${logPath()}`,
    );
  }
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
