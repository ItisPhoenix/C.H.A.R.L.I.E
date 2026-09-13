import { createRequire } from "node:module";
import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VISUAL_LAB_PLAYWRIGHT_PATH || "playwright");

const captureMode = process.env.VISUAL_LAB_PASS || "5b";
const isReferenceReview = captureMode === "5d-ref";
const isSpatialProof = captureMode === "5d-1" || captureMode === "5d-1r";
const defaultOutputDir = isReferenceReview
  ? "pass5d-ref-review/implementation"
  : isSpatialProof ? "pass5d-1r-visual-lab" : "pass5b-visual-lab";
const outputDir = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
  "artifacts",
  process.env.VISUAL_LAB_OUTPUT_DIR || defaultOutputDir,
);
const outputPrefix = process.env.VISUAL_LAB_OUTPUT_PREFIX || (isReferenceReview ? "pass5d-ref" : isSpatialProof ? "pass5d-1r" : "pass5");
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

const pass5d1CapturePlan = [1920, 1366, 800].map((width) => ({
  viewport: [width, width === 1920 ? 1080 : width === 1366 ? 768 : 600],
  scenarios: ["spatial-idle", "spatial-research", "spatial-research-selected", "spatial-vision", "spatial-vision-selected"],
}));

const pass5dRefCapturePlan = [1920, 1366, 800].map((width) => ({
  viewport: [width, width === 1920 ? 1080 : width === 1366 ? 768 : 600],
  scenarios: [
    "ref-idle", "ref-online", "ref-research", "ref-research-selected", "ref-vision", "ref-vision-selected",
    "ref-briefing", "ref-briefing-alt", "ref-system-tasks", "ref-active-docked", "ref-approval", "ref-settings",
    "ref-fault", "ref-degraded",
  ],
}));

const capturePlan = isReferenceReview
  ? pass5dRefCapturePlan
  : isSpatialProof ? pass5d1CapturePlan
  : captureMode === "5c-a" ? pass5cCapturePlan : pass5bCapturePlan;
const browser = await chromium.launch({
  headless: process.env.VISUAL_LAB_HEADLESS !== "false",
  ...(process.env.VISUAL_LAB_BROWSER_PATH ? { executablePath: process.env.VISUAL_LAB_BROWSER_PATH } : {}),
});
const page = await browser.newPage();

async function waitForScenePaint() {
  await page.locator(".charlie-scene-root").waitFor({ state: "visible" });
  await page.locator(".charlie-core-brand-center, .ref-core__wordmark").first().waitFor({ state: "visible" });
  await page.waitForFunction(() => {
    const core = document.querySelector(".charlie-core-brand-center, .ref-core__wordmark");
    const ring = document.querySelector('[data-core-renderer="authoritative-charlie-ring"]');
    return Boolean(core && ring && core.getBoundingClientRect().width > 0 && getComputedStyle(core).opacity !== "0");
  });
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
}

for (const { viewport: [width, height], scenarios } of capturePlan) {
  await page.setViewportSize({ width, height });
  for (const scenario of scenarios) {
    await page.goto(`${baseUrl}/visual-lab.html?scenario=${scenario}`, { waitUntil: "networkidle" });
    await waitForScenePaint();
    await page.evaluate(() => document.fonts?.ready);
    await page.screenshot({
      path: resolve(outputDir, `${outputPrefix}-${width}x${height}-${scenario}.png`),
      fullPage: false,
    });
  }
}

if (isSpatialProof) {
  const transitionDir = resolve(outputDir, "transitions");
  mkdirSync(transitionDir, { recursive: true });
  const transitionPage = page;

  async function waitForProof(scene) {
    await waitForScenePaint();
    await transitionPage.waitForFunction(() => Boolean(window.__CHARLIE_SPATIAL_PROOF__));
    await transitionPage.waitForFunction((expected) => document.querySelector(".spatial-canvas-root")?.getAttribute("data-spatial-scene") === expected, scene);
  }

  async function frame(name) {
    await transitionPage.screenshot({ path: resolve(transitionDir, `${name}.png`), fullPage: false });
  }

  await transitionPage.setViewportSize({ width: 1920, height: 1080 });
  await transitionPage.goto(`${baseUrl}/visual-lab.html?scenario=spatial-idle`, { waitUntil: "networkidle" });
  await waitForProof("idle");
  await frame("01-centered-to-docked-before");
  await transitionPage.evaluate(() => window.__CHARLIE_SPATIAL_PROOF__?.setScene("research"));
  await frame("02-centered-to-docked-during");
  await transitionPage.waitForTimeout(460);
  await waitForProof("research");
  await frame("03-centered-to-docked-after");

  const selectFinding = transitionPage.getByTestId("research-select-finding");
  await selectFinding.click();
  await frame("04-research-selection-open-during");
  await transitionPage.waitForTimeout(240);
  await frame("05-research-selection-open-after");
  await transitionPage.getByRole("button", { name: /Close Recovery improved/i }).click();
  await frame("06-research-selection-close-during");
  await transitionPage.waitForTimeout(240);
  await frame("07-research-selection-close-after");

  await transitionPage.evaluate(() => window.__CHARLIE_SPATIAL_PROOF__?.setScene("idle"));
  await frame("08-docked-to-centered-during");
  await transitionPage.waitForTimeout(460);
  await waitForProof("idle");
  await frame("09-docked-to-centered-after");

  await transitionPage.evaluate(() => window.__CHARLIE_SPATIAL_PROOF__?.setScene("vision"));
  await frame("10-centered-to-docked-vision-during");
  await transitionPage.waitForTimeout(460);
  await waitForProof("vision");
  await frame("11-centered-to-docked-vision-after");

  await transitionPage.locator(".spatial-vision-box").first().click();
  await frame("12-vision-selection-open-during");
  await transitionPage.waitForTimeout(240);
  await frame("13-vision-selection-open-after");
  await transitionPage.getByRole("button", { name: /Close SELECTED REGION/i }).click();
  await frame("14-vision-selection-close-during");
  await transitionPage.waitForTimeout(240);
  await frame("15-vision-selection-close-after");
}

await browser.close();
