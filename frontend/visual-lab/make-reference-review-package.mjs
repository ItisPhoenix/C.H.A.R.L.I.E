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
const implementationPrefix = process.env.VISUAL_LAB_OUTPUT_PREFIX || "pass5d-ref";
const referenceCandidate = process.env.VISUAL_LAB_REFERENCE_DIR || "D:\\C.H.A.R.L.I.E-visual-references\\final-14";
const referenceFallback = "D:\\C.H.A.R.L.I.E-visual-referencesfinal-14";
const referenceDir = existsSync(referenceCandidate) ? referenceCandidate : referenceFallback;
const allowedClassifications = ["REAL HOST", "SANDBOXED RUNTIME", "TEST/MOCK", "NOT VERIFIED"];
const classification = process.env.VISUAL_LAB_EVIDENCE_CLASSIFICATION || "TEST/MOCK";
if (!allowedClassifications.includes(classification)) throw new Error(`Unsupported evidence classification: ${classification}`);
const dataLabel = classification === "REAL HOST" ? "LIVE RUNTIME DATA" : "NO LIVE BACKEND OBSERVED";
const verification = {
  tests: process.env.VISUAL_LAB_TEST_RESULT || "not recorded",
  build: process.env.VISUAL_LAB_BUILD_RESULT || "not recorded",
  typecheck: process.env.VISUAL_LAB_TYPECHECK_RESULT || "not recorded",
  lint: process.env.VISUAL_LAB_LINT_RESULT || "not recorded",
  diff_check: process.env.VISUAL_LAB_DIFF_CHECK_RESULT || "not recorded",
  server: process.env.VISUAL_LAB_SERVER_RESULT || "not recorded",
  publish: process.env.VISUAL_LAB_PUBLISH_RESULT || "not recorded",
};
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
  { screen: "ref-idle", ui_field: "runtime phase", selector: "useCharlieStore.visualRuntime.phase", source: "charlie_state and progress events over WebSocket", backend: "main runtime event envelope; shared/event_contract.json", freshness: "event timestamp", availability: "Backend refused; no live event observed", classification: "REAL + NOT CURRENTLY AVAILABLE", fallback: "NOT VERIFIED", evidence: "frontend/src/runtime/visualRuntime.ts" },
  { screen: "ref-idle", ui_field: "runtime label/detail", selector: "useCharlieStore.visualRuntime.label/detail", source: "visual runtime reducer", backend: "typed state/progress event payloads", freshness: "event timestamp", availability: "Backend refused; no live event observed", classification: "REAL + NOT CURRENTLY AVAILABLE", fallback: "NOT VERIFIED", evidence: "frontend/src/runtime/visualRuntime.ts" },
  { screen: "ref-idle", ui_field: "connection status", selector: "useCharlieStore.connected", source: "bridge WebSocket open/close", backend: "GET /ws connection", freshness: "connection transition", availability: "127.0.0.1:8000 refused", classification: "REAL + NOT CURRENTLY AVAILABLE", fallback: "NOT VERIFIED", evidence: "frontend/src/runtime/bridge.ts" },
  { screen: "ref-online", ui_field: "CPU usage", selector: "useCharlieStore.systemStatus.cpu", source: "system_status event", backend: "main system_status snapshot", freshness: "systemStatusUpdatedAt", availability: "Backend refused; no live snapshot observed", classification: "REAL + NOT CURRENTLY AVAILABLE", fallback: "UNAVAILABLE", evidence: "frontend/src/store/charlie.ts; shared/event_contract.json" },
  { screen: "ref-online", ui_field: "network history sparkline", selector: "useCharlieStore.netHistory", source: "system_status.net_kbps", backend: "main system_status snapshot", freshness: "systemStatusUpdatedAt", availability: "Backend refused; no live snapshot observed", classification: "REAL + NOT CURRENTLY AVAILABLE", fallback: "NO TELEMETRY HISTORY AVAILABLE", evidence: "frontend/src/store/charlie.ts" },
  { screen: "ref-online", ui_field: "core temperature", selector: "none", source: "none", backend: "no temperature field in system_status or SystemStatus", freshness: "none", availability: "No canonical field", classification: "NOT WIRED", fallback: "UNAVAILABLE", missing_contract: "temperature", nearest_contract: "system_status", consumer: "frontend/src/visual-lab/ReferenceVisualLabTruthful.tsx", why_insufficient: "GPU availability cannot stand in for temperature", backend_required: "yes" },
  { screen: "ref-online", ui_field: "fan speed", selector: "none", source: "none", backend: "no fan field in system_status or SystemStatus", freshness: "none", availability: "No canonical field", classification: "NOT WIRED", fallback: "UNAVAILABLE", missing_contract: "fan_speed", nearest_contract: "system_status", consumer: "frontend/src/visual-lab/ReferenceVisualLabTruthful.tsx", why_insufficient: "network throughput cannot stand in for fan speed", backend_required: "yes" },
  { screen: "ref-research", ui_field: "query/objective", selector: "workspaceIntent.content.query", source: "research presentation_intent", backend: "charlie.research_workspace.query", freshness: "presentation event timestamp", availability: "Backend refused; no live intent observed", classification: "REAL + NOT CURRENTLY AVAILABLE", fallback: "NO RESEARCH OBJECTIVE AVAILABLE", evidence: "shared/workspace_payload_contract.json; charlie/research/presentation.py" },
  { screen: "ref-research", ui_field: "title/mode/status/confidence", selector: "research payload normalization", source: "research presentation_intent.content", backend: "charlie.research_workspace required fields", freshness: "presentation event timestamp", availability: "Backend refused; no live intent observed", classification: "REAL + NOT CURRENTLY AVAILABLE", fallback: "RESEARCH DATA UNAVAILABLE", evidence: "frontend/src/presentation/workspacePayloads.ts" },
  { screen: "ref-research", ui_field: "summary", selector: "researchPayload.summary", source: "research presentation_intent.content", backend: "build_research_workspace_payload", freshness: "presentation event timestamp", availability: "Backend refused; no live intent observed", classification: "REAL + NOT CURRENTLY AVAILABLE", fallback: "NO GROUNDED FINDINGS REPORTED", evidence: "charlie/research/presentation.py" },
  { screen: "ref-research", ui_field: "findings", selector: "researchPayload.findings", source: "research presentation_intent.content", backend: "charlie.research_workspace.findings", freshness: "presentation event timestamp", availability: "Backend refused; no live intent observed", classification: "REAL + NOT CURRENTLY AVAILABLE", fallback: "NO GROUNDED FINDINGS REPORTED", evidence: "frontend/src/visual-lab/ReferenceVisualLabTruthful.tsx" },
  { screen: "ref-research", ui_field: "sources", selector: "researchPayload.sources/latestResearchResult.sources", source: "research_result or presentation_intent", backend: "charlie.research_workspace.sources", freshness: "presentation event timestamp", availability: "Backend refused; no live result observed", classification: "REAL + NOT CURRENTLY AVAILABLE", fallback: "NO SOURCE EVIDENCE AVAILABLE", evidence: "frontend/src/store/charlie.ts; shared/workspace_payload_contract.json" },
  { screen: "ref-research", ui_field: "timeline", selector: "researchPayload.timeline_items", source: "research presentation_intent.content", backend: "optional charlie.research_workspace.timeline_items", freshness: "presentation event timestamp", availability: "Backend refused; no live intent observed", classification: "REAL + NOT CURRENTLY AVAILABLE", fallback: "NO TIMELINE DATA AVAILABLE", evidence: "charlie/research/presentation.py" },
  { screen: "ref-research", ui_field: "geographic map", selector: "content.spatial_map/map_data/map", source: "none observed", backend: "optional spatial_map has no producer in research builder", freshness: "none", availability: "No canonical payload observed", classification: "NOT WIRED", fallback: "GEOGRAPHIC DATA UNAVAILABLE", missing_contract: "research spatial map payload", nearest_contract: "workspace_payload_contract.research.spatial_map", consumer: "frontend/src/visual-lab/ReferenceVisualLabTruthful.tsx", why_insufficient: "renderer accepts shape but backend builder emits no map", backend_required: "yes" },
  { screen: "ref-research", ui_field: "activity chart", selector: "content.chart/chart_data/activity_history", source: "none observed", backend: "optional chart has no producer in research builder", freshness: "none", availability: "No canonical payload observed", classification: "NOT WIRED", fallback: "NO ACTIVITY HISTORY AVAILABLE", missing_contract: "research chart payload", nearest_contract: "workspace_payload_contract.research.chart", consumer: "frontend/src/visual-lab/ReferenceVisualLabTruthful.tsx", why_insufficient: "renderer requires numeric data points; no producer exists", backend_required: "yes" },
  { screen: "ref-research", ui_field: "activity density", selector: "content.heatmap/heatmap_data/density", source: "none observed", backend: "optional heatmap has no producer in research builder", freshness: "none", availability: "No canonical payload observed", classification: "NOT WIRED", fallback: "NO ACTIVITY DENSITY DATA AVAILABLE", missing_contract: "research heatmap payload", nearest_contract: "workspace_payload_contract.research.heatmap", consumer: "frontend/src/visual-lab/ReferenceVisualLabTruthful.tsx", why_insufficient: "renderer requires numeric points; no producer exists", backend_required: "yes" },
  { screen: "ref-research-selected", ui_field: "selected finding/context", selector: "none", source: "none", backend: "no selected-finding event or field", freshness: "none", availability: "No canonical selection state", classification: "NOT WIRED", fallback: "NO SELECTED FINDING AVAILABLE", missing_contract: "selected finding identity and analysis", nearest_contract: "research_result/findings", consumer: "frontend/src/visual-lab/ReferenceVisualLabTruthful.tsx", why_insufficient: "selection is a visual scenario, not runtime state", backend_required: "yes" },
  { screen: "ref-vision", ui_field: "live image/snapshot", selector: "workspaceIntent.content.image_url/snapshot_url", source: "vision presentation_intent (not observed)", backend: "vision_observed event exists but store does not project it", freshness: "event timestamp if wired", availability: "No live vision payload observed", classification: "NOT WIRED", fallback: "NO LIVE MEDIA FEED AVAILABLE", missing_contract: "vision media payload", nearest_contract: "vision_observed", consumer: "frontend/src/visual-lab/ReferenceVisualLabTruthful.tsx", why_insufficient: "vision_observed falls through store reducer and no vision workspace producer is evidenced", backend_required: "yes" },
  { screen: "ref-vision", ui_field: "bounding boxes", selector: "workspaceIntent.content.bounding_boxes", source: "vision presentation_intent (not observed)", backend: "no canonical bounding_boxes field", freshness: "same observation timestamp", availability: "No live vision payload observed", classification: "NOT WIRED", fallback: "NO LIVE MEDIA FEED AVAILABLE", missing_contract: "vision bounding_boxes", nearest_contract: "vision_observed", consumer: "frontend/src/visual-lab/ReferenceVisualLabTruthful.tsx", why_insufficient: "no stored observation-to-workspace projection", backend_required: "yes" },
  { screen: "ref-vision-selected", ui_field: "selected target metadata", selector: "none", source: "none", backend: "no target-analysis contract", freshness: "none", availability: "No canonical target state", classification: "NOT WIRED", fallback: "TARGET METADATA UNAVAILABLE", missing_contract: "selected target analysis", nearest_contract: "vision_observed", consumer: "frontend/src/visual-lab/ReferenceVisualLabTruthful.tsx", why_insufficient: "selection has no runtime identity or analysis payload", backend_required: "yes" },
  { screen: "ref-vision-selected", ui_field: "media timeline", selector: "none", source: "none", backend: "no media timeline contract", freshness: "none", availability: "No canonical media timeline", classification: "NOT WIRED", fallback: "NO MEDIA TIMELINE AVAILABLE", missing_contract: "media timeline", nearest_contract: "vision_observed", consumer: "frontend/src/visual-lab/ReferenceVisualLabTruthful.tsx", why_insufficient: "no event sequence is exposed to the workspace", backend_required: "yes" },
  { screen: "ref-vision-selected", ui_field: "evidence attachments", selector: "none", source: "none", backend: "no attachment schema", freshness: "none", availability: "No canonical attachment list", classification: "NOT WIRED", fallback: "NO MEDIA ATTACHMENTS AVAILABLE", missing_contract: "vision attachments", nearest_contract: "vision_observed", consumer: "frontend/src/visual-lab/ReferenceVisualLabTruthful.tsx", why_insufficient: "unknown attachment shapes cannot be rendered safely", backend_required: "yes" },
  { screen: "ref-briefing", ui_field: "headline/summary", selector: "briefingPayload.headline/summary", source: "briefing presentation_intent", backend: "charlie.briefing_workspace required fields", freshness: "presentation event timestamp", availability: "Backend refused; no live intent observed", classification: "REAL + NOT CURRENTLY AVAILABLE", fallback: "NO BRIEFING HEADLINE AVAILABLE", evidence: "shared/workspace_payload_contract.json; charlie/research/presentation.py" },
  { screen: "ref-briefing", ui_field: "stories/summaries", selector: "briefingPayload.stories/summaries", source: "briefing presentation_intent", backend: "charlie.briefing_workspace stories/summaries", freshness: "presentation event timestamp", availability: "Backend refused; no live intent observed", classification: "REAL + NOT CURRENTLY AVAILABLE", fallback: "REAL DATA / UNAVAILABLE STATE", evidence: "frontend/src/presentation/workspacePayloads.ts" },
  { screen: "ref-briefing", ui_field: "sources/timeline", selector: "briefingPayload.sources/timeline_items", source: "briefing presentation_intent", backend: "charlie.briefing_workspace sources/timeline_items", freshness: "presentation event timestamp", availability: "Backend refused; no live intent observed", classification: "REAL + NOT CURRENTLY AVAILABLE", fallback: "NO SOURCE EVIDENCE AVAILABLE", evidence: "charlie/research/presentation.py" },
  { screen: "ref-briefing", ui_field: "geographic overlay", selector: "content.geo_data/map_data/map/spatial_map", source: "none observed", backend: "optional geo_data has no producer in briefing builder", freshness: "none", availability: "No canonical geo payload", classification: "NOT WIRED", fallback: "BRIEFING GEOGRAPHIC DATA UNAVAILABLE", missing_contract: "briefing geo_data", nearest_contract: "workspace_payload_contract.briefing.geo_data", consumer: "frontend/src/visual-lab/ReferenceVisualLabTruthful.tsx", why_insufficient: "briefing builder emits stories and timeline but no geo_data", backend_required: "yes" },
  { screen: "ref-briefing-alt", ui_field: "alternate briefing data", selector: "briefingPayload", source: "same briefing presentation_intent", backend: "charlie.briefing_workspace", freshness: "presentation event timestamp", availability: "Backend refused; no live intent observed", classification: "REAL + NOT CURRENTLY AVAILABLE", fallback: "NO BRIEFING HEADLINE AVAILABLE", evidence: "frontend/src/visual-lab/ReferenceVisualLabTruthful.tsx" },
  { screen: "ref-system-tasks", ui_field: "system metrics", selector: "useCharlieStore.systemStatus", source: "system_status event", backend: "main system_status snapshot", freshness: "systemStatusUpdatedAt", availability: "Backend refused; no live snapshot observed", classification: "REAL + NOT CURRENTLY AVAILABLE", fallback: "UNAVAILABLE", evidence: "frontend/src/store/charlie.ts" },
  { screen: "ref-system-tasks", ui_field: "subsystem health", selector: "useCharlieStore.subsystemHealth", source: "subsystem_health event", backend: "main HealthRegistry snapshot", freshness: "subsystemHealthUpdatedAt", availability: "Backend refused; no live snapshot observed", classification: "REAL + NOT CURRENTLY AVAILABLE", fallback: "SUBSYSTEM HEALTH UNAVAILABLE", evidence: "charlie/subsystem_health.py; frontend/src/store/charlie.ts" },
  { screen: "ref-system-tasks", ui_field: "active task rows", selector: "useCharlieStore.tasks", source: "task_snapshot/background_task events", backend: "main task journal public projection", freshness: "event timestamp", availability: "Backend refused; no live snapshot observed", classification: "REAL + NOT CURRENTLY AVAILABLE", fallback: "NO ACTIVE OPERATIONS REPORTED", evidence: "main.py; charlie/background_task.py" },
  { screen: "ref-system-tasks", ui_field: "operation rows", selector: "system workspace content.operations", source: "system presentation intent", backend: "fastpath system workspace operations", freshness: "presentation event timestamp", availability: "Backend refused; no live intent observed", classification: "REAL + NOT CURRENTLY AVAILABLE", fallback: "NO ACTIVE OPERATIONS REPORTED", evidence: "charlie/fastpaths.py; frontend/src/scene/workspaces/SystemWorkspace.tsx" },
  { screen: "ref-system-tasks", ui_field: "process/PID table", selector: "systemContent.processes", source: "system workspace content", backend: "fastpath psutil process rows", freshness: "operation/presentation timestamp", availability: "Backend refused; no live process snapshot observed", classification: "REAL + NOT CURRENTLY AVAILABLE", fallback: "PROCESS / PID DATA UNAVAILABLE", evidence: "charlie/fastpaths.py; frontend/src/visual-lab/ReferenceVisualLabTruthful.tsx" },
  { screen: "ref-system-tasks", ui_field: "topology map", selector: "systemContent.topology/network_map", source: "none observed", backend: "no topology field in system workspace producer", freshness: "none", availability: "No canonical topology payload", classification: "NOT WIRED", fallback: "AUTHORITATIVE TOPOLOGY UNAVAILABLE", missing_contract: "system topology", nearest_contract: "presentation_contract system workspace", consumer: "frontend/src/visual-lab/ReferenceVisualLabTruthful.tsx", why_insufficient: "system producer exposes vitals/processes/operations but not topology", backend_required: "yes" },
  { screen: "ref-system-tasks", ui_field: "activity logs", selector: "systemContent.logs/activities", source: "charlie_state.activities or system content", backend: "log event is not projected by frontend store", freshness: "event timestamp", availability: "No live log projection observed", classification: "NOT WIRED", fallback: "NO SYSTEM ACTIVITY REPORTED", missing_contract: "system activity log projection", nearest_contract: "log/charlie_state", consumer: "frontend/src/visual-lab/ReferenceVisualLabTruthful.tsx", why_insufficient: "raw log events fall through the store; activities are optional state text", backend_required: "yes" },
  { screen: "ref-active-docked", ui_field: "docked runtime state", selector: "useCharlieStore.visualRuntime", source: "runtime event stream", backend: "typed state/progress event envelope", freshness: "event timestamp", availability: "Backend refused; no live event observed", classification: "REAL + NOT CURRENTLY AVAILABLE", fallback: "NOT VERIFIED", evidence: "frontend/src/runtime/visualRuntime.ts" },
  { screen: "ref-active-docked", ui_field: "dock position/safe area", selector: "scene projection and CSS tokens", source: "workspace presence plus layout", backend: "presentation contract core_position", freshness: "workspace transition", availability: "No live workspace observed", classification: "REAL + NOT CURRENTLY AVAILABLE", fallback: "NOT VERIFIED", evidence: "shared/presentation_contract.json; frontend/src/scene/scene.css" },
  { screen: "ref-approval", ui_field: "request/tool/reason/risk", selector: "useCharlieStore.activeToolApproval", source: "tool_approval_request replay", backend: "main approval event payload", freshness: "approval event timestamp", availability: "Backend refused; no live approval observed", classification: "REAL + NOT CURRENTLY AVAILABLE", fallback: "NO PENDING APPROVAL", evidence: "charlie/core.py; frontend/src/store/charlie.ts" },
  { screen: "ref-approval", ui_field: "expected impact/potential risks", selector: "none", source: "none", backend: "ToolApprovalRequest has no impact/risk-detail fields", freshness: "none", availability: "No canonical detail", classification: "NOT WIRED", fallback: "IMPACT DETAILS UNAVAILABLE / RISK DETAILS UNAVAILABLE", missing_contract: "approval impact and risk details", nearest_contract: "tool_approval_request", consumer: "frontend/src/visual-lab/ReferenceVisualLabTruthful.tsx", why_insufficient: "risk_class is not a substitute for structured impact evidence", backend_required: "yes" },
  { screen: "ref-approval", ui_field: "target image/route context", selector: "none", source: "none", backend: "no target context contract", freshness: "none", availability: "No canonical target context", classification: "NOT WIRED", fallback: "TARGET IMAGE / ROUTE CONTEXT UNAVAILABLE", missing_contract: "approval target context", nearest_contract: "tool_approval_request.arguments", consumer: "frontend/src/visual-lab/ReferenceVisualLabTruthful.tsx", why_insufficient: "arguments are intentionally not a visual target schema", backend_required: "yes" },
  { screen: "ref-settings", ui_field: "configuration fields", selector: "useSettingsSnapshot GET /api/config", source: "settings_snapshot or /api/config", backend: "main settings projection authority", freshness: "snapshot/request time", availability: "Backend refused; no live config observed", classification: "REAL + NOT CURRENTLY AVAILABLE", fallback: "RUNTIME CONFIGURATION UNAVAILABLE", evidence: "charlie/web_server.py; main.py" },
  { screen: "ref-settings", ui_field: "secret configured flags", selector: "ConfigField.is_set", source: "settings_snapshot safe projection", backend: "main settings projection redacts secret values", freshness: "snapshot timestamp", availability: "Backend refused; no live config observed", classification: "REAL + NOT CURRENTLY AVAILABLE", fallback: "RUNTIME CONFIGURATION UNAVAILABLE", evidence: "charlie/web_server.py" },
  { screen: "ref-settings", ui_field: "health modules", selector: "GET /api/health.subsystems", source: "health endpoint", backend: "main HealthRegistry projection", freshness: "request time", availability: "Backend refused; no live health observed", classification: "REAL + NOT CURRENTLY AVAILABLE", fallback: "RUNTIME HEALTH UNAVAILABLE", evidence: "charlie/web_server.py; charlie/subsystem_health.py" },
  { screen: "ref-fault", ui_field: "fault phase/detail", selector: "useCharlieStore.visualRuntime.phase/detail", source: "alert/error/recovery events", backend: "typed runtime event stream", freshness: "event timestamp", availability: "Backend refused; no live fault observed", classification: "REAL + NOT CURRENTLY AVAILABLE", fallback: "SPECIFIC FAULT CONTEXT UNAVAILABLE", evidence: "frontend/src/runtime/visualRuntime.ts" },
  { screen: "ref-fault", ui_field: "recovery action", selector: "useCharlieStore.visualRuntime.recoveryProposalId", source: "recovery_proposal event", backend: "main recovery proposal", freshness: "event timestamp", availability: "Backend refused; no live proposal observed", classification: "REAL + NOT CURRENTLY AVAILABLE", fallback: "RECOVERY ACTION NOT WIRED", evidence: "shared/event_contract.json; frontend/src/runtime/visualRuntime.ts" },
  { screen: "ref-fault", ui_field: "specific error code", selector: "none", source: "none", backend: "no normalized fault-code field", freshness: "none", availability: "No canonical fault context", classification: "NOT WIRED", fallback: "SPECIFIC FAULT CONTEXT UNAVAILABLE", missing_contract: "fault code/context", nearest_contract: "alert/recovery_proposal", consumer: "frontend/src/visual-lab/ReferenceVisualLabTruthful.tsx", why_insufficient: "runtime detail is not an error identity contract", backend_required: "yes" },
  { screen: "ref-degraded", ui_field: "subsystem rows/status/detail", selector: "useCharlieStore.subsystemHealth", source: "subsystem_health event", backend: "main HealthRegistry snapshot", freshness: "subsystemHealthUpdatedAt", availability: "Backend refused; no live health observed", classification: "REAL + NOT CURRENTLY AVAILABLE", fallback: "SUBSYSTEM HEALTH UNAVAILABLE", evidence: "charlie/subsystem_health.py" },
  { screen: "ref-degraded", ui_field: "current limitations", selector: "none", source: "none", backend: "no limitations field in subsystem_health/runtime_truth projection", freshness: "none", availability: "No canonical limitations payload", classification: "NOT WIRED", fallback: "NO LIMITATION DETAILS REPORTED", missing_contract: "operational limitations", nearest_contract: "subsystem_health", consumer: "frontend/src/visual-lab/ReferenceVisualLabTruthful.tsx", why_insufficient: "status/detail does not provide structured user-impact limitations", backend_required: "yes" },
  { screen: "ref-degraded", ui_field: "runtime status message", selector: "useCharlieStore.visualRuntime.detail", source: "degraded/error event reducer", backend: "typed runtime event stream", freshness: "event timestamp", availability: "Backend refused; no live degraded event observed", classification: "REAL + NOT CURRENTLY AVAILABLE", fallback: "REAL DATA / UNAVAILABLE STATE", evidence: "frontend/src/runtime/visualRuntime.ts" },
  { screen: "ref-degraded", ui_field: "numeric health percentage", selector: "derived from status string", source: "none", backend: "no health percentage field", freshness: "none", availability: "No canonical percentage", classification: "NOT WIRED", fallback: "status only", missing_contract: "subsystem capacity percentage", nearest_contract: "subsystem_health", consumer: "frontend/src/visual-lab/ReferenceVisualLabTruthful.tsx", why_insufficient: "healthy/running to 100% is a qualitative heuristic", backend_required: "yes" },
];

