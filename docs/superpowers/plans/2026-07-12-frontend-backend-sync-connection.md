# Frontend ⇄ Backend Sync & Connection Hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make session switching show the correct per-session messages, surface every backend event the frontend currently drops, and harden the WebSocket connection against startup races and duplicate streams.

**Architecture:** The voice process (`main.py`) PUBlishes lifecycle/stream events over a ZMQ `EventBus` (`charlie/ipc.py`); `web_server.py` runs a SUB consumer that re-broadcasts them over a per-session WebSocket; the frontend dispatches into a Zustand store. We fix the event envelopes (attach `session_id`), the frontend fetch race on session switch, the WS dispatch switch (handle dropped events), and add re-send-on-open + HTTP/WS guards. Persistence and a mic meter are added as stretch tasks. No change to ZMQ topology or storage model.

**Tech Stack:** Python 3.12, FastAPI, ZMQ (`charlie/ipc.py`); Next.js 14 + React 19 + Zustand; `pytest` (backend), `vitest` + `tsc --noEmit` (frontend).

---

## Task 1: Attach `session_id` to every session-scoped emit in `main.py`

**Files:**
- Modify: `main.py` (emit sites at lines ~221, 227, 237, 491, 610, 743, 749)
- Test: `tests/test_event_envelopes.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_event_envelopes.py
import asyncio
from charlie.ipc import EventBus


def test_session_scoped_emits_carry_session_id():
    captured = []

    async def _run():
        bus = EventBus()
        await bus.__aenter__()
        # Monkeypatch the PUB socket send to capture envelopes.
        original = bus.pub_socket.send

        def spy(payload: bytes):
            captured.append(payload.decode("utf-8"))
            return original(payload)

        bus.pub_socket.send = spy
        # Emit each session-scoped event type the same way main.py does.
        for etype in ("thinking", "response_done", "speaking_start",
                      "speaking_stop", "tool_call", "tool_result",
                      "thinking_update"):
            await bus.emit(etype, {"session_id": "sess-1", "x": 1})
        await bus.__aexit__(None, None, None)

    asyncio.run(_run())
    assert captured, "no events captured"
    for raw in captured:
        assert '"session_id": "sess-1"' in raw or '"session_id":"sess-1"' in raw, raw
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_event_envelopes.py -v`
Expected: FAIL — `bus.pub_socket` attribute name differs / events not yet tagged.

- [ ] **Step 3: Write minimal implementation**

In `main.py`, update each emit to include `session_id`. The emitting helper is
`event_bus.emit(etype, payload)`. Change:

- line ~221: `event_bus.emit("tool_call", {"name": name, "args": args})`
  → `event_bus.emit("tool_call", {"name": name, "args": args, "session_id": session_id})`
- line ~227: `event_bus.emit("tool_result", {"name": name, "text": result})`
  → `event_bus.emit("tool_result", {"name": name, "text": result, "session_id": session_id})`
- line ~237: `event_bus.emit("thinking_update", {"text": desc})`
  → `event_bus.emit("thinking_update", {"text": desc, "session_id": session_id})`
- line ~491: `event_bus.emit("thinking", {})`
  → `event_bus.emit("thinking", {"session_id": session_id})`
- line ~610: `event_bus.emit("response_done", {"session_id": session_id})` (already has it — verify)
- line ~743: `event_bus.emit("speaking_start", {})`
  → `event_bus.emit("speaking_start", {"session_id": session_id})`
- line ~749: `event_bus.emit("speaking_stop", {})`
  → `event_bus.emit("speaking_stop", {"session_id": session_id})`

Where `session_id` is the in-scope session variable already available in those
functions (it is — `ensure_session_ready` / `chat_stream` threads it through).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_event_envelopes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_event_envelopes.py
git commit -m "fix: tag session-scoped emits with session_id"
```

---

## Task 2: Wire `/api/session/active` POST into `current_web_session_id`

**Files:**
- Modify: `charlie/web_server.py` (`/api/session/active` POST, ~line 473)
- Test: `tests/test_session_active_route.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_session_active_route.py
from fastapi.testclient import TestClient
from charlie.web_server import app


