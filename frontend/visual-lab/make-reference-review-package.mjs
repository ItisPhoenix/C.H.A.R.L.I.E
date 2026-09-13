import { createRequire } from "node:module";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VISUAL_LAB_PLAYWRIGHT_PATH || "playwright");

const scriptDir = dirname(fileURLToPath(import.meta.url));
const root = resolve(scriptDir, "..", "..");
const reviewDir = resolve(root, "artifacts", process.env.VISUAL_LAB_REVIEW_DIR || "pass5d-ref-review");
const implementationDir = resolve(root, "artifacts", process.env.VISUAL_LAB_OUTPUT_DIR || "pass5d-ref-review/implementation");
const referenceCandidate = process.env.VISUAL_LAB_REFERENCE_DIR || "D:\\C.H.A.R.L.I.E-visual-references\\final-14";
const referenceFallback = "D:\\C.H.A.R.L.I.E-visual-referencesfinal-14";
const referenceDir = existsSync(referenceCandidate) ? referenceCandidate : referenceFallback;
const classification = "TEST/MOCK VISUAL COMPARISON ONLY";
const dataLabel = "VISUAL REFERENCE ONLY · REAL DATA / UNAVAILABLE STATE";
const scenarios = [
  ["ref-idle", "Idle centered", "01_idle_centered.png"],
  ["ref-online", "Online centered", "02_online_centered.png"],
  ["ref-research", "Research base", "03_research_base.png"],
  ["ref-research-selected", "Research selected anomaly", "04_research_selected.png"],
  ["ref-vision", "Vision media base", "05_vision_base.png"],
  ["ref-vision-selected", "Vision selected target", "06_vision_selected.png"],
  ["ref-briefing", "Briefing base", "07_briefing_base.png"],
  ["ref-briefing-alt", "Briefing alternate", "08_briefing_alt.png"],
  ["ref-system-tasks", "System tasks", "09_system_tasks.png"],
  ["ref-active-docked", "Active docked core", "10_active_docked_core.png"],
  ["ref-approval", "Approval", "11_approval.png"],
  ["ref-settings", "Settings", "12_settings.png"],
  ["ref-fault", "System fault", "13_fault.png"],
  ["ref-degraded", "Degraded", "14_degraded.png"],
];
const viewports = [
  [1920, 1080],
  [1366, 768],
  [800, 600],
];
const dataAudit = [
  { screen: "ref-idle", fields: "core phase, label, detail", source: "useCharlieStore.visualRuntime via charlie_state", real_host_capable: true, wired: "REAL BUT NOT CURRENTLY AVAILABLE", freshness: "event timestamp", fallback: "NOT VERIFIED" },
  { screen: "ref-online", fields: "CPU/RAM/GPU/DISK/network history", source: "useCharlieStore.systemStatus/system_status", real_host_capable: true, wired: "REAL BUT NOT CURRENTLY AVAILABLE", freshness: "systemStatusUpdatedAt", fallback: "UNAVAILABLE" },
  { screen: "ref-research", fields: "query, findings, sources, timeline, optional map/chart/heatmap", source: "research presentation_intent and latestResearchResult", real_host_capable: true, wired: "REAL BUT NOT CURRENTLY AVAILABLE", freshness: "presentation event timestamp", fallback: "DATA CONTRACT MISSING / NOT WIRED for optional spatial fields" },
  { screen: "ref-research-selected", fields: "selected finding/context", source: "no selected-context frontend contract", real_host_capable: false, wired: "NOT WIRED", freshness: "none", fallback: "NO SELECTED FINDING AVAILABLE" },
  { screen: "ref-vision", fields: "image frame, bounding boxes", source: "vision presentation_intent; vision_observed is not stored", real_host_capable: true, wired: "NOT WIRED", freshness: "event timestamp when available", fallback: "NO LIVE MEDIA FEED AVAILABLE" },
  { screen: "ref-vision-selected", fields: "selected target metadata, route, attachments", source: "no target-analysis frontend contract", real_host_capable: false, wired: "NOT WIRED", freshness: "none", fallback: "TARGET METADATA UNAVAILABLE" },
  { screen: "ref-briefing", fields: "headline, summary, stories, sources, timeline, optional geo_data", source: "briefing presentation_intent", real_host_capable: true, wired: "REAL BUT NOT CURRENTLY AVAILABLE", freshness: "presentation event timestamp", fallback: "DATA CONTRACT MISSING / NOT WIRED for optional geo_data" },
  { screen: "ref-briefing-alt", fields: "alternate briefing payload", source: "briefing presentation_intent", real_host_capable: true, wired: "REAL BUT NOT CURRENTLY AVAILABLE", freshness: "presentation event timestamp", fallback: "NO BRIEFING HEADLINE AVAILABLE" },
  { screen: "ref-system-tasks", fields: "system status, health, tasks, activities, optional topology/processes/logs", source: "system_status, subsystem_health, task_snapshot, charlie_state", real_host_capable: true, wired: "REAL BUT NOT CURRENTLY AVAILABLE", freshness: "per-snapshot timestamp", fallback: "DATA CONTRACT MISSING / NOT WIRED for topology/process/PID fields" },
  { screen: "ref-active-docked", fields: "core runtime state", source: "useCharlieStore.visualRuntime", real_host_capable: true, wired: "REAL BUT NOT CURRENTLY AVAILABLE", freshness: "event timestamp", fallback: "NOT VERIFIED" },
  { screen: "ref-approval", fields: "request id, tool, reason, risk class", source: "activeToolApproval from tool_approval_request replay", real_host_capable: true, wired: "REAL BUT NOT CURRENTLY AVAILABLE", freshness: "approval event timestamp", fallback: "NO PENDING APPROVAL" },
  { screen: "ref-settings", fields: "config fields and health projection", source: "GET /api/config and GET /api/health", real_host_capable: true, wired: "REAL BUT NOT CURRENTLY AVAILABLE", freshness: "request time / backend projection", fallback: "RUNTIME CONFIGURATION UNAVAILABLE" },
  { screen: "ref-fault", fields: "runtime phase/detail/recovery proposal", source: "useCharlieStore.visualRuntime", real_host_capable: true, wired: "REAL BUT NOT CURRENTLY AVAILABLE", freshness: "event timestamp", fallback: "SPECIFIC FAULT CONTEXT UNAVAILABLE" },
  { screen: "ref-degraded", fields: "subsystem status/detail and runtime detail", source: "subsystem_health/runtime_truth projection and visualRuntime", real_host_capable: true, wired: "REAL BUT NOT CURRENTLY AVAILABLE", freshness: "snapshot timestamp", fallback: "SUBSYSTEM HEALTH UNAVAILABLE" },
];

