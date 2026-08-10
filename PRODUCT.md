# Product

## Register

product

## Platform

web

## Users
A single power-user running Charlie on their own PC. They're the operator and the sole audience: no other accounts, no shared workspace. The job to be done is real-time monitoring and control of an autonomous local AI agent (voice assistant plus desktop-control tools) that mostly acts on its own — the dashboard exists for the moments the user needs visibility into what Charlie is doing, or needs to step in directly.

## Product Purpose
Charlie's dashboard is the control surface for an "autonomous agentic OS": it surfaces the voice pipeline's live state, the tool-call and agent-orchestration timeline, session history, and desktop-control activity, and lets the user intervene (approve/reject gated actions, stop a turn, switch sessions) without breaking Charlie's flow. Success looks like the user glancing at the screen and immediately knowing what Charlie is doing, why, and whether it needs them.

## Positioning
A real-time control surface for an autonomous local AI agent: live visibility into voice, tool, and agent activity, plus direct override, for a system that mostly acts on its own.

## Brand Personality
Precise, calm, capable. The interface should read like a cockpit or mission-control console: dense with real information, quiet when nothing is happening, and unshaken when a lot is happening at once. Confidence comes from clarity, not decoration.

## Anti-references
Not a generic SaaS admin panel — no cream/off-white card grids, no hero-metric tiles, no eyebrow-labeled sections. The current dark-glass, particle-sphere Control Center is closer to the right register (sci-fi HUD / mission-control) than a typical B2B dashboard; departure-mode variants should stay in that family, not drift toward conventional admin-panel design.

## Design Principles
- Density with clarity: pack real state (voice, tools, agents, sessions) without turning into noise — hierarchy does the work, not whitespace-for-its-own-sake.
- Calm by default, alert when it matters: the UI should be quiet during normal autonomous operation and unambiguous the moment user attention or approval is needed.
- One dark HUD, not light/dark parity: the existing black-canvas, glass-panel, purple-accent system is the identity — extend it, don't dilute it with a generic light theme.
- Icons and status color carry meaning: lucide-react icons and the existing status palette (idle/listening/thinking/speaking/error) are the vocabulary; reuse it rather than inventing new visual language per surface.
- Show the machine thinking: real-time feedback (streaming tokens, live captions, tool-call timelines, agent status) is the point of the dashboard, not a nice-to-have.

## Accessibility & Inclusion
No formal WCAG target; single local user. Respect `prefers-reduced-motion` (already implemented) and keep contrast reasonable on the dark canvas — but this is not held to a public-facing compliance bar.
