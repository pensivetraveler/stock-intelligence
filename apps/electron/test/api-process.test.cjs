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