if (!existsSync(referenceDir)) throw new Error(`Reference folder not found: ${referenceCandidate} or ${referenceFallback}`);
mkdirSync(reviewDir, { recursive: true });
mkdirSync(implementationDir, { recursive: true });
const comparisonDir = resolve(reviewDir, "references-vs-implementation");
const ringComparisonDir = resolve(reviewDir, "ring-comparisons");
mkdirSync(comparisonDir, { recursive: true });
mkdirSync(ringComparisonDir, { recursive: true });

function implementationPath(width, height, scenario) {
  return resolve(implementationDir, `${implementationPrefix}-${width}x${height}-${scenario}.png`);
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
  await page.setContent(`<!doctype html><meta charset="utf-8"><style>*{box-sizing:border-box}body{margin:0;background:#02070d;color:#e2f7fc;font:11px 'JetBrains Mono',monospace;letter-spacing:.08em}header{height:58px;padding:13px 20px;border-bottom:1px solid rgba(82,184,215,.35);text-transform:uppercase}main{display:grid;grid-template-columns:1fr 1fr;gap:10px;padding:10px}figure{position:relative;margin:0;overflow:hidden;border:1px solid rgba(82,184,215,.32);background:#061520}img{display:block;width:100%;height:auto}figcaption{position:absolute;right:0;bottom:0;left:0;padding:8px 10px;background:rgba(1,7,13,.84);color:#8bdff0;font-size:9px;text-transform:uppercase}</style><header>${label}<small style="display:block;margin-top:5px;color:#7998a5;font-size:8px">${classification} · ${dataLabel}</small></header><main><figure><img src="${imageData(reference)}"><figcaption>LEFT · REFERENCE · VISUAL REFERENCE ONLY</figcaption></figure><figure><img src="${imageData(implementation)}"><figcaption>RIGHT · ${classification} · ${dataLabel}</figcaption></figure></main>`);
  await page.screenshot({ path: outputPath, fullPage: true });
  await page.close();
}

