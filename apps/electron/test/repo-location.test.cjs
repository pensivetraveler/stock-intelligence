const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { test } = require("node:test");

const {
  findRepoDir,
  readSettings,
  rememberRepoDir,
} = require("../repo-location.cjs");

function tempDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "siw-repo-"));
}
function fakeRepo() {
  const dir = tempDir();
  fs.mkdirSync(path.join(dir, "services/api"), { recursive: true });
  fs.writeFileSync(path.join(dir, "services/api/main.py"), "");
  return dir;
}

test("SIW_REPO_DIR wins and is final even when wrong", () => {
  const repo = fakeRepo();
  const userData = tempDir();
  rememberRepoDir(userData, fakeRepo());
  assert.deepEqual(
    findRepoDir({ envDir: repo, userDataDir: userData, configDir: null }),
    {
      repoDir: repo,
      source: "env",
    },
  );
  assert.deepEqual(
    findRepoDir({ envDir: tempDir(), userDataDir: userData, configDir: repo }),
    { repoDir: null, source: "env" },
  );
});

test("a remembered folder comes before the packaged one", () => {
  const remembered = fakeRepo();
  const userData = tempDir();
  rememberRepoDir(userData, remembered);
  assert.deepEqual(
    findRepoDir({
      envDir: undefined,
      userDataDir: userData,
      configDir: fakeRepo(),
    }),
    { repoDir: remembered, source: "settings" },
  );
});

test("a moved repository falls through, and nothing valid gives null", () => {
  const userData = tempDir();
  rememberRepoDir(userData, path.join(tempDir(), "moved-away"));
  const packaged = fakeRepo();
  assert.deepEqual(
    findRepoDir({ envDir: "", userDataDir: userData, configDir: packaged }),
    { repoDir: packaged, source: "config" },
  );
  assert.deepEqual(
    findRepoDir({
      envDir: "",
      userDataDir: userData,
      configDir: "/nonexistent",
    }),
    { repoDir: null, source: null },
  );
});

test("remembering keeps other settings and survives a broken file", () => {
  const userData = tempDir();
  fs.writeFileSync(path.join(userData, "settings.json"), "{not json");
  assert.deepEqual(readSettings(userData), {});
  fs.writeFileSync(path.join(userData, "settings.json"), '{"other": 1}');
  rememberRepoDir(userData, "/a");
  assert.deepEqual(readSettings(userData), { other: 1, repoDir: "/a" });
});
