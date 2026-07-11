# Frontend ⇄ Backend Sync & Connection Hardening — Design Spec

> **Status:** Approved for planning
> **Date:** 2026-07-12

## Goal

Fix the sync/connection bugs between the voice process (`main.py`), the web
server (`charlie/web_server.py`, FastAPI + WebSocket), and the frontend
(`frontend/src`) so that: (1) switching sessions in the sidebar reliably shows
the correct per-session messages, (2) every backend event that is emitted is
actually surfaced in the UI (no silently dropped events), and (3) the
connection is robust against startup races and duplicate streams.

## Architecture

The system is three processes bridged by a ZMQ `EventBus` (`charlie/ipc.py`):
`main.py` PUBlishes lifecycle/stream events; `web_server.py` runs a SUB
consumer that re-broadcasts them over a per-session WebSocket; the frontend
subscribes and dispatches into a Zustand store (`frontend/src/store/useCharlieStore.ts`).

Session isolation already exists correctly at the persistence layer
(`SessionStore.get_session_messages` filters `WHERE session_id = ?`). The bugs
are in the **event envelope** (events missing `session_id`), the **frontend
fetch race** on session switch, and the **WS dispatch switch** (events emitted
but not handled). This spec does NOT change the storage model or the ZMQ
topology — only the event envelopes, the WS dispatch, and the React fetch/sync
logic.

## Tech Stack

- Python 3.12, FastAPI (web_server.py), ZMQ via `charlie/ipc.py`
- Next.js 14 + React 19 + Zustand (frontend)
- Tests: `pytest` (backend), `vitest` + `tsc --noEmit` (frontend)

## Findings Being Addressed (from bug hunt)

| # | Finding | Bucket | Fix |
|---|---------|--------|-----|
| 1 | `thinking`, `speaking_start`, `speaking_stop`, `response_done` emitted with `{}` (no `session_id`) | A | Attach `session_id` to every emit; all WS handlers filter by it (strict isolation) |
| 2 | `/api/session/active` HTTP POST is dead code (backend ignores it) | B | Route POST through to `current_web_session_id` in `consume_web_commands` |
| 3 | `fetchMessages` abort-guard race on rapid session switch → old session messages render in new session | A | Capture `sid` in closure; discard result if `currentSessionIdRef.current !== sid` at resolve |
| 4 | `tool_call`, `tool_result`, `thinking_update` emitted but dropped by frontend `onMessage` switch | B | Add WS handlers; render inline (collapsible rows) + EventLog feed |
| 5 | `audio_level` emitted continuously but never rendered | B/stretch | Add mic-level meter component fed by `audio_level` |
| 6 | `session_updated` handled + emitted — verified working | — | No change |
| 7 | ZMQ slow-joiner: `session_active` sent on WS open may be lost before backend SUB ready | C | Re-send `session_active` after short delay on open (idempotent re-sync) |
| 8 | HTTP `/chat` fallback + primary WS stream can double-fire during a blip | C | Guard HTTP fallback so it cannot run while a WS stream is active for the same session |

## Stretch Items (approved)

- **Mic-level meter:** visible component fed by `audio_level` events.
- **Persist tool activity:** per-session tool calls/results/reasoning persisted
  to `session_store` so they survive page refresh (not just live in-memory).
- **WS test coverage:** TDD tests for the sync paths (session-switch race, event
  scoping, tool surfacing) — the WS/sync layer currently has zero coverage.

## Design

### 1. Strict per-session event envelopes (Findings #1, #2)

Every `event_bus.emit(...)` in `main.py` and `charlie/web_server.py` that
represents a session-scoped event MUST include `session_id` in its payload.
Session-scoped events: `token`, `transcript`, `thinking`, `response_done`,
`speaking_start`, `speaking_stop`, `tool_call`, `tool_result`, `thinking_update`,
`audio_state`, `mic_state`. Global events (`system_status`, `blackboard_update`,
`log`, `alert`, `session_updated`, `wake_word`, `vad_start`, `audio_level`) keep
their existing shape where appropriate, but `audio_level` is left global
(mic level is a UI concern, not a session one).

