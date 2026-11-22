// "설정…" in the app menu of SIW Research (디자인-siw-2): the menu only sends "show the
// settings"; the screen opens its settings dialog. Same shape as the Help menu channel
// (help-menu.cjs, siw:show-help).

/** IPC channel from the shell to the screen (preload.cjs listens on it). */
const SHOW_SETTINGS = "siw:show-settings";

/** The app-menu item; `showSettings` runs when it is chosen. */
function settingsMenuItem(showSettings) {
  return {
    label: "설정…",
    accelerator: "CmdOrCtrl+,",
    click: () => showSettings(),
  };
}

/** Asks the first usable window to show its settings; false if there is none. */
function showSettingsIn(windows) {
  const window = windows.find((item) => item && !item.isDestroyed());
  if (!window) return false;
  if (window.isMinimized()) window.restore();
  window.focus();
  window.webContents.send(SHOW_SETTINGS);
  return true;
}

module.exports = { SHOW_SETTINGS, settingsMenuItem, showSettingsIn };
