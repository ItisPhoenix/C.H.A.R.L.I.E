const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  setIgnoreMouseEvents: (ignore) => ipcRenderer.send('set-ignore-mouse', ignore),
  toggleExpand: () => ipcRenderer.send('toggle-expand'),
  onModeChange: (callback) => ipcRenderer.on('mode', (event, mode) => callback(mode)),
});