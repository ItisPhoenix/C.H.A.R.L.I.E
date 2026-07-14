@AGENTS.md

# Frontend Agent Instructions

This is the Next.js 16 / React 19 / Zustand 5 dashboard for Charlie. It talks to
`charlie/web_server.py` over WebSocket and HTTP (`/api/status`, etc.).

Full frontend conventions (WebSocket event names, ErrorBoundary, Zustand selectors, glass
morphism, prop typing) live in the root `../CLAUDE.md`, section 8.5 "Frontend Patterns" -- read
that before touching this directory. This file only adds what's specific to running/verifying the
frontend in isolation.

## Verification

Run in order before declaring a frontend change done:
```bash
npx tsc --noEmit
npm run lint
npm test
```
All three must pass cleanly. `npm test` runs vitest; store tests live in `src/store/*.test.ts`.

## Local dev

`npm run dev` starts the Next.js dev server standalone, but the dashboard is only fully
functional against a running `charlie/web_server.py` (normally launched by `main.py`, which also
sets `CHARLIE_LAUNCH_ID` -- see root CLAUDE.md section 11.3). For UI-only iteration this is fine;
for anything touching live data (sessions, blackboard, transcripts) run the full stack via `main.py`.
