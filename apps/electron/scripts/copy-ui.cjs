// Copies the built desktop screen (apps/desktop/dist) into this package as ui/.
const fs = require("node:fs");
const path = require("node:path");

const source = path.resolve(__dirname, "../../desktop/dist");
const target = path.resolve(__dirname, "../ui");
if (!fs.existsSync(path.join(source, "index.html"))) {
  throw new Error(`no built screen at ${source}; run the desktop build first`);
}
fs.rmSync(target, { recursive: true, force: true });
fs.cpSync(source, target, { recursive: true });
console.log(`copied ${source} -> ${target}`);
