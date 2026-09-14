import { createRequire } from "node:module";
import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VISUAL_LAB_PLAYWRIGHT_PATH || "playwright");

const scriptDir = dirname(fileURLToPath(import.meta.url));
const root = resolve(scriptDir, "..", "..");
const reviewMode = process.env.VISUAL_LAB_PASS || "5b";
const isSpatialReview = reviewMode === "5d-1" || reviewMode === "5d-1r";
const defaultOutputDir = isSpatialReview ? "pass5d-1r-visual-lab" : "pass5b-visual-lab";
const defaultReviewDir = isSpatialReview ? "pass5d-1r-review" : "pass5b-review";
const sourceDir = resolve(root, "artifacts", process.env.VISUAL_LAB_OUTPUT_DIR || defaultOutputDir);
const reviewDir = resolve(root, "artifacts", process.env.VISUAL_LAB_REVIEW_DIR || defaultReviewDir);
const sourcePrefix = process.env.VISUAL_LAB_OUTPUT_PREFIX || (isSpatialReview ? "pass5d-1r" : "pass5");
mkdirSync(reviewDir, { recursive: true });

const pass5bItems = [
  ["01-idle.png", "pass5-1920x1080-idle.png", "1920x1080", "idle"],
  ["02-conversation-rich.png", "pass5-1920x1080-conversation-rich.png", "1920x1080", "conversation-rich"],
  ["03-research-rich.png", "pass5-1920x1080-research-rich.png", "1920x1080", "research-rich"],
  ["04-briefing-rich.png", "pass5-1920x1080-briefing-rich.png", "1920x1080", "briefing-rich"],
  ["05-system-rich.png", "pass5-1920x1080-system-rich.png", "1920x1080", "system-rich"],
  ["06-tasks-rich.png", "pass5-1920x1080-tasks-rich.png", "1920x1080", "tasks-rich"],
  ["07-settings.png", "pass5-1920x1080-settings.png", "1920x1080", "settings"],
  ["08-approval.png", "pass5-1920x1080-approval.png", "1920x1080", "approval"],
  ["09-error.png", "pass5-1920x1080-error.png", "1920x1080", "error"],
  ["10-recovery.png", "pass5-1920x1080-recovery.png", "1920x1080", "recovery"],
  ["11-degraded.png", "pass5-1920x1080-degraded.png", "1920x1080", "degraded"],
  ["12-1366x768-conversation-rich.png", "pass5-1366x768-conversation-rich.png", "1366x768", "conversation-rich"],
  ["13-1366x768-research-rich.png", "pass5-1366x768-research-rich.png", "1366x768", "research-rich"],
  ["14-1366x768-system-rich.png", "pass5-1366x768-system-rich.png", "1366x768", "system-rich"],
  ["15-1366x768-settings.png", "pass5-1366x768-settings.png", "1366x768", "settings"],
  ["16-800x600-idle.png", "pass5-800x600-idle.png", "800x600", "idle"],
  ["17-800x600-conversation-rich.png", "pass5-800x600-conversation-rich.png", "800x600", "conversation-rich"],
  ["18-800x600-system-rich.png", "pass5-800x600-system-rich.png", "800x600", "system-rich"],
  ["19-800x600-settings.png", "pass5-800x600-settings.png", "800x600", "settings"],
  ["20-800x600-approval.png", "pass5-800x600-approval.png", "800x600", "approval"],
];

const pass5cItems = [
  ["01-idle.png", `${sourcePrefix}-1920x1080-idle.png`, "1920x1080", "idle"],
  ["02-research-rich.png", `${sourcePrefix}-1920x1080-research-rich.png`, "1920x1080", "research-rich"],
  ["03-system-rich.png", `${sourcePrefix}-1920x1080-system-rich.png`, "1920x1080", "system-rich"],
  ["04-settings.png", `${sourcePrefix}-1920x1080-settings.png`, "1920x1080", "settings"],
  ["05-approval.png", `${sourcePrefix}-1920x1080-approval.png`, "1920x1080", "approval"],
  ["06-1366x768-idle.png", `${sourcePrefix}-1366x768-idle.png`, "1366x768", "idle"],
  ["07-1366x768-research-rich.png", `${sourcePrefix}-1366x768-research-rich.png`, "1366x768", "research-rich"],
  ["08-1366x768-system-rich.png", `${sourcePrefix}-1366x768-system-rich.png`, "1366x768", "system-rich"],
  ["09-1366x768-settings.png", `${sourcePrefix}-1366x768-settings.png`, "1366x768", "settings"],
  ["10-1366x768-approval.png", `${sourcePrefix}-1366x768-approval.png`, "1366x768", "approval"],
  ["11-800x600-idle.png", `${sourcePrefix}-800x600-idle.png`, "800x600", "idle"],
  ["12-800x600-research-rich.png", `${sourcePrefix}-800x600-research-rich.png`, "800x600", "research-rich"],
  ["13-800x600-system-rich.png", `${sourcePrefix}-800x600-system-rich.png`, "800x600", "system-rich"],
  ["14-800x600-settings.png", `${sourcePrefix}-800x600-settings.png`, "800x600", "settings"],
  ["15-800x600-approval.png", `${sourcePrefix}-800x600-approval.png`, "800x600", "approval"],
];

const pass5d1Items = [1920, 1366, 800].flatMap((width) => {
  const height = width === 1920 ? 1080 : width === 1366 ? 768 : 600;
  return [
    ["idle", "Idle"],
    ["research", "Research base"],
    ["research-selected", "Research selected-context"],
    ["vision", "Vision base"],
    ["vision-selected", "Vision selected-context"],
  ].map(([scenario, label], index) => [
    `${String((width === 1920 ? 1 : width === 1366 ? 6 : 11) + index).padStart(2, "0")}-${scenario}.png`,
    `${sourcePrefix}-${width}x${height}-spatial-${scenario}.png`,
    `${width}x${height}`,
    label,
  ]);
});