async function renderRingComparison(browser, outputPath, left, right, label, leftViewBox, rightViewBox) {
  const page = await browser.newPage({ viewport: { width: 1400, height: 760 } });
  const [leftX, leftY, leftWidth, leftHeight] = leftViewBox;
  const [rightX, rightY, rightWidth, rightHeight] = rightViewBox;
  await page.setContent(`<!doctype html><meta charset="utf-8"><style>*{box-sizing:border-box}body{margin:0;background:#02070d;color:#e2f7fc;font:11px 'JetBrains Mono',monospace;letter-spacing:.08em}header{height:60px;padding:15px 22px;border-bottom:1px solid rgba(82,184,215,.35);text-transform:uppercase}main{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding:12px}figure{margin:0;border:1px solid rgba(82,184,215,.32);background:#061520;overflow:hidden}svg{display:block;width:100%;height:auto;background:#010408}figcaption{padding:10px;color:#8bdff0;font-size:9px;text-transform:uppercase}</style><header>${label}<small style="display:block;margin-top:5px;color:#7998a5;font-size:8px">${classification} · ${dataLabel}</small></header><main><figure><svg viewBox="${leftX} ${leftY} ${leftWidth} ${leftHeight}" preserveAspectRatio="none"><image href="${imageData(left)}" x="0" y="0" width="1672" height="941" /></svg><figcaption>LEFT · REFERENCE · VISUAL REFERENCE ONLY</figcaption></figure><figure><svg viewBox="${rightX} ${rightY} ${rightWidth} ${rightHeight}" preserveAspectRatio="none"><image href="${imageData(right)}" x="0" y="0" width="1920" height="1080" /></svg><figcaption>RIGHT · ${classification} · ${dataLabel}</figcaption></figure></main>`);
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

const centeredRingComparison = "ring-comparisons/centered-ring.png";
const dockedRingComparison = "ring-comparisons/docked-ring.png";
await renderRingComparison(
  browser,
  resolve(reviewDir, centeredRingComparison),
  referencePath("01_idle_centered.png"),
  implementationPath(1920, 1080, "ref-idle"),
  "Centered Charlie ring",
  [560, 210, 552, 560],
  [650, 210, 633, 643],
);
await renderRingComparison(
  browser,
  resolve(reviewDir, dockedRingComparison),
  referencePath("10_active_docked_core.png"),
  implementationPath(1920, 1080, "ref-active-docked"),
  "Docked Charlie ring",
  [1240, 500, 420, 430],
  [1420, 620, 482, 494],
);

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
  path: `implementation/${implementationPrefix}-${width}x${height}-${scenario}.png`,
  classification,
})));

