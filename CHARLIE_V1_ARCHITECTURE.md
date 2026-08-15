# C.H.A.R.L.I.E. V1 — Target Architecture

## 1. Product definition

C.H.A.R.L.I.E. V1 is an **autonomous AI assistant / Agentic OS** with:
- one primary assistant/orchestrator;
- deterministic-first execution;
- capability-owned tools;
- optional bounded temporary sub-agents;
- persistent conversational continuity and selective durable memory;
- a floating desktop pet;
- a dedicated opaque, responsive React spatial HUD;
- dynamic contextual workspaces/widgets;
- local screen/vision support;
- fast browser/desktop/system automation;
- self-knowledge and controlled self-extension.

The local repository is an existing system. This is an **incremental redesign**, not a greenfield rewrite.

---

## 2. Experience model

### Desktop

```text
Windows desktop
      │
      ├── floating Charlie pet
      ├── voice/background runtime
      └── tasks continue invisibly
```

The pet is the always-available presence.

### HUD

Voice, pet double-click, global shortcut, tray menu, or explicit UI action opens the dedicated HUD.

```text
┌────────────────────────────────────────────────────┐
│                                                    │
│                CHARLIE SPATIAL HUD                 │
│                                                    │
│                     ● core                         │
│                                                    │
└────────────────────────────────────────────────────┘
```

When visual content is useful:

```text
┌────────────────────────────────────────────────────┐
│                                                    │
│              PRIMARY WORKSPACE                     │
│         map / research / camera / task             │
│                                                    │
│   contextual widget                         ●      │
│                                          Charlie   │
└────────────────────────────────────────────────────┘
```

Closing the HUD does not stop Charlie or active tasks.

---

## 3. Core runtime

```text
Inputs
├─ voice
├─ text
├─ pet/HUD actions
├─ watcher events
└─ integration events
        ↓
Turn / Request Intake
        ↓
Fast Intent Classifier
        ↓
Multi-Intent Decomposer (when needed)
        ↓
Complexity Gate
   ├─ direct
   ├─ lightweight reasoning
   └─ planned task
        ↓
Charlie Brain / Orchestrator
        ↓
Capability Router
        ↓
Policy Gate
        ↓
Task Journal + Capability Leases
        ↓
Capability Owner
        ↓
Semantic Verifier
        ↓
Result/Event
   ├───────────────┐
   ↓               ↓
Memory/History   PresentationResolver
                   ↓
        silent / caption / notification
        widget / composed surface
        workspace / attention modal
                   ↓
               React HUD
                   +
                  TTS
```

---

## 4. Brain / orchestration

Keep `Brain` as the top-level façade/orchestrator, but reduce internal overload incrementally.

Avoid one-file-per-concept overengineering.

Suggested cohesive boundaries:

```text
core.py                 # Brain facade / top-level orchestration

runtime/
  turns.py              # turn lifecycle, channels, cancellation
  execution.py          # execution coordination, leases, verification
  tasks.py              # unified task journal/adapters

presentation/
  resolver.py           # PresentationResolver + intents

capabilities/
  ...                   # capability-owned implementations
```

Existing router/planner/memory modules should be reused where sensible instead of duplicated.

---

## 5. Capability architecture

Initial authoritative capability domains:

```text
SystemCapability
DesktopCapability
BrowserCapability
ResearchCapability
TerminalCapability
FileCapability
MediaCapability
VisionCapability
MemoryCapability
TaskCapability
```

Each capability self-registers metadata:

```text
CapabilityDescriptor
├─ id
├─ description
├─ owner
├─ operations
├─ schemas
├─ availability
├─ health
├─ risk
├─ locks/leases
├─ timeout
└─ verifier
```

A runtime `CapabilityIndex` auto-discovers registered built-ins and MCP-provided capabilities.

No subsystem may quietly create a competing control path for another capability's domain.

---

## 6. Dynamic sub-agents

No fixed agent swarm.

The primary Charlie Brain may spawn temporary isolated sub-agents for work that benefits from parallelism or isolation.

V1 constraints:
- maximum ~2 concurrent;
- no nested sub-agents;
- isolated histories;
- relevant context explicitly passed;
- restricted capability allowlist;
- parent owns user communication;
- parent verifies output;
- cancellation propagates.

Examples:
- parallel research;
- bounded investigation;
- independent analysis branch.

Not used for:
- opening apps;
- volume changes;
- ordinary browsing;
- normal tool calls.

---

## 7. Deterministic execution hierarchy

### General

```text
direct API / OS command
→ known app adapter
→ structured capability action
→ LLM reasoning only if needed
```

### Browser / desktop

```text
Native API / app integration
→ Playwright DOM/accessibility
→ Windows UI Automation
→ keyboard shortcut
→ deterministic fast path / known coordinates
→ targeted OCR
→ local vision grounding
→ physical mouse coordinates
```

Physical control is a last-mile fallback.

User manual input cancels/halts Charlie's active physical-control session immediately.

---

## 8. Task and lease architecture

