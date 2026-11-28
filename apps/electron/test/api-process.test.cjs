const assert = require("node:assert/strict");
const net = require("node:net");
const { test } = require("node:test");

const { startFailure } = require("../api-process.cjs");

function listen() {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.listen(0, "127.0.0.1", () => resolve(server));
  });
}

test("a port held by another program is named with the command to find it", async () => {
  const server = await listen();
  const { port } = server.address();
  try {
    const message = await startFailure(`http://127.0.0.1:${port}`, 3);
    assert.match(message, new RegExp(`포트 ${port}`));
    assert.match(message, new RegExp(`lsof -nP -iTCP:${port} -sTCP:LISTEN`));
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});

test("a free port keeps the exit code", async () => {
  const server = await listen();
  const { port } = server.address();
  await new Promise((resolve) => server.close(resolve));
  assert.equal(
    await startFailure(`http://127.0.0.1:${port}`, 3),
    "the local API stopped while starting (exit 3)",
  );
});

const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const { residentService, waitForApi } = require("../api-process.cjs");

test("the resident service is found only when its plist is installed", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "siw-resident-"));
  const plist = path.join(dir, "com.siw.api.serve.plist");
  assert.equal(residentService(plist), null);
  fs.writeFileSync(plist, "<plist/>");
  assert.equal(residentService(plist), plist);
});

test("waiting for a restarting API succeeds once it answers", async () => {
  const server = await listen();
  const { port } = server.address();
  await new Promise((resolve) => server.close(resolve));
  const api = http.createServer((_req, res) => res.end("ok"));
  // The API comes back a moment after the wait starts, as after a deploy.
  setTimeout(() => api.listen(port, "127.0.0.1"), 300);
  try {
    assert.equal(
      await waitForApi(`http://127.0.0.1:${port}`, {
        timeoutMs: 5000,
        intervalMs: 100,
      }),
      true,
    );
  } finally {
    await new Promise((resolve) => api.close(resolve));
  }
});

test("waiting gives up when the API never answers", async () => {
  const server = await listen();
  const { port } = server.address();
  await new Promise((resolve) => server.close(resolve));
  assert.equal(
    await waitForApi(`http://127.0.0.1:${port}`, {
      timeoutMs: 300,
      intervalMs: 100,
    }),
    false,
  );
});