function markdownCell(value) {
  return String(value ?? "").replace(/\|/g, "\\|").replace(/\r?\n/g, " ");
}

writeFileSync(resolve(reviewDir, "real-data-matrix.md"), [
  "# Pass 5D-REAL data matrix",
  "",
  `Backend probe: ${process.env.VISUAL_LAB_BACKEND_STATUS || "REAL HOST DATA CAPTURE BLOCKED — BACKEND NOT RUNNING"}`,
  `Evidence classification for this capture: ${classification}`,
  "",
  "| Screen | UI field | Frontend selector/store | Event/API source | Backend canonical source | Freshness | Current availability | Classification | Fallback |",
  "|---|---|---|---|---|---|---|---|---|",
  ...dataAudit.map((row) => `| ${[row.screen, row.ui_field, row.selector, row.source, row.backend, row.freshness, row.availability, row.classification, row.fallback].map(markdownCell).join(" | ")} |`),
  "",
  "`REAL + VERIFIED` count: 0. No live backend was available in this run.",
].join("\n"), "utf8");

const gapRows = dataAudit.filter(({ classification: fieldClassification }) => fieldClassification === "NOT WIRED" || fieldClassification === "UNSUPPORTED");
writeFileSync(resolve(reviewDir, "data-contract-gaps.md"), [
  "# Pass 5D-REAL data-contract gaps",
  "",
  "This ledger reports missing source contracts only. It does not design backend changes.",
  "",
  "| Screen | UI requirement | Missing canonical field/event/API | Nearest contract | Frontend consumer | Why current data is insufficient | Backend work required |",
  "|---|---|---|---|---|---|---|",
  ...gapRows.map((row) => `| ${[row.screen, row.ui_field, row.missing_contract, row.nearest_contract, row.consumer, row.why_insufficient, row.backend_required].map(markdownCell).join(" | ")} |`),
].join("\n"), "utf8");

