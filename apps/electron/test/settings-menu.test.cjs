const assert = require("node:assert/strict");
const { test } = require("node:test");

const {
  SHOW_SETTINGS,
  settingsMenuItem,
  showSettingsIn,
} = require("../settings-menu.cjs");

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

test("the menu item opens settings with ⌘, and only calls back", () => {
  let asked = 0;
  const item = settingsMenuItem(() => {
    asked += 1;
  });
  assert.equal(item.label, "설정…");
  assert.equal(item.accelerator, "CmdOrCtrl+,");
  item.click();
  assert.equal(asked, 1);
});

test("asks the first usable window, restoring it when minimized", () => {
  const gone = fakeWindow({ destroyed: true });
  const small = fakeWindow({ minimized: true });
  assert.equal(showSettingsIn([null, gone, small]), true);
  assert.deepEqual(gone.calls, []);
  assert.deepEqual(small.calls, ["restore", "focus", `send:${SHOW_SETTINGS}`]);
  assert.equal(SHOW_SETTINGS, "siw:show-settings");
});

test("says false when no window can show it", () => {
  assert.equal(showSettingsIn([null, fakeWindow({ destroyed: true })]), false);
});
