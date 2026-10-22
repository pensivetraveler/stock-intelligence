// Finds or starts the local API (FastAPI on 127.0.0.1:8000) for the Electron shell.
// The API is not bundled: it needs this repository's Python environment, .env and the
// OS keychain. If it is already running (e.g. in a terminal) it is used as it is.
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

async function healthy(apiUrl) {
  try {
    const response = await fetch(`${apiUrl}/health`, {
      signal: AbortSignal.timeout(1500),
    });
    return response.ok;
  } catch {
    return false;
  }
}

/** uv in the usual places: apps opened from Finder do not get the shell's PATH. */
function findUv() {
  const candidates = [
    path.join(os.homedir(), ".local/bin/uv"),
    path.join(os.homedir(), ".cargo/bin/uv"),
    "/opt/homebrew/bin/uv",
    "/usr/local/bin/uv",
  ];
  return candidates.find((candidate) => fs.existsSync(candidate)) ?? null;
}

/**
 * Returns { started, process } once the API answers, starting it from repoDir when
 * nothing answers yet. Throws with a readable reason when it cannot.
 */
async function ensureApi({ apiUrl, repoDir, log }) {
  if (await healthy(apiUrl)) return { started: false, process: null };
  if (!repoDir || !fs.existsSync(path.join(repoDir, "services/api/main.py"))) {
    throw new Error(
      `the local API is not running and the repository was not found at ${repoDir}`,
    );
  }
  const uv = findUv();
  if (!uv) throw new Error("the local API is not running and uv was not found");
  const args = [
    "run",
    "uvicorn",
    "services.api.main:app",
    "--host",
    "127.0.0.1",
    "--port",
    "8000",
  ];
  if (fs.existsSync(path.join(repoDir, ".env")))
    args.push("--env-file", ".env");
  const child = spawn(uv, args, {
    cwd: repoDir,
    env: { ...process.env, UV_CACHE_DIR: path.join(repoDir, ".uv-cache") },
    stdio: ["ignore", "pipe", "pipe"],
  });
  child.stdout.on("data", (chunk) => log(String(chunk)));
  child.stderr.on("data", (chunk) => log(String(chunk)));
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(
        `the local API stopped while starting (exit ${child.exitCode})`,
      );
    }
    if (await healthy(apiUrl)) return { started: true, process: child };
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  child.kill();
  throw new Error("the local API did not answer within 60 seconds");
}

module.exports = { ensureApi, healthy };