if (!existsSync(referenceDir)) throw new Error(`Reference folder not found: ${referenceCandidate} or ${referenceFallback}`);
mkdirSync(reviewDir, { recursive: true });
mkdirSync(implementationDir, { recursive: true });
const comparisonDir = resolve(reviewDir, "references-vs-implementation");
mkdirSync(comparisonDir, { recursive: true });

function implementationPath(width, height, scenario) {
  return resolve(implementationDir, `pass5d-ref-${width}x${height}-${scenario}.png`);
}

function referencePath(file) {
  return resolve(referenceDir, file);
}

function imageData(path) {
  return `data:image/png;base64,${readFileSync(path).toString("base64")}`;
}

function sheetMarkup(images, title) {
  return `<!doctype html><meta charset="utf-8"><style>
  *{box-sizing:border-box}body{margin:0;background:#030a11;color:#dff8ff;font:11px 'JetBrains Mono',monospace;letter-spacing:.08em}
  header{padding:24px 28px 14px;border-bottom:1px solid rgba(72,190,222,.28);text-transform:uppercase}main{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;padding:18px 24px 28px}
  figure{margin:0;padding:8px;border:1px solid rgba(72,190,222,.3);background:#061521}img{display:block;width:100%;height:auto;background:#010408}figcaption{padding:10px 2px 2px;color:#77d8ef;font-size:10px;text-transform:uppercase}
  small{display:block;margin-top:5px;color:#7896a4;font-size:8px}
  </style><header>${title}<small>${classification} · ${dataLabel}</small></header><main>${images.map(({ src, caption }) => `<figure><img src="${src}"><figcaption>${caption}<small>${classification} · ${dataLabel}</small></figcaption></figure>`).join("")}</main>`;
}

async function renderSheet(browser, outputPath, title, images) {
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  await page.setContent(sheetMarkup(images, title));
  await page.screenshot({ path: outputPath, fullPage: true });
  await page.close();
}

async function renderComparison(browser, outputPath, reference, implementation, label) {
  const page = await browser.newPage({ viewport: { width: 1920, height: 620 } });
  await page.setContent(`<!doctype html><meta charset="utf-8"><style>*{box-sizing:border-box}body{margin:0;background:#02070d;color:#e2f7fc;font:11px 'JetBrains Mono',monospace;letter-spacing:.08em}header{height:58px;padding:13px 20px;border-bottom:1px solid rgba(82,184,215,.35);text-transform:uppercase}main{display:grid;grid-template-columns:1fr 1fr;gap:10px;padding:10px}figure{position:relative;margin:0;overflow:hidden;border:1px solid rgba(82,184,215,.32);background:#061520}img{display:block;width:100%;height:auto}figcaption{position:absolute;right:0;bottom:0;left:0;padding:8px 10px;background:rgba(1,7,13,.84);color:#8bdff0;font-size:9px;text-transform:uppercase}</style><header>${label}<small style="display:block;margin-top:5px;color:#7998a5;font-size:8px">${classification} · ${dataLabel}</small></header><main><figure><img src="${imageData(reference)}"><figcaption>LEFT · CANONICAL REFERENCE · VISUAL REFERENCE ONLY</figcaption></figure><figure><img src="${imageData(implementation)}"><figcaption>RIGHT · REAL DATA / UNAVAILABLE STATE</figcaption></figure></main>`);
  await page.screenshot({ path: outputPath, fullPage: true });
  await page.close();
}