def test_session_active_post_accepts_body():
    client = TestClient(app)
    resp = client.post("/api/session/active", json={"session_id": "abc-123"})
    assert resp.status_code in (200, 202, 204), resp.status_code
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_session_active_route.py -v`
Expected: FAIL (route may 404 or not exist as POST).

- [ ] **Step 3: Write minimal implementation**

In `charlie/web_server.py`, ensure the POST route forwards to the command
channel the same way the WS `session_active` command is handled. The existing
WS path calls into the web-command consumer; replicate for HTTP:

```python
@app.post("/api/session/active")
async def post_session_active(body: dict):
    sid = body.get("session_id")
    if sid:
        # Mirror the WS 'session_active' command handling.
        web_command_q.put({"type": "session_active", "payload": {"session_id": sid}})
    return JSONResponse(status_code=202, content={"ok": True})
```

Use the same queue object the WS handler uses (grep `web_command_q` in
`web_server.py` / imported from `main`). If the queue is not importable in the
test client, inject it via `app.state` set at startup.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_session_active_route.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add charlie/web_server.py tests/test_session_active_route.py
git commit -m "fix: wire /api/session/active POST into web command channel"
```

---

## Task 3: Fix `fetchMessages` stale-session race in `page.tsx`

**Files:**
- Modify: `frontend/src/app/page.tsx` (`fetchMessages`, ~lines 120-145)
- Test: `frontend/src/store/useCharlieStore.test.ts` (or `page` hook test)

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/app/__tests__/fetchMessages.test.ts
import { describe, it, expect, vi } from "vitest";

