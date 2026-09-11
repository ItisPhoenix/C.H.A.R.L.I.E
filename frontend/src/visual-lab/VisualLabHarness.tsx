import { useEffect, type ReactElement } from "react";
import {
  useCharlieStore,
  type ChatMessage,
  type PresentationIntent,
  type RuntimeTask,
  type SubsystemHealth,
  type SystemStatus,
} from "../store/charlie";
import { INITIAL_VISUAL_RUNTIME, type VisualRuntimePhase } from "../runtime/visualRuntime";
import { useWorkspaceStore } from "../layout/workspaceStore";
import { CharlieScene } from "../scene/CharlieScene";

export type VisualLabScenario =
  | "idle"
  | "listening"
  | "transcribing"
  | "thinking"
  | "acting"
  | "speaking"
  | "approval"
  | "error"
  | "recovery"
  | "conversation"
  | "conversation-rich"
  | "research"
  | "research-rich"
  | "briefing"
  | "briefing-rich"
  | "system"
  | "system-rich"
  | "tasks"
  | "tasks-rich"
  | "settings"
  | "offline"
  | "degraded";

const workspaceFor: Partial<Record<VisualLabScenario, string>> = {
  conversation: "conversation",
  "conversation-rich": "conversation",
  research: "research",
  "research-rich": "research",
  briefing: "briefing",
  "briefing-rich": "briefing",
  system: "system",
  "system-rich": "system",
  tasks: "tasks",
  "tasks-rich": "tasks",
};

const TEST_MOCK_SESSION = "visual-lab-session";

const RESEARCH_SOURCES = [
  {
    id: "source-grid-1",
    title: "Heat Resilience in Urban Power Networks",
    domain: "energy.gov",
    url: "https://example.test/energy-grid-resilience",
    snippet: "Regional operators report improved peak-load recovery after targeted storage and cooling upgrades.",
    published_at: "2026-09-09T08:30:00Z",
    source_type: "report",
    confidence: 0.92,
    type: "report",
  },
  {
    id: "source-grid-2",
    title: "Summer Demand Response Review",
    domain: "gridwatch.example",
    url: "https://example.test/demand-response",
    snippet: "Demand response reduced evening stress, but neighborhood-level transformer failures remained uneven.",
    published_at: "2026-09-08T14:10:00Z",
    source_type: "research",
    confidence: 0.84,
    type: "article",
  },
  {
    id: "source-grid-3",
    title: "Municipal Cooling Corridor Pilot",
    domain: "citylab.example",
    url: "https://example.test/cooling-corridor",
    snippet: "A cooling-corridor pilot shifted peak demand without materially increasing overnight load.",
    published_at: "2026-09-07T11:45:00Z",
    source_type: "news",
    confidence: 0.79,
    type: "news",
  },
  {
    id: "source-grid-4",
    title: "Transformer Failure Dataset Notes",
    domain: "openinfra.example",
    url: "https://example.test/transformer-data",
    snippet: "Observed failure clusters align with heat duration more strongly than with single-day temperature peaks.",
    published_at: "2026-09-06T17:20:00Z",
    source_type: "dataset",
    confidence: 0.76,
    type: "report",
  },
];

