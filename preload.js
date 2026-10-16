const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("app", {
  min: () => ipcRenderer.send("win:min"),
  max: () => ipcRenderer.send("win:max"),
  close: () => ipcRenderer.send("win:close"),
  isMax: () => ipcRenderer.invoke("win:isMax"),
});
