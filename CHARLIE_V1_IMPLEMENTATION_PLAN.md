# C.H.A.R.L.I.E. V1 — Implementation Plan

> Agent-agnostic. Execute against the **actual local working tree**.
>
> Do not start by rewriting. Establish baseline, migrate vertically, verify continuously.

---

# Phase 0 — Safety, Skills, Baseline

## Goal
Create a reproducible implementation baseline without altering product behavior.

## 0.1 Activate workflows
- Invoke `using-superpowers`.
- Invoke `caveman`.
- Inspect and invoke every relevant repository/build/test/frontend/debugging skill.
- Re-evaluate skills whenever work type changes.

## 0.2 Protect local work
- Run `git status`.
- Record branch/HEAD.
- Inspect staged, unstaged, and untracked work.
- Create safe checkpoint/branch/backup strategy.
- Do not reset or clean.

## 0.3 Baseline verification
Run the repository-supported:
- Python tests;
- frontend typecheck;
- frontend tests;
- production frontend build;
- lint if configured;
- startup smoke tests;
- current web HUD;
- current pet;
- relevant browser/desktop test subsets.

Record failures that predate V1 work.

## 0.4 Baseline performance
Measure:
- backend startup;
- frontend startup/build;
- HUD idle CPU/GPU;
- deterministic command latency;
- desktop observation latency;
- browser task latency;
- voice first transcript/token/audio if practical.

## 0.5 Current architecture inventory
Confirm the audit against live code:
- Brain;
- state/event bus;
- tasks/background tasks;
- tools;
- desktop;
- browser;
- research;
- voice;
- memory;
- web server;
- pet;
- Qt HUD/surfaces;
- React dashboard/surfaces.

## Acceptance
- no user work lost;
- baseline documented;
- tests/build status known;
- dirty-tree state understood;
- no behavior intentionally changed.

---

# Phase 1 — Typed State/Event Contract

## Goal
Make backend state and frontend projection trustworthy before redesigning presentation.

## 1.1 Event taxonomy
Define explicit categories:
- snapshot;
- transient event;
- command/request;
- acknowledgement;
- progress;
- result/audit.

## 1.2 Versioned envelope
Introduce shared versioned event metadata:
- id;
- type;
- version;
- timestamp;
- source;
- task/session;
- replay semantics.

## 1.3 Shared validation
Implement/generated shared Python ↔ TypeScript schemas.

## 1.4 Replay/hydration
Ensure a newly connected HUD can reconstruct:
- Charlie state;
- active tasks;
- approvals;
- voice/mic state;
- active workspace;
- pinned widgets;
- important notifications/results.

## 1.5 Zustand boundary
Refactor frontend so Zustand projects backend truth rather than inventing global runtime state.

## Tests
- event validation;
- replay;
- reconnect;
- late connect;
- malformed event handling;
- version mismatch behavior.

## Acceptance
A frontend reload reconnects without losing task/state truth.

---

# Phase 2 — Unified Task Journal & Capability Leases

## Goal
Remove lifecycle ambiguity between foreground/background/browser/research work.

## 2.1 Task schema
Unify statuses:
- queued;
- planning;
- waiting;
- running;
- paused;
- approval_required;
- verifying;
- completed;
- failed;
- cancelled.

## 2.2 Adapters
Adapt existing foreground/background/browser/research paths into the unified journal without deleting working logic immediately.

## 2.3 Priority
Implement:
- foreground high;
- user background normal;
- maintenance/proactive low.

## 2.4 Capability leases
Introduce explicit leases for:
- physical mouse;
- keyboard where required;
- desktop observe→act→verify;
- browser profile/session;
- terminal session.

## 2.5 Cancellation
Support semantic cancellation:
- “cancel that”;
- named task;
- stop everything;
- user physical takeover.

## Tests
- two concurrent non-conflicting tasks;
- conflicting desktop tasks;
- cancellation;
- lease timeout;
- task recovery after reconnect;
- background task continues when HUD closes.

## Acceptance
No two tasks fight for the same physical capability and the HUD can truthfully show task state.

---

# Phase 3 — Capability Contract & Tool Ownership

## Goal
Turn the current tool mega-layer into capability-owned execution without breaking compatibility.

