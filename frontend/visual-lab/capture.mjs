import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const captureMode = process.env.VISUAL_LAB_PASS || "5b";
const outputDir = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
  "artifacts",
  process.env.VISUAL_LAB_OUTPUT_DIR || "pass5b-visual-lab",
);
const outputPrefix = process.env.VISUAL_LAB_OUTPUT_PREFIX || "pass5";
const baseUrl = process.env.VISUAL_LAB_URL || "http://127.0.0.1:5173";
mkdirSync(outputDir, { recursive: true });

const pass5bCapturePlan = [
  {
    viewport: [1920, 1080],
    scenarios: ["idle", "conversation-rich", "research-rich", "briefing-rich", "system-rich", "tasks-rich", "settings", "approval", "error", "recovery", "degraded"],
  },
  {
    viewport: [1366, 768],
    scenarios: ["conversation-rich", "research-rich", "system-rich", "settings"],
  },
  {
    viewport: [800, 600],
    scenarios: ["idle", "conversation-rich", "system-rich", "settings", "approval"],
  },
];

const pass5cCapturePlan = [1920, 1366, 800].map((width) => ({
  viewport: [width, width === 1920 ? 1080 : width === 1366 ? 768 : 600],
  scenarios: ["idle", "research-rich", "system-rich", "settings", "approval"],
}));

const capturePlan = captureMode === "5c-a" ? pass5cCapturePlan : pass5bCapturePlan;
const browser = await chromium.launch({ headless: process.env.VISUAL_LAB_HEADLESS !== "false" });
const page = await browser.newPage();
for (const { viewport: [width, height], scenarios } of capturePlan) {
  await page.setViewportSize({ width, height });
  for (const scenario of scenarios) {
    await page.goto(`${baseUrl}/visual-lab.html?scenario=${scenario}`, { waitUntil: "networkidle" });
    await page.locator(".charlie-scene-root").waitFor({ state: "visible" });
    await page.evaluate(() => document.fonts?.ready);
    await page.screenshot({
      path: resolve(outputDir, `${outputPrefix}-${width}x${height}-${scenario}.png`),
      fullPage: false,
    });
  }
}
await browser.close();