const RESEARCH_CONTENT: Record<string, unknown> = {
  schema: "charlie.research_workspace",
  version: 1,
  query: "How resilient are urban power grids during prolonged heat events?",
  objective: "Compare grid operators, municipal pilots, and transformer observations to identify where resilience is improving and where risk remains concentrated.",
  mode: "evidence",
  title: "URBAN GRID RESILIENCE",
  summary: "Evidence points to better peak-load recovery, with local transformer exposure still concentrated in older neighborhoods.",
  status: "complete",
  confidence: 0.87,
  findings: [
    { id: "finding-1", title: "Recovery improved after storage upgrades", detail: "Operators shortened evening recovery windows after pairing neighborhood storage with demand-response contracts.", source_ids: ["source-grid-1", "source-grid-2"], confidence: 0.91 },
    { id: "finding-2", title: "Failure risk remains spatially concentrated", detail: "Observed transformer incidents cluster in older feeder corridors rather than following the citywide temperature average.", source_ids: ["source-grid-4"], confidence: 0.84, contradiction: true },
    { id: "finding-3", title: "Demand response shifts, not removes, load", detail: "The pilot reduced evening stress while creating a smaller overnight rebound that operators now monitor explicitly.", source_ids: ["source-grid-2", "source-grid-3"], confidence: 0.8 },
    { id: "finding-4", title: "Cooling corridors show measurable benefit", detail: "Targeted public cooling spaces lowered local peak demand without materially increasing overnight load.", source_ids: ["source-grid-3"], confidence: 0.77 },
    { id: "finding-5", title: "Duration matters more than the daily peak", detail: "Multi-day heat exposure is a stronger signal for component stress than a single high-temperature maximum.", source_ids: ["source-grid-1", "source-grid-4"], confidence: 0.75 },
  ],
  sources: RESEARCH_SOURCES,
  timeline_items: [
    { id: "timeline-1", time: "SEP 06", title: "Dataset window closed", summary: "Transformer observations normalized.", status: "completed" },
    { id: "timeline-2", time: "SEP 07", title: "Cooling pilot reported", summary: "Municipal corridor results published.", status: "completed" },
    { id: "timeline-3", time: "SEP 08", title: "Demand response reviewed", summary: "Evening load reduction confirmed.", status: "completed" },
    { id: "timeline-4", time: "SEP 09", title: "Operator recovery note", summary: "Storage upgrade evidence added.", status: "active" },
    { id: "timeline-5", time: "NOW", title: "Synthesis ready", summary: "Cross-source conclusion assembled.", status: "active" },
  ],
  radar: {
    mode: "radar",
    title: "EVIDENCE SIGNAL FIELD",
    subtitle: "SOURCE-BOUND OBSERVATIONS",
    objects: [
      { id: "signal-1", label: "NORTH", type: "hub", status: "active", angle: 18, distance: 0.72 },
      { id: "signal-2", label: "EAST", type: "signal", status: "warning", angle: 92, distance: 0.58 },
      { id: "signal-3", label: "SOUTH", type: "hub", status: "active", angle: 176, distance: 0.82 },
      { id: "signal-4", label: "WEST", type: "signal", status: "idle", angle: 250, distance: 0.52 },
      { id: "signal-5", label: "CENTER", type: "hub", status: "active", angle: 318, distance: 0.28 },
    ],
  },
  heatmap: {
    title: "INCIDENT DENSITY",
    subtitle: "NORMALIZED OBSERVATIONS / 72 HOURS",
    gridWidth: 12,
    gridHeight: 5,
    minLabel: "LOW",
    maxLabel: "HIGH",
    points: [
      { x: 1, y: 3, value: 0.35 }, { x: 2, y: 3, value: 0.55 }, { x: 3, y: 3, value: 0.74 },
      { x: 4, y: 2, value: 0.86 }, { x: 5, y: 2, value: 0.66 }, { x: 6, y: 2, value: 0.48 },
      { x: 7, y: 1, value: 0.78 }, { x: 8, y: 1, value: 0.92 }, { x: 9, y: 1, value: 0.64 },
      { x: 3, y: 4, value: 0.4 }, { x: 6, y: 4, value: 0.57 }, { x: 10, y: 2, value: 0.36 },
    ],
  },
  chart: {
    chartType: "line",
    title: "RECOVERY INDEX",
    unit: "%",
    data: [
      { label: "D-4", value: 48 }, { label: "D-3", value: 55 }, { label: "D-2", value: 61 },
      { label: "D-1", value: 68 }, { label: "NOW", value: 76 },
    ],
  },
};