writeFileSync(resolve(reviewDir, "runtime-evidence-ledger.json"), `${JSON.stringify({
  task: "Pass 5D-REAL",
  starting_head: process.env.VISUAL_LAB_STARTING_HEAD || "unknown",
  checkpoint_sha: process.env.VISUAL_LAB_CHECKPOINT_SHA || "unknown",
  branch: process.env.VISUAL_LAB_BRANCH || "unknown",
  scope: scenarios.map(([scenario]) => scenario),
  status: "in_progress",
  visual_review: "unverified",
  backend_probe: {
    endpoint: "http://127.0.0.1:8000/api/status",
    status: "blocked",
    observed: process.env.VISUAL_LAB_BACKEND_STATUS || "REAL HOST DATA CAPTURE BLOCKED — BACKEND NOT RUNNING",
    classification: "NOT VERIFIED",
    writes: "none",
  },
  verification,
  evidence_classification: classification,
  capture: { implementation_prefix: implementationPrefix, implementation_directory: implementationDir, evidence },
  ring_comparisons: [centeredRingComparison, dockedRingComparison],
  remaining_work: "SOL/HUMAN pixel review and visual signoff",
  requirements: [
    { id: "reference-locked-14-screens", status: "in_progress", evidence, remaining_work: "Human comparison against canonical references." },
    { id: "centered-docked-ring", status: "in_progress", evidence: evidence.filter(({ scenario }) => ["ref-idle", "ref-online", "ref-fault", "ref-degraded", "ref-active-docked"].includes(scenario)), remaining_work: "Human confirmation of scale, geometry, and dock offset." },
    { id: "responsive-1920-1366-800", status: "in_progress", evidence, remaining_work: "Human confirmation that hierarchy and readability hold at each viewport." },
    { id: "comparison-artifacts", status: "passed", evidence: [{ path: "references-vs-implementation/", classification }, { path: "comparison-contact-sheet.png", classification }], remaining_work: "None for artifact generation." },
  ],
  data_source_audit: dataAudit,
  not_wired_fields: gapRows,
  classification,
  matrix_path: "real-data-matrix.md",
  contract_gaps_path: "data-contract-gaps.md",
  reference_directory: referenceDir,
  implementation_directory: implementationDir,
  comparison_directory: comparisonDir,
  ring_comparison_directory: ringComparisonDir,
}, null, 2)}\n`, "utf8");

writeFileSync(resolve(reviewDir, "VISUAL-REVIEW.txt"), [
  `All implementation screenshots are ${classification}.`,
  `Comparison data label: ${dataLabel}.`,
  process.env.VISUAL_LAB_BACKEND_STATUS || "REAL HOST DATA CAPTURE BLOCKED — BACKEND NOT RUNNING",
  `Reference directory: ${referenceDir}`,
  `Implementation directory: ${implementationDir}`,
  `Comparison directory: ${comparisonDir}`,
  `Ring comparisons: ${centeredRingComparison}, ${dockedRingComparison}`,
  "",
  ...evidence.map(({ scenario, label, viewport, path }) => `${scenario} · ${label}\n  viewport: ${viewport}\n  implementation: ${path}\n  classification: ${classification}`),
  "",
  "Comparison artifacts",
  ...scenarios.map(([scenario, label], index) => `${scenario} · ${label}\n  comparison: references-vs-implementation/compare-${String(index + 1).padStart(2, "0")}.png\n  classification: ${classification}`),
].join("\n\n"), "utf8");

console.log(`Generated ${evidence.length} implementation references, ${scenarios.length} comparisons, and 4 contact sheets in ${reviewDir}`);
