import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { defineConfig } from "vitest/config";
import type { Plugin } from "vite";
import react from "@vitejs/plugin-react";

function gitValue(args: string[]) {
  try {
    return execFileSync("git", args, {
      cwd: process.cwd(), encoding: "utf8", stdio: ["ignore", "pipe", "ignore"],
    }).trim() || null;
  } catch {
    return null;
  }
}

export function createFrontendBuildIdentity() {
  const gitSha = gitValue(["rev-parse", "HEAD"]);
  const gitStatus = gitSha === null ? null : gitValue(["status", "--porcelain", "--untracked-files=no"]);
  const inputFingerprint = process.env.CHARLIE_FRONTEND_INPUT_FINGERPRINT?.trim();
  return {
    build_id: process.env.CHARLIE_BUILD_ID?.trim() || `frontend-${randomUUID()}`,
    ...(inputFingerprint ? { input_fingerprint: inputFingerprint } : {}),
    git_sha: gitSha,
    dirty: gitStatus === null ? null : Boolean(gitStatus),
    built_at: new Date().toISOString(),
    authority: "vite_build",
  };
}

const escapeHtml = (value: string) => value
  .replaceAll("&", "&amp;")
  .replaceAll('"', "&quot;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;");

export function createFrontendBuildIdentityPlugin(identity = createFrontendBuildIdentity()): Plugin {
  const marker = [
    ["data-charlie-build-id", identity.build_id],
    ["data-charlie-build-built-at", identity.built_at],
    ["data-charlie-build-status", "valid"],
    identity.git_sha === null ? null : ["data-charlie-build-git-sha", identity.git_sha],
    identity.dirty === null ? null : ["data-charlie-build-dirty", String(identity.dirty)],
  ].filter((entry): entry is [string, string] => entry !== null)
    .map(([name, value]) => `${name}="${escapeHtml(value)}"`)
    .join(" ");

  return {
    name: "charlie-build-identity",
    apply: "build",
    transformIndexHtml(html) {
      return html.replace(/<div id=["']root["']/, `<div id="root" ${marker}`);
    },
    generateBundle() {
      this.emitFile({
        type: "asset",
        fileName: "charlie-build.json",
        source: `${JSON.stringify(identity, null, 2)}\n`,
      });
    },
  };
}

const outDir = process.env.CHARLIE_FRONTEND_OUT_DIR?.trim() || "dist";

export default defineConfig({
  plugins: [react(), createFrontendBuildIdentityPlugin()],
  build: { outDir },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    clearMocks: true,
  },
});