const browser = await chromium.launch({
  headless: process.env.VISUAL_LAB_HEADLESS !== "false",
  ...(process.env.VISUAL_LAB_BROWSER_PATH ? { executablePath: process.env.VISUAL_LAB_BROWSER_PATH } : {}),
});

for (const [width, height] of viewports) {
  const images = scenarios.map(([scenario, label]) => ({
    src: imageData(implementationPath(width, height, scenario)),
    caption: `${scenario} · ${label} · ${width}×${height}`,
  }));
  await renderSheet(browser, resolve(reviewDir, `${width}-contact-sheet.png`), `${width}×${height} implementation contact sheet`, images);
}

for (const [index, [scenario, label, reference]] of scenarios.entries()) {
  await renderComparison(
    browser,
    resolve(comparisonDir, `compare-${String(index + 1).padStart(2, "0")}.png`),
    referencePath(reference),
    implementationPath(1920, 1080, scenario),
    `${scenario} · ${label} · 1920×1080`,
  );
}

const comparisonImages = scenarios.map(([scenario, label], index) => ({
  src: imageData(resolve(comparisonDir, `compare-${String(index + 1).padStart(2, "0")}.png`)),
  caption: `${scenario} · ${label}`,
}));
await renderSheet(browser, resolve(reviewDir, "comparison-contact-sheet.png"), "Canonical reference vs implementation", comparisonImages);
await browser.close();

const evidence = scenarios.flatMap(([scenario, label]) => viewports.map(([width, height]) => ({
  scenario,
  label,
  viewport: `${width}x${height}`,
  path: `implementation/pass5d-ref-${width}x${height}-${scenario}.png`,
  classification,
})));

writeFileSync(resolve(reviewDir, "acceptance-ledger.json"), `${JSON.stringify({
  scope: scenarios.map(([scenario]) => scenario),
  status: "in_progress",
  visual_review: "unverified",
  remaining_work: "SOL/HUMAN pixel review and visual signoff",
  requirements: [
    { id: "reference-locked-14-screens", status: "in_progress", evidence, remaining_work: "Human comparison against canonical references." },
    { id: "centered-docked-ring", status: "in_progress", evidence: evidence.filter(({ scenario }) => ["ref-idle", "ref-online", "ref-fault", "ref-degraded", "ref-active-docked"].includes(scenario)), remaining_work: "Human confirmation of scale, geometry, and dock offset." },
    { id: "responsive-1920-1366-800", status: "in_progress", evidence, remaining_work: "Human confirmation that hierarchy and readability hold at each viewport." },
    { id: "comparison-artifacts", status: "passed", evidence: [{ path: "references-vs-implementation/", classification }, { path: "comparison-contact-sheet.png", classification }], remaining_work: "None for artifact generation." },
  ],
  data_source_audit: dataAudit,
  not_wired_fields: dataAudit.filter(({ wired }) => wired === "NOT WIRED" || wired.includes("NOT CURRENTLY")),
  classification,
  reference_directory: referenceDir,
  implementation_directory: implementationDir,
  comparison_directory: comparisonDir,
}, null, 2)}\n`, "utf8");

writeFileSync(resolve(reviewDir, "VISUAL-REVIEW.txt"), [
  `All screenshots are ${classification}.`,
  `Comparison data label: ${dataLabel}.`,
  `Reference directory: ${referenceDir}`,
  `Implementation directory: ${implementationDir}`,
  `Comparison directory: ${comparisonDir}`,
  "",
  ...evidence.map(({ scenario, label, viewport, path }) => `${scenario} · ${label}\n  viewport: ${viewport}\n  implementation: ${path}\n  classification: ${classification}`),
  "",
  "Comparison artifacts",
  ...scenarios.map(([scenario, label], index) => `${scenario} · ${label}\n  comparison: references-vs-implementation/compare-${String(index + 1).padStart(2, "0")}.png\n  classification: ${classification}`),
].join("\n\n"), "utf8");

console.log(`Generated ${evidence.length} implementation references, ${scenarios.length} comparisons, and 4 contact sheets in ${reviewDir}`);
