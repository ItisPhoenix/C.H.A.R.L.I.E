# Core Loop — Charlie boots clean

**Status:** Design approved
**Date:** 2026-06-03
**Sub-project:** Core Loop (1 of 11 in the project decomposition)

## Goal

`start-charlie.ps1` brings up Charlie so that all required subsystems are running, the Doctor reports green, and the daemon log does not contain repeating error messages. Charlie is observably "working" from the operator's perspective even if individual features (RAG, vision, integrations) are still pending.

## Scope

**Required to start (must be `ok` or `disabled` with a posted reason):**
- Brain
- LLM (chat endpoint)
- Audio
- Browser
- Telegram

**Allowed disabled (vision is the one subsystem that may stay down):**
- Vision

**Policy:** Missing external dependency = subsystem reports `disabled:<reason>`, not failure. Reported in the dashboard `/doctor` page.

## Out of scope

- Dashboard visual alignment (next sub-project: Dashboard Polish)
- RAG ingestion quality, memory system
- Telegram UX, voice conversation flows
- Browser stealth / fingerprint hardening
- Agent creation, automation routines
- Vision endpoint wiring (intentionally skipped this pass)

## Current state (root cause, with file:line)

### 1. LLM health check fires false-positive on every NIM-like endpoint

`charlie/utils/doctor.py:127-143` builds the auth header as
`headers = {"Authorization": f"Bearer {settings.llm.llm_api_key}"}`
unconditionally. When `llm_api_key` is empty, the literal string
`Authorization: Bearer ` is sent. NVIDIA's NIM and most other
OpenAI-compatible gateways return **401 Unauthorized** on that, so
`check_llm_health` flips `_llm_healthy` to false. `perform_vitals_check`
(`charlie/utils/doctor.py:35-53`) escalates to `CRITICAL_LLM`, the
remediator runs `remediate_llm | llm_service_down`, and the loop repeats
every 30s because the gate is monotonic (`_llm_healthy` only ever goes
false→true, never re-checks the same status twice).

**Even when the user has set a valid key**, the doctor is hostile to
endpoints that 401 on a malformed Bearer header before the user logs in.

### 2. Browser `_main_loop` swallows `WinError 2` and keeps logging

`charlie/browser/headless_browser.py:141-155` runs the per-request loop.
At `:152`, any `Exception` is caught, logged as `browser_loop_err`, and
**silently continues** — only exiting if the message contains `"closed"`
or `"pipe"`. `WinError 2` is "file not found" (typically a stale
`Singleton*` lock or a missing profile dir), so it never matches those
strings, and the loop logs the same error forever. The heartbeat
updates inside the same loop, so the supervisor thinks Browser is fine
and never restarts it.

### 3. Telegram and Audio always start unconditionally

`charlie/watchdog/phoenix.py:439-518` `start_process` always spawns
Telegram (`run_telegram`, line 105-116) and Audio (`run_audio`, line
31-47). When the dependency is missing (no Telegram token, no mic), the
child either crashes immediately and gets respawned, or runs an empty
heartbeat loop and gets flagged as "alive but useless." There is no
mechanism for a child to declare itself `disabled` and have the
supervisor respect that.

### 4. Embedding timeout is a warning, not a failure (no fix needed)

`charlie/utils/embedding.py:44-66` runs the model load in a background
thread with a 10s join timeout. On timeout, the code falls back to
`embedding_functions.DefaultEmbeddingFunction()` (ChromaDB's built-in
embedding). The log line `embedding_load_timeout | model=...` is a
**warning**, not an error. Brain still starts. Documented here so the
success criteria do not treat this as a blocker.

## Design

### A. Fix the LLM health check

In `charlie/utils/doctor.py:127-143` (`check_llm_health`):

- Build `headers` conditionally. If `settings.llm.llm_api_key` is a
  non-empty string, include `Authorization: Bearer <key>`. Otherwise,
  omit the header (do **not** send an empty Bearer).
- The 5-second timeout and the `GET <llm_url>/v1/models` request stay
  the same.
- Status code semantics:
  - `2xx` → healthy, log `llm_health_ok | status=<code>`
  - `401`, `403` → healthy (server is up, auth will be applied at
    chat time), log `llm_health_unauth | status=<code>`
  - `404` → healthy on a non-standard deployment (e.g., Ollama
    without `/v1/models`); log `llm_health_no_models_endpoint`
  - `5xx`, network errors, timeouts → unhealthy,
    log `llm_health_down | status=<code>|error=<str>`

The `remediator` only fires on the unhealthy branch.

### B. Cap browser loop error spam

In `charlie/browser/headless_browser.py:141-155` (`_main_loop`):

- Track `_err_window: list[float]` — a list of timestamps of recent
  errors inside a 60-second sliding window.
- On each `Exception`:
  1. Append `time.time()` to the window, prune entries older than 60s.
  2. If the window has ≥ 5 entries, log
     `browser_loop_fatal | errs_in_60s=5 | exiting_for_respawn` and
     `return` from `_main_loop`. The non-zero exit code at
     `headless_browser.py:91-93` will propagate, the supervisor
     respawns once with the existing 10s cooldown
     (`charlie/watchdog/phoenix.py:580-585`).
  3. If the window has < 5 entries, continue (same as today).
