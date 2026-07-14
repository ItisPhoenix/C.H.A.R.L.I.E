# Phase 3 - Dashboard "Watch It Drive" Live View

> Part of the "Charlie -> Agentic Windows OS" initiative. Standalone; read on its own.
> Prerequisite: Phase 1 (UIA) shipped; Phase 2a/2b make the view more interesting but aren't required.

## Goal

Give the existing web dashboard a live view of what Charlie is doing on the desktop - the annotated
screenshot plus a step log - building trust and making it easy to catch a runaway action and hit Stop.

## Why this is its own phase

It needs a new event type carrying image data end-to-end (backend -> IPC -> web server -> frontend), and
a new frontend surface. Charlie's event bus today is JSON/text-only (`ipc.py` uses `send_string`), so
this phase adds an image-over-JSON convention rather than a new transport.

## Backend: new event

- In `charlie/desktop/ocr.py` / `vision.py` (whichever performed the most recent capture), after each
  `capture()` + annotate step, emit a `desktop_frame` event via the existing `EventBus.emit()`
  (`charlie/ipc.py`), payload shape:
  ```json
  {"session_id": "...", "image_b64": "<base64 png>", "marks": [{"mark_id":3,"name":"Save","bounds":[...]}]}
  ```
  This is **base64 over the existing `send_string` JSON channel** - no new binary transport. Throttle to
  a small constant frames-per-second (e.g. `_DESKTOP_FRAME_FPS = 2`, module-level constant per project
  convention) and downscale the image before encoding, to keep payload size reasonable.

## `charlie/web_server.py`

- `_event_bridge` (~line 166) already forwards unknown event types via `broadcast(event)` - add
  `desktop_frame` explicitly to `_SESSION_SCOPED_EVENTS` (~line 152) so frames are only delivered to
  WebSocket clients subscribed to the owning session (same pattern as `token`/`transcript`).

## Frontend

- New component `frontend/src/components/DesktopView.tsx` - renders the latest `desktop_frame` image
  plus a short scrolling log of the actions taken (reuse existing `tool_call`/`tool_result` events
  already flowing into `toolActivity`, per `CLAUDE.md` section 8.5, rather than inventing a new log format).
- `frontend/src/app/page.tsx` WS dispatch (~lines 231-344): add a handler for `desktop_frame` that
  updates a new store field (e.g. `latestDesktopFrame`).
- `frontend/src/store/useCharlieStore.ts`: add the `latestDesktopFrame` field, one primitive/object per
  the existing Zustand selector convention (CLAUDE.md 8.5 - one field per `useCharlieStore((s) => s.field)` call).
- Add a new tab to `InsightRail.tsx` (alongside the existing Swarm/Memory/MCP/Tasks tabs) hosting `DesktopView`.
- The existing dashboard **Stop** button already sends the `stop` command wired to `cancel_chat()` -
  confirm it also triggers `charlie.desktop.actions.halt()` (already true if Phase 1's panic-hotkey
  handler and the barge-in path both call `halt()` - just verify the Stop command path does too).

## Verification

1. `uv run ruff check .` / `uv run pytest -v` pass. `npx tsc --noEmit` and `npm test` pass in `frontend/`.
2. Start a desktop-control task with the dashboard open. Confirm frames appear in the new tab
   near-real-time, at the throttled rate, without flooding the WebSocket.
3. Click dashboard **Stop** mid-task; confirm the desktop action halts within one step, same as the panic hotkey.
4. Confirm frames are session-scoped: opening a second session's dashboard tab does not show the first
   session's desktop frames.

## Friction / risks

- Base64 frames are large relative to Charlie's other JSON events - throttle (fps) and downscale
  (max width/height as a named constant) are both required, not optional, to avoid saturating the
  WebSocket relative to `token`/`transcript` traffic.
- No binary channel exists today; if frame volume ever becomes a real bottleneck, a dedicated binary
  WebSocket path would be the next step - out of scope here.

## Explicitly out of scope for this phase

The MARVEL operator persona (Phase 4), the plugin/skill system (Phase 5).