const BRIEFING_CONTENT: Record<string, unknown> = {
  schema: "charlie.briefing_workspace",
  version: 1,
  title: "DAILY INTELLIGENCE BRIEFING",
  headline: "GRID RESILIENCE MOVES FROM PILOT TO PRACTICE",
  summary: "Regional operators are reporting faster heat-event recovery while municipalities expand cooling corridors. The remaining concern is local transformer exposure, not a lack of citywide capacity.",
  stories: [
    { id: "story-1", title: "Recovery", summary: "Storage upgrades shorten peak recovery windows.", source_ids: ["briefing-source-1"], region: "NORTH AMERICA" },
    { id: "story-2", title: "Cooling", summary: "Municipal cooling corridors reduce neighborhood stress.", source_ids: ["briefing-source-2"], region: "CITY SYSTEMS" },
    { id: "story-3", title: "Exposure", summary: "Older feeder corridors remain the main risk concentration.", source_ids: ["briefing-source-3"], region: "INFRASTRUCTURE" },
  ],
  summaries: [
    "Storage upgrades shorten peak recovery windows across the monitored regions.",
    "Cooling corridors reduce local stress while operators watch for overnight rebound.",
    "Older feeder corridors remain the clearest concentration of component exposure.",
  ],
  timeline_items: [
    { id: "briefing-time-1", time: "06:42", title: "Overnight checkpoint added", summary: "Demand response review updated.", status: "completed" },
    { id: "briefing-time-2", time: "07:10", title: "Exposure map refreshed", summary: "Feeder corridor risk re-ranked.", status: "completed" },
    { id: "briefing-time-3", time: "07:55", title: "Cooling pilot expanded", summary: "Seasonal sites approved.", status: "active" },
    { id: "briefing-time-4", time: "08:30", title: "Recovery update published", summary: "Operator evidence incorporated.", status: "active" },
  ],
  sources: [
    { id: "briefing-source-1", title: "Regional operators publish recovery update", domain: "energy.gov", url: "https://example.test/briefing/operators", snippet: "Storage and demand-response programs reduced evening recovery time across three monitored regions.", timestamp: "08:30", type: "report", confidence: 0.91 },
    { id: "briefing-source-2", title: "Cooling corridor pilot expands", domain: "citylab.example", url: "https://example.test/briefing/cooling", snippet: "Municipal cooling sites move from pilot to seasonal operation after a measurable local demand reduction.", timestamp: "07:55", type: "news", confidence: 0.84 },
    { id: "briefing-source-3", title: "Transformer exposure map updated", domain: "openinfra.example", url: "https://example.test/briefing/transformers", snippet: "Older feeder corridors remain the highest-confidence concentration of component stress.", timestamp: "07:10", type: "article", confidence: 0.8 },
    { id: "briefing-source-4", title: "Demand response shifts overnight load", domain: "gridwatch.example", url: "https://example.test/briefing/demand", snippet: "Operators add an overnight checkpoint to distinguish shifted load from removed load.", timestamp: "06:42", type: "report", confidence: 0.77 },
  ],
  status: "ready",
  confidence: 0.88,
  geo_data: {
    mode: "geo",
    useRealEngine: false,
    title: "GLOBAL SIGNAL MAP",
    subtitle: "BRIEFING SOURCES / VERIFIED",
    nodes: [
      { id: "geo-1", label: "PACIFIC", sublabel: "RECOVERY", x: 21, y: 46, status: "active", color: "#22d3ee" },
      { id: "geo-2", label: "NORTH ATL", sublabel: "PILOT", x: 49, y: 31, status: "active", color: "#38bdf8" },
      { id: "geo-3", label: "EUROPE", sublabel: "EXPOSURE", x: 56, y: 27, status: "warning", color: "#fbbf24" },
      { id: "geo-4", label: "ASIA", sublabel: "DEMAND", x: 73, y: 37, status: "active", color: "#22d3ee" },
      { id: "geo-5", label: "SOUTH", sublabel: "COOLING", x: 58, y: 65, status: "idle", color: "#818cf8" },
    ],
    edges: [
      { from: "geo-1", to: "geo-2", type: "route", active: true },
      { from: "geo-2", to: "geo-3", type: "dotted", active: true },
      { from: "geo-3", to: "geo-4", type: "route", active: true },
      { from: "geo-2", to: "geo-5", type: "link", active: false },
    ],
  },
};