The frontend `sessionOf(msg)` helper already extracts `session_id` from
top-level or `payload`. WS handlers for session-scoped events will:
- read `eventSession = sessionOf(msg)`;
- if `eventSession` is present and `eventSession !== currentSessionIdRef.current`, return (ignore).

This makes cross-session events impossible to bleed into the wrong thread.

`/api/session/active` POST: `web_server.py` already has the route; it must push
the `session_id` to `main.py` so `consume_web_commands` updates
`current_web_session_id`. Wire it via the existing command channel (treat the
HTTP POST body as a `session_active` command).

### 2. Session-switch fetch race (Finding #3)

`fetchMessages(currentSessionId)` in `page.tsx`:
- capture `const sid = currentSessionId;` (or read the arg);
- on resolve, `if (currentSessionIdRef.current !== sid) return;` before
  `setMessages(...)`;
- keep the existing `fetchMessagesInFlight` guard but ALSO check the ref at
  resolve time (the current guard only blocks re-fetching the same sid, not
  out-of-order completion).

### 3. Surface dropped backend events (Findings #4, #5)

WS `onMessage` switch gains handlers for `tool_call`, `tool_result`,
`thinking_update`, `audio_level`:
- `tool_call` / `tool_result` / `thinking_update` → push an entry into a new
  store slice `toolActivity` (array, per active session) AND into EventLog;
  rendered inline in `ChatView` as **collapsible rows** under the assistant
  bubble (collapsed by default), e.g. `🔧 Ran web_search → 3 results`.
- `audio_level` → `setAudioLevel(level)` consumed by the new mic meter.

### 4. Robustness (Findings #7, #8)

- **#7:** In `connectWS`, after `ws.onopen` sends `session_active`, schedule a
  second `session_active` send after a short delay (~250ms) so a lost
  slow-joiner packet is recovered. Idempotent.
- **#8:** Before issuing the HTTP `/chat` fallback POST
  (`handleSendMessage`), check a per-session flag `wsStreamingRef[sessionId]`;
  if a WS stream is already active for that session, skip the HTTP fallback.

### 5. Stretch — persistence + meter + tests

- `session_store.py`: add `append_tool_event(session_id, kind, name, text)` and
  `get_tool_events(session_id)` (new table `tool_events` with
  `session_id, kind, name, text, created_at`). `getSessionMessages`/chat load
  merges tool events so they survive refresh.
- New frontend `MicMeter` component reads `audioLevel` from store.
- Tests: backend integration test asserting a `thinking` event with
  `session_id="B"` does NOT reach the WS handler filtering for session "A";
  frontend test (vitest) asserting `fetchMessages` for stale sid is discarded.

## Data Flow (after fix)

```
main.py emit(session_id=X)
   → EventBus PUB (ZMQ)
      → web_server SUB consumer (filters by subscription)
         → WebSocket (session-scoped topics)
            → page.tsx onMessage
               → sessionOf(msg) === currentSessionIdRef ?
                  yes → store update (messages / toolActivity / audioLevel)
                  no  → ignore (strict isolation)
```

## Error Handling

- Malformed WS packets: keep `try/catch` ignore (socket stays alive).
- `fetchMessages` network failure: keep `setMessages([])` + `setMessagesLoading(false)`.
- Tool-event persistence failure: log + continue (non-fatal, matches AGENTS.md
  "expected failures use debug/warning").

## Testing

- Backend (pytest): event envelope includes `session_id`; WS consumer delivers
  only matching session; `/api/session/active` updates `current_web_session_id`.
- Frontend (vitest): `fetchMessages` discards stale sid; `tool_call` handler
  appends to `toolActivity`; `audio_level` updates `audioLevel`; `session_active`
  re-send scheduled on open.
- Gate per task: `uv run ruff check .`, `uv run pytest -v`, `cd frontend && npx tsc --noEmit`, `npm test`.

## Out of Scope

- No change to ZMQ topology, storage model, or provider-agnostic config rules.
- No new LLM/voice features.
- No UI redesign beyond the meter + collapsible rows + EventLog feed.
