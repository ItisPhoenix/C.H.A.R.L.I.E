import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

const apiTarget = "http://127.0.0.1:8001";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: apiTarget,
        changeOrigin: true,
        headers: { Origin: apiTarget },
      },
    },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.ts"],
  },
});