const SYSTEM_CONTENT: Record<string, unknown> = {
  topology: {
    mode: "topology",
    useRealEngine: false,
    title: "LOCAL RUNTIME TOPOLOGY",
    subtitle: "AUTHORITATIVE SUBSYSTEM LINKS",
    nodes: [
      { id: "brain", label: "BRAIN", sublabel: "READY", x: 50, y: 20, status: "active", color: "#22d3ee" },
      { id: "voice", label: "VOICE", sublabel: "ONLINE", x: 23, y: 47, status: "active", color: "#38bdf8" },
      { id: "browser", label: "BROWSER", sublabel: "READY", x: 77, y: 47, status: "active", color: "#22d3ee" },
      { id: "memory", label: "MEMORY", sublabel: "SYNC", x: 34, y: 78, status: "active", color: "#818cf8" },
      { id: "event-bus", label: "EVENT BUS", sublabel: "LIVE", x: 66, y: 78, status: "active", color: "#22d3ee" },
    ],
    edges: [
      { from: "brain", to: "voice", type: "route", active: true },
      { from: "brain", to: "browser", type: "route", active: true },
      { from: "brain", to: "memory", type: "link", active: true },
      { from: "brain", to: "event-bus", type: "route", active: true },
      { from: "voice", to: "memory", type: "dotted", active: false },
      { from: "browser", to: "event-bus", type: "link", active: true },
    ],
  },
  operations: [
    { id: "op-research", title: "Research synthesis", subtitle: "cross-source evidence", progress: 76, status: "RUNNING" },
    { id: "op-browser", title: "Browser verification", subtitle: "semantic postcondition", progress: 48, status: "RUNNING" },
    { id: "op-memory", title: "Memory index", subtitle: "session continuity", progress: 100, status: "COMPLETED" },
    { id: "op-calendar", title: "Calendar read", subtitle: "awaiting provider", progress: 0, status: "QUEUED" },
  ],
  processes: {
    title: "WHAT IS RUNNING",
    subtitle: "LIVE PROCESSES / AUTHORITATIVE SNAPSHOT",
    processes: [
      { name: "charlie-brain", pid: 4216, status: "RUNNING", uptime: "02:14:08", cpu: "14%", memory: "412 MB" },
      { name: "event-bridge", pid: 4312, status: "RUNNING", uptime: "02:13:42", cpu: "3%", memory: "88 MB" },
      { name: "browser-worker", pid: 4470, status: "IDLE", uptime: "01:52:10", cpu: "0%", memory: "126 MB" },
      { name: "memory-index", pid: 4511, status: "RUNNING", uptime: "00:41:22", cpu: "5%", memory: "214 MB" },
    ],
  },
  vitals: {
    title: "SYSTEM VITALS",
    subtitle: "LOCAL HOST / OBSERVED",
    gauges: [
      { id: "cpu", label: "CPU", value: 38, unit: "%", color: "#22d3ee" },
      { id: "ram", label: "RAM", value: 62, unit: "%", color: "#38bdf8" },
      { id: "disk", label: "DISK", value: 44, unit: "%", color: "#818cf8" },
    ],
    stats: [
      { label: "NETWORK", value: "1.8 MB/S" },
      { label: "UPTIME", value: "02:14:08" },
      { label: "EVENTS", value: "1,284" },
    ],
  },
  logs: [
    { timestamp: "09:00:02", level: "INFO", message: "runtime state snapshot accepted" },
    { timestamp: "08:59:48", level: "INFO", message: "research evidence projection updated" },
    { timestamp: "08:58:12", level: "WARN", message: "calendar provider remains unavailable" },
    { timestamp: "08:57:40", level: "INFO", message: "browser verification completed" },
    { timestamp: "08:56:19", level: "DEBUG", message: "session continuity checkpoint written" },
  ],
};

