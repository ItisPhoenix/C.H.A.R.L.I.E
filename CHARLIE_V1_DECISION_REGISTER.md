# C.H.A.R.L.I.E. V1 — Locked Decision Register

This file prevents later coding sessions from silently re-litigating or forgetting product decisions.

## Product
- Charlie is an autonomous assistant / Agentic OS.
- Assistant first, HUD second.
- Not a Codex clone.
- Not a permanent dashboard.
- Web-first V1, Electron later.
- Local working tree is the implementation truth.

## Surfaces
- Floating Charlie pet remains on desktop.
- HUD is dedicated, opaque, full-screen-capable, responsive.
- Pet transforms conceptually into HUD core; do not show duplicate pet + core.
- HUD opens via voice, pet double-click, global shortcut, tray/quick menu.
- Closing HUD leaves pet/runtime/tasks alive.
- Qt HUD is legacy/migration path and should eventually be retired.
- React HUD is primary future surface.
- Settings is modal/workspace, not a permanent sidebar.

## HUD
- Idle = almost empty.
- Existing Charlie core centered.
- Workspace opens → core docks, normally bottom-right.
- One primary workspace by default.
- Secondary contextual widgets.
- No automatic large split views.
- `clear screen` removes temporary UI/minimizes workspace but keeps tasks/pinned widgets.
- `clear everything` temporarily hides pinned UI too.
- Escape/close applies only to selected/focused surface.
- Recent workspaces/results are retrievable; no permanent taskbar.

## Visual
- Dark navy/near-black base.
- Subtle technical grid.
- Strong four-edge/corner vignette.
- Content visually blends into scene.
- Restrained HUD framing.
- Main cyan/teal-blue identity color.
- Semantic state accents.
- Geist Sans + JetBrains Mono.
- No SaaS card wall.
- No excessive glass.
- Fast functional animation.
- Responsive/adaptive density.
- Preserve existing core.

## Widgets / SurfaceComposer
- Common feature → dedicated widget.
- One-off information → schema-driven composed surface.
- Charlie may create temporary UI.
- No arbitrary runtime React/JS/CSS generation.
- Dynamic surfaces can patch live.
- Semantic UI actions; never mouse-simulate Charlie's own UI.
- Temporary widgets auto-place in free space.
- Pinned widgets are user-owned and not auto-moved.
- Auto-dismiss is contextual, not one fixed timeout.

## News / research
- Broad news → visual briefing workspace.
- Dynamic/adaptive layouts.
- Visuals only when useful.
- Sources secondary but accessible.
- Research progress shown semantically, not chain-of-thought.
- Follow-up extends current workspace.
- World Monitor = inspiration only, no dependency/clone requirement.

## Voice
- Wake word optional/off by default.
- Current `charlie.onnx` should be evaluated.
- Start speaking interrupts TTS.
- Follow-up conversation window.
- Captions temporary.
- Short responses by default; detailed when requested/needed.
- Talking does not automatically open HUD.

## Vision / camera
- Vision local.
- Screenshots/images temporary.
- No continuous screen analysis.
- Camera on demand.
- No gesture control.
- Vision perceives; DesktopCapability acts.
- UIA/OCR before vision for UI control.

## PC/browser
- deterministic/API first.
- Playwright before physical browser clicking.
- UIA before OCR/vision.
- physical mouse last.
- visible cursor-control indicator desirable.
- manual user takeover cancels/halts physical-control session.
- complex creative apps best-effort, not V1 autonomy target.

## Terminal
- Charlie terminal is a real persistent Windows shell session.
- Real current directory/prompt/state.
- Render inside Charlie HUD.
- Explicit “open Windows Terminal” opens native Windows Terminal.

## Tasks
- multiple logical tasks can run.
- resource leases prevent conflict.
- compact task count by default.
- “what's running?” opens task workspace.
- closing HUD does not stop tasks.
- meaningful milestones only, no click-by-click narration.
- bounded retries; no loops.

## Autonomy
- safe/reversible/local → act.
- destructive/ambiguous/consequential → confirm.
- explicit instruction authorizes the explicit action.
- `rm -rf`-class / format / mass destructive ops → final confirmation.
- no UAC bypass.
- upload ≠ publish.
- draft ≠ send.
- explicit “publish/send” authorizes.
- purchases/payments require final confirmation.
- unsaved-work consequence triggers confirmation.

## Agents
- one primary Charlie Brain.
- bounded temporary sub-agents allowed when useful.
- no permanent role-agent swarm.
- V1: ~2 concurrent, no nesting.
- parent verifies and communicates.

## Tools/capabilities
- one authoritative owner per capability.
- auto-discovered CapabilityIndex.
- MCP integrates through capability model.
- plugins future.
- LLM not used for deterministic mechanics.

## Memory
- persistent conversation sessions.
- cross-session continuity through retrieval.
- selective durable memory.
- explicit remember durable.
- memory inspect/edit/delete/export.
- do not automatically persist screenshots/camera frames.
- do not prematurely merge all memory stores.

## Proactive
- highly restrained.
- most events silent/subtle.
- meaningful task completion/failure/approval only.
- deterministic watchers.
- LLM only after meaningful signal.
- safe internal self-heal allowed; consequential repair asks.

## Settings
- General, Voice, Appearance, HUD, Pet, Automation, Memory, Privacy, Models, Tools, MCP, Integrations, System, Developer.
- settings map to validated config/.env.
- live/restart metadata.
- prompt when restart required.
- secrets never read back into React.
- one cloud LLM/model currently.
- vision and embedding models separate.
- multi-LLM optional later.

## Self-knowledge / extension
- Charlie knows live code, architecture, capabilities, models, config, health, tasks.
- no hardcoded self-knowledge as primary truth.
- explicit “add this to yourself” can trigger controlled extension.
- checkpoint → minimal change → test → health → restart → verify → rollback.
- no spontaneous core rewrite without approval.

## V1 later/deferred
- gestures.
- continuous camera vision.
- deep Photoshop/Blender/CAD autonomy.
- permanent swarm.
- nested agents.
- plugin marketplace.
- Electron packaging.
- native Windows rewrite.
- World Monitor integration.
