// Where the repository (and so the local API) lives. The packaged app is not tied to
// the folder it was built from: SIW_REPO_DIR wins, then the folder the person picked
// (remembered in the app's user data), then the folder recorded at packaging time.
// Only a path is stored, never keys.
const fs = require("node:fs");
const path = require("node:path");

const SETTINGS_FILE = "settings.json";

/** True when dir holds this repository's API entry point. */
function isRepoDir(dir) {
  return (
    typeof dir === "string" &&
    dir.length > 0 &&
    fs.existsSync(path.join(dir, "services/api/main.py"))
  );
}

function readSettings(userDataDir) {
  try {
    const parsed = JSON.parse(
      fs.readFileSync(path.join(userDataDir, SETTINGS_FILE), "utf8"),
    );
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function rememberRepoDir(userDataDir, repoDir) {
  const settings = { ...readSettings(userDataDir), repoDir };
  fs.mkdirSync(userDataDir, { recursive: true });
  fs.writeFileSync(
    path.join(userDataDir, SETTINGS_FILE),
    `${JSON.stringify(settings, null, 2)}\n`,
  );
}

/**
 * The first valid folder in order: SIW_REPO_DIR, the remembered pick, the packaged
 * config. A set SIW_REPO_DIR is final, even when wrong, so it is never silently
 * overridden. Returns { repoDir, source } with repoDir null when none is valid.
 */
function findRepoDir({ envDir, userDataDir, configDir }) {
  if (envDir)
    return { repoDir: isRepoDir(envDir) ? envDir : null, source: "env" };
  const remembered = readSettings(userDataDir).repoDir;
  if (isRepoDir(remembered)) return { repoDir: remembered, source: "settings" };
  if (isRepoDir(configDir)) return { repoDir: configDir, source: "config" };
  return { repoDir: null, source: null };
}

module.exports = { findRepoDir, isRepoDir, readSettings, rememberRepoDir };