const SYSTEM_DEGRADED_CONTENT: Record<string, unknown> = {
  ...SYSTEM_CONTENT,
  operations: [
    { id: "op-recovery", title: "Provider recovery", subtitle: "retry proposal available", progress: 22, status: "RUNNING" },
    { id: "op-browser", title: "Browser verification", subtitle: "paused by provider", progress: 48, status: "FAILED" },
    { id: "op-memory", title: "Memory index", subtitle: "session continuity", progress: 100, status: "COMPLETED" },
  ],
  logs: [
    { timestamp: "09:00:02", level: "WARN", message: "calendar provider health degraded" },
    { timestamp: "08:59:48", level: "ERROR", message: "browser verification stopped before postcondition" },
    { timestamp: "08:58:12", level: "INFO", message: "recovery proposal prepared" },
  ],
};

const TASK_FIXTURES: Record<string, RuntimeTask> = {
  "task-alpha": {
    id: "task-alpha", title: "Prepare resilience briefing", status: "running", currentStep: 3, totalSteps: 5,
    origin: "foreground", lane: "priority", priority: "high", sessionId: TEST_MOCK_SESSION, turnId: "turn-alpha",
    progress: 0.64, currentAction: "Correlating research findings with source timeline",
    capabilityRequirements: ["research", "presentation"],
  },
  "task-beta": {
    id: "task-beta", title: "Verify browser postcondition", status: "queued", currentStep: 0, totalSteps: 3,
    origin: "background", lane: "normal", priority: "normal", sessionId: TEST_MOCK_SESSION, progress: 0,
    currentAction: "Waiting for browser capability", waitingReason: "Capability lease queued", capabilityRequirements: ["browser"],
  },
  "task-gamma": {
    id: "task-gamma", title: "Refresh memory index", status: "completed", currentStep: 4, totalSteps: 4,
    origin: "background", lane: "maintenance", priority: "low", sessionId: TEST_MOCK_SESSION, progress: 1,
    currentAction: "Index checkpoint stored", resultReference: "memory://checkpoint/2026-09-11", capabilityRequirements: ["memory"],
  },
  "task-delta": {
    id: "task-delta", title: "Read calendar availability", status: "failed", currentStep: 1, totalSteps: 2,
    origin: "background", lane: "normal", priority: "normal", sessionId: TEST_MOCK_SESSION, progress: 0.5,
    currentAction: "Provider request rejected", errorSummary: "Calendar provider unavailable; no change was applied.",
    resultReference: "calendar://unavailable", capabilityRequirements: ["calendar"],
  },
};

const CONVERSATION_FIXTURES: ChatMessage[] = [
  {
    id: "message-1", role: "user", pending: false, turnId: "turn-alpha", taskId: "task-alpha",
    text: "Review the grid resilience findings and prepare a concise briefing for the morning review.",
  },
  {
    id: "message-2", role: "charlie", pending: false, turnId: "turn-alpha", taskId: "task-alpha",
    text: "I found a consistent signal: recovery improved after storage and demand-response upgrades, but risk remains concentrated in older feeder corridors. I am checking the source timeline now so the briefing separates observed evidence from interpretation.",
  },
  {
    id: "message-3", role: "user", pending: false, turnId: "turn-alpha", taskId: "task-alpha",
    text: "Call out what is verified and what still needs attention.",
  },
  {
    id: "message-4", role: "charlie", pending: true, turnId: "turn-alpha", taskId: "task-alpha",
    text: "Verified: peak-load recovery is improving, cooling corridors show a measurable local effect, and the remaining exposure is spatially concentrated. Still under review: whether the overnight rebound is being measured consistently across all regions. I am keeping that distinction visible in the briefing instead of presenting it as a completed conclusion.",
  },
];