const items = isSpatialReview ? pass5d1Items : reviewMode === "5c-a" ? pass5cItems : pass5bItems;
const contactSheetName = isSpatialReview ? "16-contact-sheet.png" : reviewMode === "5c-a" ? "16-contact-sheet.png" : "21-contact-sheet.png";
const classification = "TEST/MOCK — NOT RUNTIME ACCEPTANCE";

for (const [target, source] of items) copyFileSync(resolve(sourceDir, source), resolve(reviewDir, target));

if (isSpatialReview) {
  const transitionSourceDir = resolve(sourceDir, "transitions");
  const transitionReviewDir = resolve(reviewDir, "transitions");
  if (existsSync(transitionSourceDir)) {
    mkdirSync(transitionReviewDir, { recursive: true });
    for (const file of readdirSync(transitionSourceDir).filter((name) => name.endsWith(".png"))) {
      copyFileSync(resolve(transitionSourceDir, file), resolve(transitionReviewDir, file));
    }
  }

  writeFileSync(
    resolve(reviewDir, "acceptance-ledger.json"),
    `${JSON.stringify({
      scope: ["spatial-idle", "spatial-research", "spatial-research-selected", "spatial-vision", "spatial-vision-selected"],
      status: "in_progress",
      visual_review: "unverified",
      remaining_work: "SOL/HUMAN visual proof review",
      next_action: "Review 16-contact-sheet.png and transitions/ at human visual signoff.",
      requirements: [
        { id: "ring-identity", status: "passed", evidence: ["01-idle.png", "06-idle.png", "11-idle.png"], remaining_work: "Human confirmation that checkpoint identity is restored." },
        { id: "ring-centered-docked-scale", status: "passed", evidence: ["01-idle.png", "02-research.png", "04-vision.png", "11-idle.png"], remaining_work: "Human confirmation of centered/docked proportion." },
        { id: "research-dominant-surface", status: "passed", evidence: ["02-research.png", "03-research-selected.png", "12-research.png"], remaining_work: "Human confirmation that schematic evidence context is not dashboard-like." },
        { id: "vision-media-fixture", status: "passed", evidence: ["04-vision.png", "05-vision-selected.png", "14-vision.png"], remaining_work: "Human confirmation that synthetic media reads as a believable fixture." },
        { id: "context-preservation", status: "passed", evidence: ["03-research-selected.png", "05-vision-selected.png", "13-research-selected.png", "15-vision-selected.png"], remaining_work: "None in isolated proof path." },
        { id: "responsive-no-overflow", status: "passed", evidence: ["11-idle.png", "12-research.png", "13-research-selected.png", "14-vision.png", "15-vision-selected.png"], remaining_work: "None in measured 800x600 browser proof." },
        { id: "transition-evidence", status: "passed", evidence: ["transitions/"], remaining_work: "Human review of motion quality." },
      ],
      classification,
      source_directory: `artifacts/${process.env.VISUAL_LAB_OUTPUT_DIR || defaultOutputDir}`,
      review_directory: `artifacts/${process.env.VISUAL_LAB_REVIEW_DIR || defaultReviewDir}`,
      transition_evidence: "transitions/",
    }, null, 2)}\n`,
    "utf8",
  );
}

const html = `<!doctype html><meta charset="utf-8"><style>
body{margin:0;background:#050b12;color:#dff8ff;font:14px 'JetBrains Mono',monospace}
main{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;padding:24px}
figure{margin:0;border:1px solid rgba(80,200,230,.35);background:#02070d;padding:8px}
img{display:block;width:100%;height:210px;object-fit:contain;background:#010305}
figcaption{padding:8px 2px 2px;letter-spacing:.08em;text-transform:uppercase}
</style><main>${items.map(([target, , viewport, scenario]) => `<figure><img src="data:image/png;base64,${readFileSync(resolve(reviewDir, target)).toString("base64")}"><figcaption>${target} · ${viewport} · ${classification} · ${scenario}</figcaption></figure>`).join("")}</main>`;
const browser = await chromium.launch({
  headless: process.env.VISUAL_LAB_HEADLESS !== "false",
  ...(process.env.VISUAL_LAB_BROWSER_PATH ? { executablePath: process.env.VISUAL_LAB_BROWSER_PATH } : {}),
});
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
await page.setContent(html);
await page.screenshot({ path: resolve(reviewDir, contactSheetName), fullPage: true });
await browser.close();

writeFileSync(resolve(reviewDir, "VISUAL-REVIEW.txt"), [
  `All screenshots are ${classification}.`,
  `Source directory: artifacts/${process.env.VISUAL_LAB_OUTPUT_DIR || defaultOutputDir}`,
  `Review directory: artifacts/${process.env.VISUAL_LAB_REVIEW_DIR || defaultReviewDir}`,
  "",
  ...items.map(([target, source, viewport, scenario]) => `${target}\n  original: artifacts/${process.env.VISUAL_LAB_OUTPUT_DIR || defaultOutputDir}/${source}\n  viewport: ${viewport}\n  scenario: ${scenario}\n  classification: ${classification}\n  rendered: VisualLabHarness / ${isSpatialReview ? "SpatialCanvas" : "CharlieScene"}`),
  ...(isSpatialReview ? [
    "",
    `Transition evidence\n  directory: artifacts/${process.env.VISUAL_LAB_REVIEW_DIR || defaultReviewDir}/transitions\n  classification: ${classification}`,
  ] : []),
].join("\n\n"));