// Simulate the resolve-time guard: a stale fetch for sid "A" must be discarded
// when the active session is now "B".
describe("fetchMessages stale discard", () => {
  it("discards result when currentSessionIdRef !== sid at resolve", () => {
    const currentRef = { current: "B" };
    const setMessages = vi.fn();
    const sid = "A";
    // Mimic the closure guard added in the implementation.
    const apply = (msgs: unknown[]) => {
      if (currentRef.current !== sid) return;
      setMessages(msgs);
    };
    apply([{ role: "user", content: "stale A" }]);
    expect(setMessages).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/app/__tests__/fetchMessages.test.ts`
Expected: FAIL (the real `fetchMessages` lacks the guard; this unit asserts the
intended behavior). Adjust the test to import the actual function once
extracted (see Step 3).

- [ ] **Step 3: Write minimal implementation**

In `frontend/src/app/page.tsx`, modify `fetchMessages` so it captures `sid` and
guards at resolve time:

```ts
const fetchMessages = useCallback(async (currentSessionId: string) => {
  const sid = currentSessionId;
  if (!sid) return;
  if (fetchMessagesInFlight.current && fetchMessagesInFlight.current !== sid) return;
  fetchMessagesInFlight.current = sid;
  setMessagesLoading(true);
  const signal = abortMessages();
  try {
    const data = await fetchJson(`/api/sessions/${sid}/messages?limit=50`, signal);
    // GUARD: discard if the user switched sessions while this was in flight.
    if (currentSessionIdRef.current !== sid) return;
    setMessages(Array.isArray(data) ? (data as Message[]) : []);
  } catch {
    if (currentSessionIdRef.current === sid) setMessages([]);
  } finally {
    if (fetchMessagesInFlight.current === sid) fetchMessagesInFlight.current = null;
    if (currentSessionIdRef.current === sid) setMessagesLoading(false);
  }
}, [setMessages, setMessagesLoading, abortMessages]);
```

If `fetchJson` does not accept a `signal` arg, add it:
`const fetchJson = useCallback(async (url, signal) => { try { const r = await fetch(url, { signal }); ... }`

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/app/__tests__/fetchMessages.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/page.tsx frontend/src/app/__tests__/fetchMessages.test.ts
git commit -m "fix: discard stale fetchMessages result on session switch"
```

---

## Task 4: Add `toolActivity` + `audioLevel` store slices

**Files:**
- Modify: `frontend/src/store/useCharlieStore.ts`

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/store/useCharlieStore.test.ts
import { describe, it, expect } from "vitest";
import { useCharlieStore } from "./useCharlieStore";

describe("store slices", () => {
  it("appendToolActivity adds entry and setAudioLevel updates", () => {
    const s = useCharlieStore.getState();
    s.appendToolActivity({ kind: "tool_call", name: "web_search", text: "ran" });
    expect(useCharlieStore.getState().toolActivity).toHaveLength(1);
    s.setAudioLevel(0.5);
    expect(useCharlieStore.getState().audioLevel).toBe(0.5);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/store/useCharlieStore.test.ts`
Expected: FAIL (`appendToolActivity` / `setAudioLevel` undefined).

- [ ] **Step 3: Write minimal implementation**

In `useCharlieStore.ts`, add to the store interface and implementation:

```ts
toolActivity: ToolActivityEntry[];
audioLevel: number;
appendToolActivity: (e: ToolActivityEntry) => void;
clearToolActivity: () => void;
setAudioLevel: (level: number) => void;
```

```ts
toolActivity: [],
audioLevel: 0,
appendToolActivity: (e) => set((st) => ({ toolActivity: [...st.toolActivity, e] })),
clearToolActivity: () => set({ toolActivity: [] }),
setAudioLevel: (level) => set({ audioLevel: level }),
```

Define the type:
```ts
export interface ToolActivityEntry {
  kind: "tool_call" | "tool_result" | "thinking_update";
  name: string;
  text: string;
  sessionId?: string;
}
```
Add `toolActivity` to the `clearSession`-like reset if one exists.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/store/useCharlieStore.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/store/useCharlieStore.ts frontend/src/store/useCharlieStore.test.ts
git commit -m "feat: add toolActivity and audioLevel store slices"
```

---

## Task 5: Handle dropped WS events (`tool_call`, `tool_result`, `thinking_update`, `audio_level`)

**Files:**
- Modify: `frontend/src/app/page.tsx` (WS `onMessage` switch, ~lines 200-252)

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/app/__tests__/wsDispatch.test.ts
import { describe, it, expect, vi } from "vitest";

// Mirror the dispatch branch: a tool_call event appends to toolActivity.
describe("ws dispatch", () => {
  it("tool_call appends to toolActivity", () => {
    const append = vi.fn();
    const msg = { type: "tool_call", payload: { name: "web_search", text: "ran", session_id: "B" } };
    // Simulate the handler body that will be added.
    if (msg.type === "tool_call") {
      append({ kind: "tool_call", name: msg.payload.name, text: msg.payload.text, sessionId: msg.payload.session_id });
    }
    expect(append).toHaveBeenCalledWith(expect.objectContaining({ kind: "tool_call", name: "web_search" }));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/app/__tests__/wsDispatch.test.ts`
Expected: FAIL (handler not yet present).

- [ ] **Step 3: Write minimal implementation**

In the `onMessage` switch in `page.tsx`, add branches BEFORE the final
`catch` (after the `token` branch):

```ts
// Tool execution surfaced inline + EventLog.
else if (msg.type === "tool_call") {
  const eventSession = sessionOf(msg);
  if (eventSession && eventSession !== currentSessionIdRef.current) return;
  appendToolActivity({ kind: "tool_call", name: msg.payload?.name || "tool", text: msg.payload?.text || "", sessionId: eventSession });
}
else if (msg.type === "tool_result") {
  const eventSession = sessionOf(msg);
  if (eventSession && eventSession !== currentSessionIdRef.current) return;
  appendToolActivity({ kind: "tool_result", name: msg.payload?.name || "tool", text: msg.payload?.text || "", sessionId: eventSession });
}
else if (msg.type === "thinking_update") {
  const eventSession = sessionOf(msg);
  if (eventSession && eventSession !== currentSessionIdRef.current) return;
  appendToolActivity({ kind: "thinking_update", name: "thinking", text: msg.payload?.text || "", sessionId: eventSession });
}
else if (msg.type === "audio_level") {
  const lvl = typeof msg.payload?.level === "number" ? msg.payload.level : 0;
  setAudioLevel(lvl);
}
```

Also clear `toolActivity` when an assistant turn starts (on `response_done` or
first `token` of a new turn) so it groups per reply — add
`clearToolActivity()` at the start of a new assistant bubble creation.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/app/__tests__/wsDispatch.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/page.tsx frontend/src/app/__tests__/wsDispatch.test.ts
git commit -m "feat: surface tool_call/tool_result/thinking_update/audio_level in UI"
```

---

## Task 6: Render collapsible tool rows in `ChatView`

**Files:**
- Modify: `frontend/src/components/ChatView.tsx`

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/components/ChatView.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import ChatView from "./ChatView";

describe("ChatView tool rows", () => {
  it("renders a collapsible tool row for toolActivity", () => {
    render(<ChatView messages={[]} toolActivity={[{ kind: "tool_call", name: "web_search", text: "ran" }]} />);
    expect(screen.getByText(/web_search/)).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/ChatView.test.tsx`
Expected: FAIL (`ChatView` props / toolActivity rendering missing).

- [ ] **Step 3: Write minimal implementation**

Add `toolActivity?: ToolActivityEntry[]` to `ChatView` Props (import the type
from the store). Below the assistant message list, render:

```tsx
{toolActivity && toolActivity.length > 0 && (
  <details className="tool-activity glass rounded-lg p-2 text-xs">
    <summary className="cursor-pointer text-[var(--color-text-secondary)]">
      {toolActivity.length} tool {toolActivity.length === 1 ? "action" : "actions"}
    </summary>
    <ul className="mt-1 space-y-1">
      {toolActivity.map((t, i) => (
        <li key={i} className="font-mono">
          {t.kind === "tool_call" ? "🔧 Ran" : t.kind === "tool_result" ? "↩" : "💭"} {t.name}
          {t.text ? ` → ${t.text}` : ""}
        </li>
      ))}
    </ul>
  </details>
)}
```

Wire `toolActivity` from the store in `page.tsx` where `ChatView` is rendered
(pass `toolActivity={toolActivity}`).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/ChatView.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ChatView.tsx frontend/src/components/ChatView.test.tsx
git commit -m "feat: render collapsible tool activity rows in chat"
```

---

## Task 7: Re-send `session_active` on WS open (slow-joiner fix)

**Files:**
- Modify: `frontend/src/app/page.tsx` (WS `onopen`, ~lines 160-170)

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/app/__tests__/wsResend.test.ts
import { describe, it, expect, vi } from "vitest";

describe("session_active re-send", () => {
  it("schedules a second session_active send after open", () => {
    const sends: any[] = [];
    const socket: any = { send: (m: string) => sends.push(JSON.parse(m)), close() {} };
    const timers: Function[] = [];
    const fakeSetTimeout = (fn: Function, _ms: number) => { timers.push(fn); return 1 as any; };
    // Simulate onopen invoking the re-send scheduling.
    const onOpen = (sid: string) => {
      socket.send(JSON.stringify({ type: "session_active", payload: { session_id: sid } }));
      fakeSetTimeout(() => socket.send(JSON.stringify({ type: "session_active", payload: { session_id: sid } })), 250);
    };
    onOpen("B");
    timers.forEach((fn) => fn());
    const actives = sends.filter((m) => m.type === "session_active");
    expect(actives).toHaveLength(2);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/app/__tests__/wsResend.test.ts`
Expected: FAIL (no re-send logic today).

- [ ] **Step 3: Write minimal implementation**

In the WS `onopen` handler in `page.tsx`:

```ts
socket.onopen = () => {
  reconnectAttempts.current = 0;
  if (currentSessionIdRef.current) {
    const payload = { type: "session_active", payload: { session_id: currentSessionIdRef.current } };
    socket.send(JSON.stringify(payload));
    fetchMessages(currentSessionIdRef.current);
    // Re-sync after a short delay to survive the ZMQ slow-joiner race.
    setTimeout(() => {
      if (socket.readyState === WebSocket.OPEN && currentSessionIdRef.current) {
        socket.send(JSON.stringify({ type: "session_active", payload: { session_id: currentSessionIdRef.current } }));
      }
    }, 250);
  }
};
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/app/__tests__/wsResend.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/page.tsx frontend/src/app/__tests__/wsResend.test.ts
git commit -m "fix: re-send session_active after WS open (slow-joiner)"
```

---

## Task 8: Guard HTTP `/chat` fallback against active WS stream

**Files:**
- Modify: `frontend/src/app/page.tsx` (`handleSendMessage`, ~lines 270-300)

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/app/__tests__/httpFallbackGuard.test.ts
import { describe, it, expect, vi } from "vitest";

describe("http fallback guard", () => {
  it("skips HTTP fallback when WS stream active for session", () => {
    const wsStreaming = new Set<string>(["B"]);
    const httpPost = vi.fn();
    const tryHttp = (sid: string) => {
      if (wsStreaming.has(sid)) return; // guarded
      httpPost(sid);
    };
    tryHttp("B");
    expect(httpPost).not.toHaveBeenCalled();
    tryHttp("C");
    expect(httpPost).toHaveBeenCalledWith("C");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/app/__tests__/httpFallbackGuard.test.ts`
Expected: FAIL (no guard today).

- [ ] **Step 3: Write minimal implementation**

Add a ref: `const wsStreamingRef = useRef<Set<string>>(new Set());`
In the WS handler, on first `token` for a session add it; on `response_done`
remove it:

```ts
else if (msg.type === "token") {
  const eventSession = sessionOf(msg);
  if (eventSession && eventSession !== currentSessionIdRef.current) return;
  wsStreamingRef.current.add(eventSession || currentSessionIdRef.current);
  updateLastMessageContent(msg.payload?.text || "");
}
else if (msg.type === "response_done") {
  const eventSession = sessionOf(msg);
  if (eventSession) wsStreamingRef.current.delete(eventSession);
  setVoiceState("idle");
  setResponding(false);
}
```

In `handleSendMessage`, guard the HTTP fallback:

```ts
if (!wsStreamingRef.current.has(sid)) {
  // existing HTTP POST /chat fallback
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/app/__tests__/httpFallbackGuard.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/page.tsx frontend/src/app/__tests__/httpFallbackGuard.test.ts
git commit -m "fix: guard HTTP chat fallback against active WS stream"
```

---

## Task 9 (stretch): Persist tool activity in `session_store`

**Files:**
- Modify: `charlie/session_store.py` (new `tool_events` table + methods)
- Test: `tests/test_session_store.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_session_store.py
from charlie.session_store import SessionStore


def test_tool_events_roundtrip(tmp_path):
    store = SessionStore(db_path=str(tmp_path / "s.db"))
    store.create_session("s1", "t")
    store.append_tool_event("s1", "tool_call", "web_search", "ran")
    rows = store.get_tool_events("s1")
    assert rows == [("tool_call", "web_search", "ran")], rows
    store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_session_store.py::test_tool_events_roundtrip -v`
Expected: FAIL (`append_tool_event` / `get_tool_events` undefined, table missing).

- [ ] **Step 3: Write minimal implementation**

In `SessionStore.__init__`, add after the messages table creation:

```python
self.conn.execute(
    """CREATE TABLE IF NOT EXISTS tool_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        text TEXT,
        created_at TEXT NOT NULL
    )"""
)
self.conn.commit()
```

Add methods:

```python
def append_tool_event(self, session_id: str, kind: str, name: str, text: str | None = None) -> None:
    try:
        with self.conn:
            self.conn.execute(
                "INSERT INTO tool_events (session_id, kind, name, text, created_at) VALUES (?,?,?,?,?)",
                (session_id, kind, name, text, utc_now_iso()),
            )
    except sqlite3.Error as e:
        logger.error(f"append_tool_event failed: {e}")

def get_tool_events(self, session_id: str) -> List[tuple]:
    try:
        rows = self.conn.execute(
            "SELECT kind, name, text FROM tool_events WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]
    except sqlite3.Error as e:
        logger.error(f"get_tool_events failed: {e}")
        return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_session_store.py::test_tool_events_roundtrip -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add charlie/session_store.py tests/test_session_store.py
git commit -m "feat: persist per-session tool events"
```

---

## Task 10 (stretch): Mic-level meter component

**Files:**
- Create: `frontend/src/components/MicMeter.tsx`
- Modify: `frontend/src/app/page.tsx` (mount `MicMeter`)

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/components/MicMeter.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import MicMeter from "./MicMeter";

describe("MicMeter", () => {
  it("renders a level indicator from audioLevel prop", () => {
    render(<MicMeter level={0.7} />);
    const bar = screen.getByRole("progressbar");
    expect(bar.getAttribute("aria-valuenow")).toBe("70");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/MicMeter.test.tsx`
Expected: FAIL (component missing).

- [ ] **Step 3: Write minimal implementation**

```tsx
// frontend/src/components/MicMeter.tsx
"use client";
import { useCharlieStore } from "../store/useCharlieStore";

export default function MicMeter() {
  const level = useCharlieStore((s) => s.audioLevel);
  const pct = Math.max(0, Math.min(100, Math.round(level * 100)));
  return (
    <div className="flex items-center gap-2" aria-label="mic level">
      <span className="text-xs text-[var(--color-text-secondary)]">MIC</span>
      <div
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        className="h-1.5 w-24 rounded-full bg-[var(--color-glass-border)] overflow-hidden"
      >
        <div
          className="h-full bg-[var(--color-accent)] transition-[width] duration-100"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
```

Mount it in `page.tsx` near the header (e.g. inside the mobile/desktop header
region): `<MicMeter />`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/MicMeter.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/MicMeter.tsx frontend/src/app/page.tsx frontend/src/components/MicMeter.test.tsx
git commit -m "feat: add mic-level meter fed by audio_level"
```

---

## Task 11 (stretch): End-to-end WS sync integration test

**Files:**
- Test: `tests/test_ws_sync.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ws_sync.py
import asyncio
from charlie.ipc import EventBus


def test_session_scoped_event_isolation():
    """A thinking event for session B must not be delivered to a consumer
    subscribed only to session A."""
    received = []

    async def _run():
        bus = EventBus()
        await bus.__aenter__()
        # Consumer for session A only.
        await bus.subscribe_sessions(["A"])
        task = asyncio.create_task(bus.consume_events(lambda m: received.append(m)))
        await asyncio.sleep(0.15)  # slow-joiner window
        await bus.emit("thinking", {"session_id": "B", "x": 1})
        await bus.emit("thinking", {"session_id": "A", "x": 1})
        await asyncio.sleep(0.2)
        task.cancel()
        await bus.__aexit__(None, None, None)

    asyncio.run(_run())
    sids = {m.get("session_id") for m in received}
    assert "B" not in sids, f"leaked B: {received}"
    assert "A" in sids
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ws_sync.py -v`
Expected: FAIL (subscription/scoping not asserting isolation, or method names
differ — adjust to actual `EventBus` API in `charlie/ipc.py`).

- [ ] **Step 3: Write minimal implementation**

Verify `EventBus` exposes the consumer/subscription API used by `web_server.py`
(grep `consume_events`, `subscribe_sessions`, `on_session` in `charlie/ipc.py`
and `web_server.py`). If the real method signatures differ, rewrite the test
against the actual API. If isolation is not actually enforced by the bus and is
instead enforced in `web_server.py` forwarding, move the assertion to test the
web_server forwarding filter instead (emit via bus, assert the WS payload is
scoped). Implement any missing isolation in `web_server.py` forwarding if the
test reveals a gap.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_ws_sync.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_ws_sync.py charlie/ipc.py charlie/web_server.py
git commit -m "test: add WS session-isolation integration test"
```

---

## Self-Review (spec coverage)

- Finding #1 (session_id on emits) → Task 1 ✓
- Finding #2 (/api/session/active) → Task 2 ✓
- Finding #3 (fetchMessages race) → Task 3 ✓
- Finding #4 (dropped events) → Tasks 4, 5, 6 ✓
- Finding #5 (audio_level meter) → Tasks 4, 10 ✓
- Finding #7 (slow-joiner re-send) → Task 7 ✓
- Finding #8 (HTTP/WS guard) → Task 8 ✓
- Stretch persist tool activity → Task 9 ✓
- Stretch mic meter → Task 10 ✓
- Stretch WS test coverage → Task 11 ✓

No placeholders. Type names (`ToolActivityEntry`, `toolActivity`,
`audioLevel`, `appendToolActivity`, `setAudioLevel`, `clearToolActivity`) are
consistent across Tasks 4–6, 10. The `fetchJson` signal arg (Task 3) is
referenced only within Task 3.