One task journal for:
- foreground turns;
- background tasks;
- research;
- browser work;
- long system operations;
- self-extension.

States:

```text
queued
planning
waiting
running
paused
approval_required
verifying
completed
failed
cancelled
```

Task state is not the same as resource ownership.

Capability leases:
- mouse;
- keyboard;
- desktop observe→act→verify;
- browser profile/session;
- terminal session;
- other explicitly exclusive resources.

Priority:
- user foreground → high;
- user background → normal;
- proactive/maintenance → low.

---

## 9. Event/state contract

Backend remains canonical.

Target versioned event envelope:

```json
{
  "type": "workspace.intent",
  "version": 1,
  "id": "event-id",
  "timestamp": "server-time",
  "source": "brain",
  "session_id": null,
  "task_id": null,
  "replay": false,
  "payload": {}
}
```

Explicit categories:
- durable snapshots;
- transient events;
- commands requiring acknowledgement;
- progress;
- append-only result/audit entries.

Generate/validate shared Python/TypeScript contracts rather than relying on loose casts.

Frontend Zustand = projection/cache, not authoritative runtime state.

---

## 10. PresentationResolver

Presentation is separate from execution.

Inputs:
- result;
- task state;
- user request;
- importance;
- active HUD state;
- current workspace;
- screen real estate;
- pinned surfaces;
- urgency.

Outputs:

```text
SILENT
CAPTION
NOTIFICATION
WIDGET
COMPOSED_SURFACE
WORKSPACE
ATTENTION
```

Examples:

```text
"Turn Bluetooth off"
→ SystemCapability
→ verified
→ tiny success feedback

"What's my CPU usage?"
→ SystemCapability
→ system widget
→ auto-dismiss

"Show full system status"
→ System workspace

"What's happening today?"
→ research/news briefing workspace
```

---

## 11. CharlieScene

```text
CharlieScene
├─ EnvironmentLayer
│  ├─ opaque dark navy/near-black base
│  ├─ subtle technical grid
│  ├─ radial lighting
│  ├─ restrained noise/grain
│  └─ strong four-edge/corner vignette
├─ WorkspaceLayer
├─ ContentMaskLayer
├─ WidgetLayer
├─ ContextLayer
│  ├─ captions
│  ├─ notifications
│  └─ approval/attention
└─ CharlieCore
```

### Core

Reuse existing Charlie core.

States may change:
- particle velocity/density;
- ring motion;
- pulse;
- deformation;
- state accent.

Position:
- idle/listening → centered;
- active primary workspace → docked, normally bottom-right;
- no arbitrary random movement.

### Visual language

- primary accent: cool cyan / teal-blue;
- semantic state accents allowed;
- UI: Geist Sans;
- code/data/terminal: JetBrains Mono;
- small radii;
- thin/partial borders;
- restrained HUD framing;
- minimal permanent chrome;
- hover/proximity controls;
- strong content masking/vignette.

---

## 12. WorkspaceManager

Rules:
- one primary workspace by default;
- no automatic split;
- explicit split/composition supported;
- inactive primary workspace can be minimized into Recent;
- no permanent taskbar;
- voice can restore recent work;
- closing visual surface does not cancel its backend task unless explicitly requested.

Workspace examples:
- research/news;
- map;
- camera/vision;
- tasks;
- full system;
- terminal;
- conversation history;
- settings;
- diagnostics.

---

## 13. WidgetManager

Capabilities:
- open;
- close;
- focus;
- drag;
- resize;
- minimize;
- pin;
- auto-dismiss;
- restore.

Placement:
- layout zones / free-space detection;
- collision avoidance;
- pinned surfaces immutable to Charlie auto-layout;
- temporary widgets may be moved automatically.

Examples:
- CPU status;
- media;
- task progress;
- approval/input;
- notifications;
- compact result cards.

---

## 14. SurfaceComposer

Schema-driven dynamic UI.

Primitives:
- text;
- image/video;
- metric;
- progress;
- list;
- table;
- chart;
- timeline;
- map;
- source/evidence;
- button/action;
- layout.

No arbitrary runtime JavaScript/React/CSS generation.

Surfaces can be:
- patched live;
- pinned;
- interacted with through semantic actions;
- persisted as layout when pinned.

---

## 15. News/research presentation

Broad news request should create a dynamic briefing workspace.

Adaptive patterns:
- daily briefing → top stories + visuals + context;
- major breaking event → hero + timeline + sources;
- market-heavy → chart + events + stories;
- geopolitical → map + timeline + sources.

Research workspace progressively shows:
- search status;
- sources found/analyzed;
- evidence;
- synthesis;
- conflicts/uncertainty;
- visuals/comparisons.

Never show private chain-of-thought. Show useful execution state.

World-monitor-like concepts are **inspiration only**:
- progressive disclosure;
- map layers;
- time/category filters;
- situational briefing density.

No dependency or clone requirement.

---

## 16. Voice

