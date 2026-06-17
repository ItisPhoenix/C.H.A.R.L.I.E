const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  setIgnoreMouseEvents: (ignore) => ipcRenderer.send('set-ignore-mouse', ignore),
  toggleExpand: () => ipcRenderer.send('toggle-expand'),
  onModeChange: (callback) => ipcRenderer.on('mode', (event, mode) => callback(mode)),
  onToggleExpandKey: (callback) => ipcRenderer.on('toggle-expand-key', () => callback()),
  dragStart: (mouseX, mouseY) => ipcRenderer.send('drag-start', { mouseX, mouseY }),
  dragMove: (mouseX, mouseY) => ipcRenderer.send('drag-move', { mouseX, mouseY }),
});