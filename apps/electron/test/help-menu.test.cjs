const assert = require("node:assert/strict");
const { test } = require("node:test");

const { SHOW_HELP, helpSubmenu, showHelpIn } = require("../help-menu.cjs");

function fakeWindow({ destroyed = false, minimized = false } = {}) {
  const calls = [];
  return {
    calls,
    isDestroyed: () => destroyed,
    isMinimized: () => minimized,
    restore: () => calls.push("restore"),
    focus: () => calls.push("focus"),
    webContents: { send: (channel) => calls.push(`send:${channel}`) },
  };
}

test("the Help menu has one guide item that calls showHelp", () => {
  let shown = 0;
  const items = helpSubmenu(() => {
    shown += 1;
  });
  assert.equal(items.length, 1);
  assert.equal(items[0].label, "구현 기능 안내");
  assert.equal(items[0].accelerator, "F1");
  items[0].click();
  assert.equal(shown, 1);
});

test("shows the guide in the first window that is not destroyed", () => {
  const gone = fakeWindow({ destroyed: true });
  const hidden = fakeWindow({ minimized: true });
  assert.equal(showHelpIn([null, gone, hidden]), true);
  assert.deepEqual(gone.calls, []);
  assert.deepEqual(hidden.calls, ["restore", "focus", `send:${SHOW_HELP}`]);
});

test("does nothing without a window", () => {
  assert.equal(showHelpIn([]), false);
  assert.equal(showHelpIn([fakeWindow({ destroyed: true })]), false);
});