## 3.1 CapabilityDescriptor
Add:
- ID;
- owner;
- operations;
- schemas;
- risk;
- availability/health;
- lease;
- timeout;
- verifier.

## 3.2 Capability Index
Auto-discover built-ins and compatible MCP tools.

## 3.3 Compatibility layer
Keep existing tool names where necessary but dispatch into authoritative capability owners.

## 3.4 Split implementation ownership
Migrate implementation out of `tools.py` gradually.

## 3.5 Public errors
Sanitize errors before model/UI/public surfaces.
Preserve detailed diagnostics in logs/developer mode.

## Acceptance
Tool/capability ownership is inspectable; duplicate routes are explicitly known; adding a capability no longer requires hardcoding central routing branches everywhere.

---

# Phase 4 — Deterministic Fast Path & Verification

## Goal
Make Charlie materially faster and more reliable before adding visual complexity.

## 4.1 Direct routing
Prioritize:
- OS/system APIs;
- known apps;
- media;
- files;
- settings;
- shell/terminal;
- direct browser DOM.

## 4.2 Desktop observation
Change normal flow toward:
- UIA first;
- OCR only when needed;
- bounded-region OCR;
- local vision grounding last.

## 4.3 Browser
Preserve:
- accessibility/DOM first;
- recipes/fast paths;
- bounded agent fallback.

Separate research/read from interactive browser control.

## 4.4 Verifiers
Implement semantic postconditions for meaningful actions.

## 4.5 User takeover
Detect manual mouse/keyboard takeover during physical automation and halt/cancel physical control.

## 4.6 Cursor control indicator
If feasible, add a subtle Charlie visual cue during physical cursor ownership.

## Acceptance
Common known commands avoid LLM/vision; ordinary UIA observations do not pay full-screen OCR cost; “done” means verified.

---

# Phase 5 — PresentationResolver

## Goal
Separate what Charlie does from what Charlie displays.

## 5.1 Presentation model
Implement:
- SILENT;
- CAPTION;
- NOTIFICATION;
- WIDGET;
- COMPOSED_SURFACE;
- WORKSPACE;
- ATTENTION.

## 5.2 Decision inputs
Use:
- request intent;
- result type;
- urgency;
- task state;
- active HUD;
- current workspace;
- free screen space;
- pinned surfaces.

## 5.3 First vertical slices
Implement:
1. CPU query → system widget → auto-dismiss.
2. full system status → system workspace.
3. research → research workspace.
4. task state → compact indicator / task workspace.
5. approval → attention modal.

## Acceptance
The same capability can produce different presentations depending on user intent without UI logic leaking into capability execution.

---

# Phase 6 — CharlieScene Foundation

## Goal
Replace dashboard-first information architecture with the unified opaque spatial HUD.

## 6.1 EnvironmentLayer
Build:
- dark navy/near-black background;
- subtle grid;
- radial illumination;
- restrained grain/noise;
- strong four-edge/corner vignette.

## 6.2 Existing Charlie core
Integrate existing core:
- centered idle/listening;
- dynamic docking;
- state-driven animation;
- responsive scale;
- no replacement orb.

## 6.3 Responsive layout
Support:
- windowed browser;
- fullscreen browser;
- standard 16:9;
- ultrawide;
- laptop/smaller viewport.

Use layout zones, not hardcoded stage rectangles.

## 6.4 Motion
Implement fast transitions:
- workspace materialization;
- core docking;
- widget entry/exit;
- minimized/restore;
- reduced motion.

## 6.5 Core menu
Compact:
- Tasks;
- Recent;
- Chat;
- Settings;
- Clear screen;
- Close HUD;
- mic/listening if useful.

## Acceptance
Idle HUD is calm/minimal; opening a workspace feels embedded in one scene; 60 FPS is targeted where hardware supports it and effects degrade gracefully.

---

# Phase 7 — WorkspaceManager + WidgetManager

## Goal
Provide one coherent visual surface system.

## 7.1 Workspace lifecycle
- open;
- update;
- focus;
- minimize;
- restore;
- close.

## 7.2 Widget lifecycle
- open;
- drag;
- resize;
- focus/z;
- pin;
- auto-dismiss;
- close.

## 7.3 Placement
Implement free-space/layout-zone selection and collision avoidance.