- wake word optional/off by default;
- current model `charlie.onnx`;
- local STT/TTS path retained;
- speaking user interrupts Charlie;
- short operational speech by default;
- detailed answer when requested/needed;
- temporary captions;
- follow-up conversational window without repeated wake word;
- voice does not force HUD open.

---

## 17. Terminal

The Charlie Terminal is a real interactive shell surfaced inside HUD.

Target:

```text
xterm-like React renderer
       ↕ WebSocket/stream
Windows ConPTY bridge
       ↕
PowerShell / CMD / optional WSL
```

Requirements:
- persistent cwd/state;
- real prompt;
- real stdout/stderr;
- ANSI/VT;
- resizing;
- safe terminal lease;
- user and Charlie can share the session with ownership rules.

---

## 18. Memory and conversations

Persist full conversation sessions and allow:
- search;
- resume;
- rename;
- delete.

Long-term memory remains selective:
- preferences;
- explicit memories;
- stable useful facts;
- task result references.

Do not automatically store screenshots/camera frames.

Use retrieval to reconstruct continuity across sessions.

---

## 19. Vision/camera

Vision model is local.

Screen flow:

```text
structured desktop observation
→ targeted OCR when needed
→ local visual grounding if needed
→ semantic target
→ DesktopCapability action
```

Vision never owns mouse/keyboard.

Camera:
- on demand;
- visible active state;
- transient frames;
- no continuous recording by default.

Live watch tasks are later.

No gesture subsystem.

---

## 20. Settings / configuration

Settings is a modal/workspace, not permanent navigation.

Categories:
- General;
- Voice;
- Appearance;
- HUD;
- Pet;
- Automation;
- Memory;
- Privacy;
- Models;
- Tools;
- MCP;
- Integrations;
- System;
- Developer.

Typed settings metadata:
- type;
- validation;
- secret;
- live reload;
- service restart;
- full restart.

`.env`/config changes are reflected safely.
If restart is required, Charlie asks/prompts.

Secrets are never returned to React after save.

---

## 21. SelfKnowledge

Runtime-generated self model:

```text
SelfKnowledge
├─ CodeIndex
├─ CapabilityIndex
├─ RuntimeIntrospection
├─ ConfigurationIntrospection
├─ HealthModel
└─ ArchitectureKnowledge
```

Charlie answers questions about itself from live code/config/runtime state.

No hardcoded giant “self-description” prompt as the primary truth source.

---

## 22. SelfExtension

Controlled self-extension:

```text
request
→ inspect self
→ classify extension:
   skill / config / MCP / code
→ checkpoint
→ minimal implementation plan
→ implement
→ tests
→ health check
→ restart affected service if required
→ verify
→ rollback on failure
→ report
```

Independent spontaneous core code changes require approval.

Explicit “add this to yourself” authorizes the requested change, subject to normal destructive/system boundaries.

---

## 23. Health / Doctor

Expose:
- `charlie doctor`;
- optional repair mode;
- Settings → System → Diagnose.

Check:
- core/Brain;
- voice;
- browser;
- desktop;
- research;
- memory;
- models;
- MCP;
- event bus;
- configuration;
- HUD.

Safe internal service recovery may be automatic.
Consequential repair asks first.

---

## 24. Web-first / future Electron

V1:

```text
React + Vite + TypeScript
          ↕
typed WebSocket/API contract
          ↕
Python Charlie runtime
```

Future:

```text
same React HUD
      ↓
Electron host adapter
      ↕
same runtime contract
```

Electron is not a V1 dependency and should not infect the architecture.

Keep current native pet for V1.

Retire Qt HUD gradually after React parity.

---

## 25. Scope boundaries

### Mandatory V1 redesign
- state/event/task contract work required by new HUD;
- deterministic/capability routing improvements;
- PresentationResolver;
- CharlieScene;
- workspaces/widgets;
- SurfaceComposer;
- settings;
- conversation history;
- task workspace;
- research/news briefing;
- system widgets/workspace;
- real Charlie terminal;
- execution status;
- user takeover;
- verification;
- auto-dismiss/pinning;
- recovery/replay;
- Qt migration path;
- self-knowledge foundation.

### Recommended if architecture allows
- Charlie Doctor;
- improved watchers;
- selected direct app/OS adapters;
- map workspace;
- notification/result history;
- memory management UI;
- multi-monitor polish;
- Charlie physical-control cursor indicator.

### Later
- gestures;
- continuous live camera vision;
- deep Photoshop/Blender/CAD autonomy;
- multi-LLM routing unless additional models configured;
- uncontrolled autonomous skill creation;
- permanent multi-agent swarm;
- plugin marketplace;
- Electron packaging;
- World Monitor dependency/integration;
- native Windows UI rewrite.

---

## 26. Key acceptance statement

V1 is successful when:

> Charlie remains the same assistant and preserves existing working capabilities, but its runtime has clear ownership, its common actions are materially faster and more deterministic, its task/event state is coherent, its HUD feels like one adaptive spatial environment rather than a dashboard of panels, and Charlie can truthfully understand its own capabilities/runtime while presenting only the information useful to the current task.
