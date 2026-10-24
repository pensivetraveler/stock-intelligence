// The one thing the SIW Research screen may ask of the shell: open SIW Agent. It takes
// no arguments, so the screen cannot choose what is started.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("siwDesktop", {
  openAgentApp: () => ipcRenderer.invoke("siw:open-agent-app"),
});
