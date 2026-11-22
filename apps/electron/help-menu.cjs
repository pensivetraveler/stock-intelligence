// The Help menu of SIW Research: one item that shows the implemented-features guide
// (docs/FEATURES.md, bundled into the screen) in the app window. The menu only sends
// "show the guide"; the screen decides what to show.

/** IPC channel from the shell to the screen (preload.cjs listens on it). */
const SHOW_HELP = "siw:show-help";

/** Items of the Help menu; `showHelp` runs when the guide item is chosen. */
function helpSubmenu(showHelp) {
  return [
    {
      label: "구현 기능 안내",
      accelerator: "F1",
      click: () => showHelp(),
    },
  ];
}

/** Asks the first usable window to show the guide; false if there is none. */
function showHelpIn(windows) {
  const window = windows.find((item) => item && !item.isDestroyed());
  if (!window) return false;
  if (window.isMinimized()) window.restore();
  window.focus();
  window.webContents.send(SHOW_HELP);
  return true;
}

module.exports = { SHOW_HELP, helpSubmenu, showHelpIn };
