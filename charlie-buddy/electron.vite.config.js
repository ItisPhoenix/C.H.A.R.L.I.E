import { defineConfig } from 'electron-vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  main: {
    build: {
      rollupOptions: {
        input: 'src/main.js',
        external: ['electron'],
      },
    },
  },
  preload: {
    build: {
      rollupOptions: {
        input: 'src/preload.js',
        external: ['electron'],
      },
    },
  },
  renderer: {
    plugins: [react()],
    root: 'src/renderer',
    build: {
      rollupOptions: {
        input: path.resolve(__dirname, 'src/renderer/index.html'),
      },
      outDir: path.resolve(__dirname, 'out/renderer'),
    },
  },
});