function runtimeFor(scenario: VisualLabScenario): { phase: VisualRuntimePhase; label: string; detail: string | null } {
  if (scenario === "approval") return { phase: "approval_wait", label: "APPROVAL WAIT", detail: "User approval required" };
  if (scenario === "error") return { phase: "error", label: "ERROR", detail: "Provider verification failed; no change was applied" };
  if (scenario === "recovery") return { phase: "recovering", label: "RECOVERY AVAILABLE", detail: "A bounded provider recovery is available" };
  if (scenario === "offline") return { phase: "offline", label: "OFFLINE", detail: "Runtime connection unavailable" };
  if (scenario === "degraded") return { phase: "degraded", label: "DEGRADED", detail: "Calendar provider requires attention" };
  if (scenario === "conversation-rich") return { phase: "speaking", label: "SPEAKING", detail: "Streaming response" };
  if (scenario === "research-rich") return { phase: "acting", label: "RESEARCHING", detail: "Reviewing evidence and source chronology" };
  if (scenario === "briefing-rich") return { phase: "acting", label: "COMPOSING BRIEFING", detail: "Assembling verified signals" };
  if (scenario === "system-rich") return { phase: "acting", label: "SYSTEM REVIEW", detail: "Observing authoritative runtime state" };
  if (scenario === "tasks-rich") return { phase: "acting", label: "TASK EXECUTION", detail: "Managing concurrent operations" };
  const phase: VisualRuntimePhase = [
    "idle", "listening", "transcribing", "thinking", "acting", "speaking",
  ].includes(scenario) ? scenario as VisualRuntimePhase : "idle";
  return { phase, label: phase.toUpperCase(), detail: null };
}

function workspaceContentFor(scenario: VisualLabScenario): Record<string, unknown> {
  const workspaceType = workspaceFor[scenario];
  if (workspaceType === "research") return RESEARCH_CONTENT;
  if (workspaceType === "briefing") return BRIEFING_CONTENT;
  if (workspaceType === "system") return scenario === "degraded" ? SYSTEM_DEGRADED_CONTENT : SYSTEM_CONTENT;
  if (workspaceType === "tasks") return { status: "running", summary: "Concurrent task operations" };
  if (workspaceType === "conversation") return { status: "streaming", summary: "Conversation fixture with active response" };
  return {};
}

function taskFixturesFor(scenario: VisualLabScenario): Record<string, RuntimeTask> {
  return scenario === "tasks" || scenario === "tasks-rich" || scenario === "system-rich" ? TASK_FIXTURES : {};
}

function conversationFor(scenario: VisualLabScenario): ChatMessage[] {
  return scenario === "conversation" || scenario === "conversation-rich" ? CONVERSATION_FIXTURES : [];
}

function systemStatusFor(scenario: VisualLabScenario): SystemStatus | null {
  return scenario === "system" || scenario === "system-rich" || scenario === "degraded"
    ? { cpu: 38, ram: 62, gpu: 27, disk: 44, netKbps: 1840, uptimeSeconds: 8048, batteryPercent: 86 }
    : null;
}

function healthFor(scenario: VisualLabScenario): Record<string, SubsystemHealth> {
  if (scenario === "degraded") {
    return {
      brain: { status: "healthy", detail: "Available" },
      browser: { status: "degraded", detail: "Provider recovery available" },
      calendar: { status: "unavailable", detail: "Provider did not respond" },
      memory: { status: "healthy", detail: "Index available" },
    };
  }
  if (scenario === "system" || scenario === "system-rich") {
    return {
      brain: { status: "healthy", detail: "Available" },
      browser: { status: "healthy", detail: "Ready" },
      memory: { status: "healthy", detail: "Index available" },
      event_bus: { status: "healthy", detail: "Connected" },
    };
  }
  return {};
}

