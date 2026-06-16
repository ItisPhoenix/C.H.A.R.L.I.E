const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('path');

let mainWindow;
let isExpanded = false;

const COMPACT_BOUNDS = { width: 200, height: 250 };
const EXPANDED_BOUNDS = { width: 800, height: 600 };

function createWindow() {
  mainWindow = new BrowserWindow({
    width: COMPACT_BOUNDS.width,
    height: COMPACT_BOUNDS.height,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    focusable: false,
    skipTaskbar: true,
    resizable: false,
    x: 20,
    y: 20,
    webPreferences: {
      preload: path.join(__dirname, '../preload/preload.mjs'),
      nodeIntegration: false,
      contextIsolation: true,
    },
  });

  mainWindow.loadFile(path.join(__dirname, '../renderer/index.html'));
  mainWindow.setAlwaysOnTop(true, 'screen-saver');
}

// Click-through: transparent areas pass clicks to desktop
ipcMain.on('set-ignore-mouse', (event, ignore) => {
  if (!mainWindow) return;
  if (ignore) {
    mainWindow.setIgnoreMouseEvents(true, { forward: true });
  } else {
    mainWindow.setIgnoreMouseEvents(false);
  }
});

// Expand/collapse dashboard
ipcMain.on('toggle-expand', () => {
  if (!mainWindow) return;

  if (isExpanded) {
    mainWindow.setSize(COMPACT_BOUNDS.width, COMPACT_BOUNDS.height);
    mainWindow.setResizable(false);
    mainWindow.webContents.send('mode', 'compact');
  } else {
    mainWindow.setResizable(true);
    mainWindow.setSize(EXPANDED_BOUNDS.width, EXPANDED_BOUNDS.height);
    mainWindow.webContents.send('mode', 'expanded');
  }
  isExpanded = !isExpanded;
});

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});