const assert = require("node:assert/strict");
const { test } = require("node:test");

const { agentCandidates, findAgentApp } = require("../agent-app.cjs");

test("looks for SIW Agent in a fixed order and nowhere the screen chooses", () => {
  assert.deepEqual(
    agentCandidates({
      envPath: "/x/SIW Agent.app",
      repoDir: "/repo",
      home: "/Users/me",
    }),
    [
      "/x/SIW Agent.app",
      "/Applications/SIW Agent.app",
      "/Users/me/Applications/SIW Agent.app",
      "/repo/apps/desktop/src-tauri/target/release/bundle/macos/SIW Agent.app",
      "/repo/apps/desktop/src-tauri/target/release/stock-intelligence-workstation",
    ],
  );
});

test("finds the first existing app bundle or binary", () => {
  const options = { repoDir: "/repo", home: "/Users/me" };
  const binary =
    "/repo/apps/desktop/src-tauri/target/release/stock-intelligence-workstation";
  assert.deepEqual(
    findAgentApp(options, (p) => p === binary),
    {
      path: binary,
      kind: "binary",
    },
  );
  assert.deepEqual(
    findAgentApp(
      options,
      (p) => p === "/Applications/SIW Agent.app" || p === binary,
    ),
    { path: "/Applications/SIW Agent.app", kind: "app" },
  );
  assert.equal(
    findAgentApp(options, () => false),
    null,
  );
});