## 7.4 Ownership
- temporary widget → Charlie can reposition;
- pinned widget → user owns position.

## 7.5 Clear screen
Implement exact semantics from architecture.

## 7.6 Recent
Keep recent useful workspaces/results, not every trivial action.

## 7.7 Focused Escape/close
Operate only on selected/focused surface.

## Acceptance
No permanent panel clutter; widgets never randomly overlap; pinned layouts survive; workspace transitions do not cancel backend tasks.

---

# Phase 8 — SurfaceComposer

## Goal
Allow Charlie to create useful one-off UI without arbitrary code generation.

## 8.1 Schema
Implement validated primitives:
- text;
- metrics;
- list;
- table;
- chart;
- timeline;
- map;
- media;
- evidence/source;
- actions;
- layout.

## 8.2 Renderer registry
Build deterministic React renderers.

## 8.3 Live patching
Allow Charlie/backend to update existing composed surfaces.

## 8.4 Semantic interaction
Actions emit semantic events, not mouse simulation.

## 8.5 Pin/persist
Pinned composed surfaces remember layout and explicit refresh configuration.

## Security
No arbitrary runtime JS/React/CSS from LLM output.

## Acceptance
A new comparison/result UI can be created from structured data without adding a dedicated React component for every one-off request.

---

# Phase 9 — Essential V1 Workspaces

## 9A — Research / News briefing
Build dynamic/adaptive:
- main synthesis;
- evidence;
- sources;
- media/visuals;
- comparison;
- uncertainty/conflicts;
- progressive research status.

Do not show chain-of-thought.

## 9B — Tasks
Show:
- active;
- queued;
- blocked;
- approval;
- progress;
- verification;
- completion/failure.

## 9C — System
Compact widget and full workspace:
- CPU;
- RAM;
- GPU if available;
- disk;
- network;
- top processes;
- Charlie service health.

## 9D — Conversation
On demand only:
- browse;
- search;
- continue;
- rename;
- delete sessions.

No permanent ChatGPT-style chat pane.

## 9E — Settings
Usability-first modal/workspace with typed configuration metadata.

## 9F — Terminal
Real persistent shell session via ConPTY/PTY bridge + terminal renderer.

## 9G — Map/spatial primitive
If feasible in V1:
- native Charlie styling;
- map fades into scene;
- voice/semantic filters;
- generic spatial data primitive.

## Acceptance
Major information-heavy intents produce purpose-built adaptive presentations instead of generic cards.

---

# Phase 10 — Voice / Conversation Continuity

## Goal
Make voice feel immediate without forcing HUD usage.

## 10.1 Wake word
- optional;
- off by default;
- evaluate current `charlie.onnx`;
- sensitivity setting;
- no assumption that replacement is required before evaluation.

## 10.2 Captions
- temporary;
- only when useful;
- anchored to Charlie core in HUD;
- long detail belongs in workspace.

## 10.3 Barge-in
Speaking interrupts TTS immediately.

## 10.4 Follow-up window
Allow short conversational continuation without repeated wake word.

## 10.5 Persistent sessions
Persist sessions and retrieve relevant prior turns for continuity.

## Acceptance
Normal voice requests remain fast and do not automatically open the HUD.

---

# Phase 11 — Settings, Memory, MCP, Developer Tools

## 11.1 Typed SettingsService
Expose live/restart metadata and safe config writes.

## 11.2 `.env`
Allow supported `.env`-backed values to be changed safely.
Never return stored secrets.

## 11.3 MCP
Settings UI for:
- configured servers;
- status;
- capabilities/tools;
- connect/disconnect/restart where supported.

Plugins remain future.

## 11.4 Memory
Provide:
- search;
- inspect;
- edit;
- delete;
- clear category;
- export;
- auto-memory toggle.

## 11.5 Privacy
Expose retention controls for:
- transcripts;
- terminal history;
- browser/tool history;
- screenshots/camera;
- other stored artifacts.

## 11.6 Developer
Expose:
- event stream;
- state;
- leases;
- task IDs;
- tool/capability calls;
- model calls;
- logs;
- latency;
- workspace debug overlays.

## Acceptance
Configuration is manageable without manual `.env` editing for normal use, and developer debugging does not pollute normal HUD.

