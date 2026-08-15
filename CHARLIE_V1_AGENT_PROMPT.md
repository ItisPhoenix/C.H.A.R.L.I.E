# C.H.A.R.L.I.E. V1 — Agent-Agnostic Implementation Prompt

You are the primary coding agent working directly in my local C.H.A.R.L.I.E. repository.

Your task is to execute the approved **C.H.A.R.L.I.E. V1 redesign** end-to-end, incrementally, while preserving working functionality.

## Before anything else

1. Read `AGENTS.md` completely and treat it as mandatory.
2. Invoke the mandatory skills:
   - `using-superpowers`
   - `caveman`
3. Inspect all available skills and invoke every additional skill relevant to the current task.
4. Read:
   - `CHARLIE_V1_ARCHITECTURE.md`
   - `CHARLIE_V1_IMPLEMENTATION_PLAN.md`
   - `CHARLIE_V1_DECISION_REGISTER.md`
   - current repository architecture/handoff docs
   - relevant tests
5. Run `git status` and inspect staged, unstaged and untracked work.
6. The **local working tree is the source of truth**. Do not assume GitHub is current.
7. Never reset, discard, overwrite, or broad-clean existing user work.

## Communication

Keep me in the loop throughout implementation.

Before each major phase:
- tell me what you inspected;
- tell me what you are changing;
- tell me any significant assumption.

During work:
- surface important bugs/architecture contradictions as soon as found;
- update after meaningful milestones;
- do not spam low-level command logs.

Ask me a question only when:
- a genuine product decision is unresolved;
- an action is destructive/irreversible;
- the repository contradicts the approved architecture in a way that cannot safely be reconciled;
- there are multiple materially different approaches and the choice affects product behavior.

Do not ask me questions the code/tests/docs can answer.

## Implementation behavior

Follow the phased implementation plan, but adapt sequencing when live code dependencies prove a different order is safer.

Use **vertical slices** and keep Charlie usable throughout.

Do not:
- rewrite Charlie from scratch;
- add a fixed multi-agent swarm;
- replace working browser/desktop/research/voice systems unnecessarily;
- replace the existing Charlie core;
- add gesture control;
- package Electron in V1;
- clone World Monitor;
- create arbitrary runtime React/JS from LLM output.

## Core outcome

Charlie V1 must become:
- assistant-first;
- deterministic-first;
- faster in common OS/browser tasks;
- capability-owned;
- task/lease safe;
- event/state coherent;
- persistent across sessions;
- self-aware through live introspection;
- safely self-extensible when explicitly instructed;
- visually unified through one adaptive React spatial HUD.

## UI outcome

Build/evolve the existing React frontend into the approved `CharlieScene`:
- opaque dark navy/near-black canvas;
- subtle technical grid;
- radial lighting;
- strong four-edge/corner vignette;
- restrained grain;
- existing Charlie core centered at idle;
- core dynamically docks when workspaces open;
- one primary workspace;
- contextual widgets;
- pinned/auto-dismiss behavior;
- dynamic schema-driven SurfaceComposer;
- no permanent dashboard clutter;
- responsive/adaptive to browser and monitor size;
- Geist Sans UI;
- JetBrains Mono terminal/data;
- fast functional motion;
- content/maps/media/research visually embedded into the scene.

## Mandatory V1 implementation areas

Implement/refactor as required by the live repository:
1. typed/versioned backend↔frontend event/state contract;
2. unified task journal;
3. capability leases;
4. capability ownership/index/discovery;
5. deterministic routing and semantic verification;
6. PresentationResolver;
7. CharlieScene;
8. WorkspaceManager;
9. WidgetManager;
10. SurfaceComposer;
11. task workspace;
12. research/news briefing workspace;
13. system widgets/workspace;
14. on-demand conversation history workspace;
15. settings modal/workspace backed by typed configuration;
16. real Charlie terminal with persistent Windows shell semantics;
17. persistent conversation continuity and selective durable memory;
18. MCP settings/status integration;
19. Developer/debug settings;
20. SelfKnowledge foundation;
21. Charlie Doctor/health model where practical;
22. controlled self-extension foundation;
23. React migration path away from legacy Qt HUD;
24. user physical-control takeover;
25. reconnect/replay/recovery behavior;
26. cleanup of verified obsolete/duplicate code after migration.

## Runtime principles

Target execution pipeline:

```text
Input
→ deterministic classification
→ multi-intent decomposition if needed
→ complexity assessment
→ plan only when needed
→ capability route
→ policy
→ leases
→ execute
→ verify
→ PresentationResolver
→ HUD/TTS/result
```

Target PC/browser hierarchy:

```text
Native API / direct app integration
→ Playwright DOM/accessibility
→ Windows UI Automation
→ keyboard shortcuts
→ deterministic fast paths
→ targeted OCR
→ local vision grounding
→ physical mouse
```

Do not use vision/LLM where structured deterministic control works.

## Sub-agents

Keep one primary Charlie orchestrator.

Temporary sub-agents may be spawned only when useful for isolated/parallel work.

V1 defaults:
- max ~2 concurrent;
- no nesting;
- restricted capability access;
- isolated context;
- parent verifies;
- parent communicates with user.

## Self-knowledge / self-extension

Charlie should answer questions about itself from live repo/runtime introspection.

When explicitly told to add functionality to itself:

```text
inspect self
→ classify skill/config/MCP/code change
→ checkpoint
→ minimal implementation
→ tests
→ health check
→ restart if required
→ verify
→ rollback on failure
→ report
```

Do not allow spontaneous unapproved core rewrites.

## Testing

At each phase, run the relevant tests immediately.

Before final completion run:
- backend/unit/integration tests;
- frontend tests;
- typecheck;
- production build;
- event-contract tests;
- workspace lifecycle;
- browser/desktop routing;
- task/concurrency/lease;
- permissions;
- reconnect/recovery;
- targeted performance measurements.

Never claim completion without verification.

## Cleanup

The repo is dirty and contains important uncommitted/untracked work.

For every cleanup candidate:

```text
identify
→ check references
→ verify replacement/obsolescence
→ test without it
→ remove only if safe
```

Never broad-clean untracked files.

## Final report

When the V1 scope is complete, give me:
1. architecture summary;
2. exact files/modules changed;
3. migrations completed;
4. legacy/deprecated paths remaining;
5. tests/build results;
6. baseline vs final performance measurements;
7. known limitations;
8. deferred V1+ items;
9. exact run instructions;
10. any decisions still genuinely requiring me.

Do not stop after only writing a plan. The architecture has already been approved.

Implement the V1 redesign phase-by-phase and keep me informed throughout.