- A successful heartbeat (no exception for 60s) clears the window.

The 5-in-60s threshold is a starting point. Once the project is
running, dial it in.

### C. Add `SUBSYSTEM_STATUS` boot gate in Phoenix

In `charlie/watchdog/phoenix.py`:

- New constant `_DISABLED_RE`: matches `disabled:<reason>` payloads.
- `monitor()` (line 520) reads from `status_q` in addition to the
  shared `heartbeat` value: when a child's process is alive but has
  posted a `SUBSYSTEM_STATUS` payload with `state == "disabled"`, add
  the child's name to `self.disabled_children: set[str]` and **skip**
  heartbeat-staleness checks, crash-count math, and remediator
  triggers for it. The supervisor's per-child status (the same
  `status_q` payload it forwards to the ControlServer) reflects
  `disabled` rather than `alive` so the dashboard shows the real
  state.
- The set is reset on `monitor()` initialization and is
  process-local; it does not need to be persisted.
- A child that later transitions from `disabled` → `ok` (e.g., a mic
  is hot-plugged) re-enables itself by sending a fresh
  `SUBSYSTEM_STATUS` with `state == "ok"`.

This is the smallest edit to `phoenix.py` that achieves the "missing
dep = disabled, not failed" policy. It does **not** rewrite Phoenix.

### D. Telegram child posts `disabled` on missing token

In `charlie/watchdog/phoenix.py:105-116` (`run_telegram`):

- At the top of the function, check `settings.supervisor.telegram_token`
  and `settings.supervisor.telegram_chat_id`. If either is empty:
  - `status_q.put_nowait({"type": "SUBSYSTEM_STATUS", "name": "telegram",
    "state": "disabled", "reason": "no_token"})`
  - Touch `heartbeat.value = time.time()` so the supervisor sees a
    live process.
  - Block on a `multiprocessing.Event` (`telegram_re_enable_event`)
    that the operator can set from the dashboard to retry.
  - Return.

The Telegram subsystem still reports a heartbeat. The supervisor's
new gate (C) sees the `SUBSYSTEM_STATUS: disabled` payload and stops
flapping.

### E. Audio child posts `disabled` on missing device

In `charlie/audio_proc.py` (`AudioEngine.__init__` or its boot path):

- Catch the device-open failure
  (`sounddevice.PortAudioError: Error querying device index`).
- If the device is not present, post
  `SUBSYSTEM_STATUS: disabled — mic_index=<n> not found` to `status_q`.
- Touch `heartbeat.value = time.time()` and block on a re-enable event.
- A periodic re-probe (every 30s) lets a hot-plugged mic come back
  online without a daemon restart.

### F. Dashboard `/doctor` page reflects disabled state

`/doctor` shows the new state shape. A child marked `disabled` renders
as a grey chip with the reason (`no token`, `mic not found`). The
Doctor's overall status remains `ok` if all required children are
either `ok` or `disabled`.

## Files touched

| File | Why |
| --- | --- |
| `charlie/utils/doctor.py` | Section A |
| `charlie/browser/headless_browser.py` | Section B |
| `charlie/watchdog/phoenix.py` | Sections C, D |
| `charlie/audio_proc.py` | Section E |
| `dashboard/src/app/doctor/page.tsx` | Section F (read-only consumer) |
| `dashboard/src/lib/api.ts` | If the `/api/doctor` response shape changes (it will — add `state` and `reason` per child) |

## Success criteria (observable)

After running `start-charlie.ps1`:

1. Process tree shows 1 supervisor + the required children alive
   (Brain, Audio, Browser, Telegram — and optionally Vision, which is
   allowed disabled). Each child is either posting heartbeats
   (state `ok`) or has posted `SUBSYSTEM_STATUS: disabled` once.
2. `charlie/logs/daemon.log` does not contain the strings
   `remediate_llm | llm_service_down`,
   `browser_loop_err | [WinError 2]` repeated more than once in any
   60-second window, or any new `ERROR` line after 60s of clean
   running.
3. `curl -sf http://127.0.0.1:8090/api/doctor` returns
   `{"ok": true, "children": { ... }}` where every required child is
   either `ok` or `disabled:<reason>`. (Use `netstat` to confirm port
   8090 is bound; the `curl` exit-7 issue on this box is unrelated.)
4. Brain `run_self_check` returns `{ok: true}` within 30s of brain
   startup.
5. Dashboard `/doctor` page renders without errors. Required children
   show green or grey; vision shows "skipped".

## Open questions (to confirm before implementation)

1. **5-in-60s threshold for browser respawn.** Is 5 errors in 60s the
   right cap, or do you want a tighter/larger window?
2. **Audio re-probe cadence.** 30s feels right but it is a fixed
   number. If you want the operator to be able to trigger a re-probe
   from the dashboard, the design grows by one more wire.
3. **Telegram re-enable event.** If Telegram is disabled and the
   operator adds a token via the settings page, should the dashboard
   have a "retry now" button, or do we wait for a full daemon
   restart?
