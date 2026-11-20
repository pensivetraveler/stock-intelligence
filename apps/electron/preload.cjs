// The one thing the SIW Research screen may ask of the shell: open SIW Agent. It takes
// no arguments, so the screen cannot choose what is started. The shell may also tell
// the screen that its Help menu asked for the guide (help-menu.cjs) or that its app
// menu asked for the settings (settings-menu.cjs).
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("siwDesktop", {
  openAgentApp: () => ipcRenderer.invoke("siw:open-agent-app"),
  onShowHelp: (callback) => {
    const listener = () => callback();
    ipcRenderer.on("siw:show-help", listener);
    return () => ipcRenderer.removeListener("siw:show-help", listener);
  },
  onShowSettings: (callback) => {
    const listener = () => callback();
    ipcRenderer.on("siw:show-settings", listener);
    return () => ipcRenderer.removeListener("siw:show-settings", listener);
  },
});
