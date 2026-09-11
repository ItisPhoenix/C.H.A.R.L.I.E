import { chromium } from "playwright";
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const root = resolve(scriptDir, "..", "..");
const reviewMode = process.env.VISUAL_LAB_PASS || "5b";
const sourceDir = resolve(root, "artifacts", process.env.VISUAL_LAB_OUTPUT_DIR || "pass5b-visual-lab");
const reviewDir = resolve(root, "artifacts", process.env.VISUAL_LAB_REVIEW_DIR || "pass5b-review");
const sourcePrefix = process.env.VISUAL_LAB_OUTPUT_PREFIX || "pass5";
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

const items = reviewMode === "5c-a" ? pass5cItems : pass5bItems;
const contactSheetName = reviewMode === "5c-a" ? "16-contact-sheet.png" : "21-contact-sheet.png";

for (const [target, source] of items) copyFileSync(resolve(sourceDir, source), resolve(reviewDir, target));

const classification = "TEST/MOCK — NOT RUNTIME ACCEPTANCE";
const html = `<!doctype html><meta charset="utf-8"><style>
body{margin:0;background:#050b12;color:#dff8ff;font:14px 'JetBrains Mono',monospace}
main{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;padding:24px}
figure{margin:0;border:1px solid rgba(80,200,230,.35);background:#02070d;padding:8px}
img{display:block;width:100%;height:210px;object-fit:contain;background:#010305}
figcaption{padding:8px 2px 2px;letter-spacing:.08em;text-transform:uppercase}
</style><main>${items.map(([target, , viewport, scenario]) => `<figure><img src="data:image/png;base64,${readFileSync(resolve(reviewDir, target)).toString("base64")}"><figcaption>${target} · ${viewport} · ${classification} · ${scenario}</figcaption></figure>`).join("")}</main>`;
const browser = await chromium.launch({ headless: process.env.VISUAL_LAB_HEADLESS !== "false" });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
await page.setContent(html);
await page.screenshot({ path: resolve(reviewDir, contactSheetName), fullPage: true });
await browser.close();

writeFileSync(resolve(reviewDir, "VISUAL-REVIEW.txt"), [
  `All screenshots are ${classification}.`,
  `Source directory: artifacts/${process.env.VISUAL_LAB_OUTPUT_DIR || "pass5b-visual-lab"}`,
  `Review directory: artifacts/${process.env.VISUAL_LAB_REVIEW_DIR || "pass5b-review"}`,
  "",
  ...items.map(([target, source, viewport, scenario]) => `${target}\n  original: artifacts/${process.env.VISUAL_LAB_OUTPUT_DIR || "pass5b-visual-lab"}/${source}\n  viewport: ${viewport}\n  scenario: ${scenario}\n  classification: ${classification}\n  rendered: VisualLabHarness / CharlieScene`),
].join("\n\n"));