---

# Phase 12 — SelfKnowledge & Charlie Doctor

## Goal
Charlie knows its actual implementation/runtime and can diagnose itself.

## 12.1 CodeIndex
Index relevant repo/module structure incrementally.

## 12.2 CapabilityIndex integration
Expose live tools/capabilities/health.

## 12.3 Runtime introspection
Expose:
- processes/services;
- model configuration;
- runtime status;
- current tasks;
- event bus;
- memory providers;
- MCP.

## 12.4 Self answers
Route questions about Charlie to live introspection/code retrieval, not hardcoded prompt claims.

## 12.5 Doctor
Implement:
- structured checks;
- severity;
- evidence;
- fix hint;
- safe repair capability.

Expose CLI + Settings.

## 12.6 Self-healing
Auto-restart safe internal failures.
Ask before consequential repair/mutation.

## Acceptance
Charlie can truthfully explain its own capabilities/health and diagnose common internal failures.

---

# Phase 13 — Controlled Self-Extension

## Goal
Allow explicit “add this to yourself” workflows without uncontrolled self-modification.

## 13.1 Extension classifier
Decide:
- settings/config;
- reusable skill/procedure;
- MCP/tool;
- small code extension;
- larger architecture change.

## 13.2 Safe change workflow
- git checkpoint;
- inspect architecture;
- minimal plan;
- implement;
- tests;
- health check;
- restart;
- verify;
- rollback.

## 13.3 Reusable skills
Support explicit creation/update of reusable procedures where the runtime skill model permits it.

## 13.4 Source modification guard
Independent spontaneous source modification requires approval.

## Acceptance
An explicitly requested self-extension can be completed safely and verified without Charlie silently rewriting its own architecture.

---

# Phase 14 — Native Surface Migration / Cleanup

## Goal
Retire duplicated legacy UI only after React parity.

## 14.1 Qt inventory
Map every remaining Qt HUD/surface responsibility.

## 14.2 Migrate
Move:
- approvals;
- high-attention surfaces;
- contextual surfaces;
- workspace behavior
to React/event architecture.

## 14.3 Disable
Disable Qt HUD by default only after parity.

## 14.4 Remove
Remove dead/duplicate paths only after:
- usage check;
- tests;
- replacement verification.

## 14.5 Native pet
Keep the existing native pet for V1 unless a concrete blocker requires change.

## Acceptance
One primary React HUD presentation architecture remains; no silent loss of legacy capability.

---

# Phase 15 — Performance / Hardening / V1 Release

## 15.1 Performance
Re-run baseline metrics and compare.

## 15.2 Frontend
- profile rendering;
- reduce hidden animation;
- reduce full-rate state updates;
- adaptive particle density.

## 15.3 Backend
- profile hot routes;
- remove unnecessary LLM calls;
- remove eager OCR;
- improve startup/lazy loading where justified.

## 15.4 Reliability
Test:
- frontend crash/reconnect;
- backend service restart;
- task recovery;
- browser failures;
- desktop failures;
- model endpoint failure;
- offline/degraded deterministic functions.

## 15.5 Security/policy
Review:
- shell;
- files;
- publish/send;
- deletion;
- secrets;
- local API;
- MCP trust.

## 15.6 Documentation
Update only durable docs.

## 15.7 Final cleanup
Remove verified dead files/paths.
Never broad-clean untracked work.

## V1 Acceptance
Charlie:
- preserves current working capability;
- feels materially faster on deterministic actions;
- has coherent tasks/state/events;
- has one adaptive spatial HUD;
- uses contextual UI instead of permanent panels;
- has a real terminal;
- has persistent conversation continuity;
- knows its own runtime/capabilities;
- can safely extend itself when explicitly requested;
- can recover/reconnect without losing active work;
- has passing supported tests/build and documented limitations.

---

# Explicitly Deferred Beyond V1

- gesture control;
- continuous high-rate live camera understanding;
- full Photoshop/Blender/CAD autonomy;
- permanent multi-agent swarm;
- nested sub-agents;
- multi-LLM routing unless multiple models are configured;
- uncontrolled autonomous skill generation;
- plugin marketplace;
- Electron packaging;
- native Windows UI rewrite;
- World Monitor integration/clone.
