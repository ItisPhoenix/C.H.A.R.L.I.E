import { defineConfig } from "vitest/config";
import { loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const apiTarget = loadEnv(mode, ".", "").VITE_CHARLIE_GATEWAY_URL ?? "http://127.0.0.1:8000";
  return {
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
  };
});
