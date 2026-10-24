// Where SIW Agent (the Tauri app, ADR 0036) is on this computer, for the "open SIW
// Agent" button. SIW Research only starts it; orders stay in SIW Agent. Candidates in
// order: SIW_AGENT_APP, the installed app in /Applications or ~/Applications, then the
// bundle or binary a `tauri build` left in the repository.
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const APP_NAME = "SIW Agent.app";
const BINARY = "stock-intelligence-workstation";

function agentCandidates({ envPath, repoDir, home = os.homedir() }) {
  const candidates = [];
  if (envPath) candidates.push(envPath);
  candidates.push(path.join("/Applications", APP_NAME));
  candidates.push(path.join(home, "Applications", APP_NAME));
  if (repoDir) {
    const release = path.join(repoDir, "apps/desktop/src-tauri/target/release");
    candidates.push(path.join(release, "bundle/macos", APP_NAME));
    candidates.push(path.join(release, BINARY));
  }
  return candidates;
}

/** The first candidate that exists: { path, kind: "app" | "binary" }, or null. */
function findAgentApp(options, exists = fs.existsSync) {
  for (const candidate of agentCandidates(options)) {
    if (exists(candidate)) {
      return {
        path: candidate,
        kind: candidate.endsWith(".app") ? "app" : "binary",
      };
    }
  }
  return null;
}

module.exports = { agentCandidates, findAgentApp };
