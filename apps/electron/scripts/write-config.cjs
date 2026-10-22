// Records where this repository is, so the packaged app can start the local API from
// it. Only a path: keys stay in the OS keychain and .env, never in the app.
const fs = require("node:fs");
const path = require("node:path");

const repoDir = path.resolve(__dirname, "../../..");
fs.writeFileSync(
  path.resolve(__dirname, "../siw-config.json"),
  `${JSON.stringify({ repoDir, apiUrl: "http://127.0.0.1:8765" }, null, 2)}\n`,
);
console.log(`siw-config.json: repoDir=${repoDir}`);