export function VisualLabHarness({ scenario }: { scenario: VisualLabScenario }): ReactElement {
  useEffect(() => {
    const runtime = runtimeFor(scenario);
    const workspaceType = workspaceFor[scenario];
    const content = workspaceContentFor(scenario);
    const tasks = taskFixturesFor(scenario);
    const conversation = conversationFor(scenario);
    const systemStatus = systemStatusFor(scenario);
    const subsystemHealth = healthFor(scenario);
    const settingsIntent: Record<string, PresentationIntent> = scenario === "settings" ? {
      "visual-lab-settings": {
        id: "visual-lab-settings",
        kind: "overlay" as const,
        title: "TEST / MOCK SETTINGS",
        summary: "TEST/MOCK fixture only",
        content: {},
        overlayType: "settings",
        priority: 50,
        attentionLevel: "normal" as const,
        dismissPolicy: "manual" as const,
        preferredZone: "center" as const,
        anchor: "screen" as const,
        replayable: false,
        createdAt: new Date().toISOString(),
      },
    } : {};
    useWorkspaceStore.setState({ workspaces: {}, activeWorkspaceId: null, recentWorkspaces: [] });
    useCharlieStore.setState({
      connected: scenario !== "offline",
      coreState: ["acting", "conversation-rich", "research-rich", "briefing-rich", "system-rich", "tasks-rich"].includes(scenario) ? "working" : scenario,
      visualRuntime: { ...INITIAL_VISUAL_RUNTIME, ...runtime, updatedAt: new Date().toISOString() },
      presentationIntents: workspaceType ? ({
        [`visual-lab-${workspaceType}`]: {
          id: `visual-lab-${workspaceType}`,
          kind: "workspace",
          title: `TEST / MOCK ${workspaceType.toUpperCase()}`,
          summary: "TEST/MOCK fixture only",
          content,
          workspaceType,
          taskId: workspaceType === "tasks" ? "task-alpha" : null,
          sessionId: TEST_MOCK_SESSION,
          turnId: null,
          capability: "visual-lab",
          operation: "test.fixture",
          priority: 50,
          attentionLevel: "normal",
          dismissPolicy: "manual",
          preferredZone: "center",
          anchor: "screen",
          replayable: false,
          createdAt: new Date().toISOString(),
        },
      } as unknown as Record<string, PresentationIntent>) : settingsIntent,
      activeToolApproval: scenario === "approval" ? {
        request_id: "visual-lab-approval",
        tool_name: "browser_navigate",
        reason: "Charlie is ready to open the verified briefing source set.",
        arguments: {},
        risk_class: "safe",
      } : null,
      activeSessionId: TEST_MOCK_SESSION,
      activeSessionTitle: "TEST / MOCK",
      tasks,
      chatMessages: conversation,
      activities: scenario === "conversation" || scenario === "conversation-rich"
        ? ["SYNTHESIS // source timeline checked", "RESPONSE // streaming verified distinction"]
        : scenario === "research-rich"
          ? ["RESEARCH // source set normalized", "RESEARCH // chronology compared"]
          : [],
      systemStatus,
      systemStatusUpdatedAt: systemStatus ? new Date().toISOString() : null,
      subsystemHealth,
      subsystemHealthUpdatedAt: Object.keys(subsystemHealth).length ? new Date().toISOString() : null,
      audioLevel: scenario === "listening" || scenario === "speaking" ? 0.68 : 0,
      activeAlert: null,
    });
  }, [scenario]);

  return (
    <div data-visual-lab="TEST/MOCK" data-visual-lab-scenario={scenario}>
      <div className="sr-only">TEST/MOCK VISUAL LAB — not runtime acceptance</div>
      <CharlieScene />
    </div>
  );
}
