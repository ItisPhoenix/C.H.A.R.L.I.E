# Changelog

All notable changes to Charlie (the voice-first AI assistant) are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]
### Live Chat & Voice UI Sync (2026-07-09)
- **Critical - Token Duplication:** `main.py` emitted BOTH a per-chunk `token` event AND a sentence-boundary `token` event for every reply, doubling the assistant text in the chat. Now emits a single sentence-boundary stream.
- **Critical - Thinking Leak:** Raw model chunks (which can include `<thinking>` blocks) were sent to the chat UI. Added `_strip_think()` so reasoning never reaches the transcript.
- **High - Static Chat:** `updateLastMessageContent` replaced the assistant bubble instead of appending, so tokens never accumulated live. Now appends streamed tokens into a single growing bubble.
- **High - STT in Chat:** The backend streams recognized speech as `transcript` events, but the UI ignored them. Added a `transcript` handler that surfaces spoken input as a user bubble.
- **High - Fresh Session per Launch:** The dashboard reused the first existing session on load. Now creates a new session on every start so conversations don't inherit prior history.
- **Medium - Audio Dock Duplicate State:** `VoiceDock` rendered the global voice `state` twice (centered label + "Voice Link" block). The mic block now shows mic status (offline/muted/live) instead of duplicating the state word.
- **Backend Binding:** Confirmed the dashboard and Brain/LLM are one system - chat requires full mode (`run.py`), since the WebSocket `chat` command is forwarded to `main.py` via the event bus. Web-only mode serves the UI but cannot answer.

### Frontend Codebase Audit & Fix (2026-07-05)
- **Critical - Blackboard Sync:** Fixed WebSocket handler checking `msg.type === "blackboard"` instead of `"blackboard_update"`, which caused the Swarm HUD to never update.
- **Critical - Fallback Chat Endpoint:** Added missing `/api/sessions/{id}/chat` endpoint to `web_server.py`, so chat messages no longer 404 when WebSocket is down.
- **Stale Cleanup:** Removed unused `HelpCircle` import from VoiceDock, deleted dead `sendActivity()` function, removed stale `useRef`/`useEffect` from WaveformBar, removed unused `handleSearch`/`setSearchQuery` from SmartPanel.
- **Slop Cleanup:** Removed unused `MessageSquare`, `CheckCircle`, `XCircle` imports from Sidebar, removed redundant `handleSendMessage` variable and inline `apiCall` helper.
- **Duplicate Removal:** Removed redundant `useEffect` for focus management in MainWorkspace.
- **ErrorBoundary:** Added production-ready React ErrorBoundary with fallback UI and dev-mode error details.
- **Test Coverage:** Added comprehensive Zustand store unit tests (23 tests covering all CRUD operations).
- **Docs Update:** Replaced stale Tauri 2.0 references with Next.js 16 + React 19. Updated project structure. Added frontend dev commands.
### Agentic OS Foundation (2026-07-05)
- **Config Toggles:** Added `blackboard_enabled`, `swarm_enabled`, `reflection_enabled`, `mcp_enabled`, `plugins_enabled` to `Config` dataclass with env var backing.
- **Module Boundaries:** Expanded AGENTS.md module table to include `blackboard.py`, `swarm.py`, `agents/`, `memory_v2.py`, `memory_graph.py`, `reflection.py`, `mcp_client.py`, `plugins.py`.
- **Environment:** Added Agentic OS toggle section to `.env.example` with all 5 new env vars.
- **Version bump:** `1.1.0` -> `2.0.0-alpha.1` for Agentic OS milestone.
### Stabilization Pass (2026-06-27)
- **CI/CD:** Added GitHub Actions workflow with ruff lint + pytest.
- **Fast-path extraction:** Extracted `_normalize_app_list` and related constants to `charlie/text_utils.py` for testability.
- **Fast-path tests:** Added `tests/test_fastpaths.py` covering app list normalization, URL detection, and known-app matching.
- **SSE refactor:** Extracted shared SSE stream parser into `charlie/streaming.py`, deduplicated 5 inline parsing loops and 2 payload builders.
- **Async consolidation:** Fixed `_consolidate_memory` to use proper `await` instead of `run_until_complete` (avoids RuntimeError in running event loop).
- **SessionStore singleton:** Replaced 6 per-request `SessionStore()` instantiations in `web_server.py` with a module-level singleton.
- **Import cleanup:** Replaced `__import__('datetime')` with proper `from datetime import datetime`.
- **Runtime artifacts:** Untracked `charlie_memory_db/` files that were committed before `.gitignore` rules.
- **Loopback binding:** Changed web server bind from `0.0.0.0` to `127.0.0.1` (local-only).
- **File write guard:** Added `Path.resolve()` prefix check in `file_write` tool to prevent path traversal.
- **README corrections:** Fixed force-flush (100->200 chars) and max tool rounds (4->12) to match code.


### Web UI Glassmorphism Redesign & Active Session Sync (Phase 1 & 2)
- **Glassmorphism Theme System:** Established an Apple Intelligence-inspired visual system using frosted glass panels, ambient glows, and smooth transitions (Vite + Tailwind CSS + Framer Motion).
- **Three-Column Dashboard Layout:** Integrated a responsive tablet+ layout containing an collapsible sidebar (collapses to an icon rail on tablet), main Chat Panel, and a collapsible Smart Panel.
- **Smart Activity Panel:** Created a live activity feed displaying real-time intermediate thinking updates (`thinking_update`), active tool call cards with status spinners, and collapsed historical steps.
- **Animate-on-State Voice Dock:** Built a persistent bottom dock with an SVG/CSS waveform that animates dynamically based on Charlie's state (listening, thinking, speaking, or idle).
- **Active Session Synchronization:** Added a `"session_active"` WebSocket broadcaster that syncs the selected frontend chat session with `main.py`, guaranteeing that microphone voice inputs route directly to the active browser chat.
- **Cross-Browser SQLite Datetime Support:** Integrated a `parseDate` helper that normalizes UTC space-separated strings from SQLite into standard ISO-8601 format, eliminating WebKit/Safari `Invalid Date` rendering errors.
- **Upgraded Multi-App & Website Fast-Paths:** Enhanced the open/close helpers in `core.py` to support scanning multiple targets (e.g., *"open chrome, youtube, and reddit.com"*) and launching/terminating them in a loop without LLM prefill latency. Automatically resolves TLD domain names and whitelisted popular websites.

### Current State
Charlie is a headless, voice-first local AI assistant running on Windows 11.
Pipeline: **VAD -> Whisper ASR -> LLM (streaming) -> Kokoro TTS -> playback**.
Primary LLM via async httpx (OpenAI-compatible API) with automatic fallback to secondary provider.
### App & Website Opening
- SOUL.md instructs the LLM to use `shell_execute` with Windows `start` command for opening apps and websites.
- "Open YouTube" maps to `shell_execute("start https://youtube.com")`.
- "Open Calculator" maps to `shell_execute("start calc")`.

### Semantic Memory (`memory` tool)
- New `memory` tool for persistent memory management (MEMORY.md and USER.md).
- Actions: `add` (append), `replace` (swap substring), `remove` (delete substring).
- Enforces char limits: MEMORY.md max 2200 chars, USER.md max 1375 chars.
- System prompt explicitly tells the LLM to use memory for "remember X" and "what do you know about me" queries.

### Episodic Memory (`session_search` tool)
- New `session_search` tool queries SQLite FTS5 index for past conversation turns.
- Returns top 5 matching messages formatted as `[role]: content`.
- Falls back to SQL LIKE if FTS5 unavailable.

### Provider Fallback
- Automatic failover to secondary LLM provider on connection/timeout/rate-limit errors.
- Configurable via `FALLBACK_LLM_URL`, `FALLBACK_LLM_API_KEY`, `FALLBACK_LLM_MODEL` env vars.
- Both initial and tool-followup requests have fallback.
- No behavior change when fallback vars are unset.

### Text-Based Tool Parser Fix
- `_extract_tool_calls` now parses multi-argument tool calls from text format.
- `memory("add", "user", "text")` correctly maps positional args to named parameters.
- Added `memory` and `session_search` to `_TOOL_PARAM_NAMES` map.

### Tool Loop Hardening
- **Silent content kill fix**: If no tool calls detected after stream ends, yield accumulated content immediately instead of returning nothing.
- **Follow-up TOOL: leak fix**: Gated follow-up stream yields to prevent raw `TOOL:` text leaking into TTS.
- **Tool result format**: Use `{"role": "tool", "content": ...}` instead of `{"role": "assistant", ...}` for tool results -- local models expect this format.
- **Tool result confirmation**: Explicit success message after `shell_execute` ("executed successfully, now running") prevents model from denying capability.
- **Multi-app text normalization**: `_normalize_app_list()` in `main.py` inserts "and" between app names in commands like "Open Chrome calculator notepad" before LLM call. Zero-cost, model-agnostic.
- **Duplicate tool call guard**: `_seen_tool_calls` dict caches by `name(sorted_args_json)`, reuses result on duplicate.
- **Tool timeout**: `asyncio.wait_for(run_in_executor(...), timeout=15.0)` prevents infinite hangs.
- **Generation counter for cancel**: Monotonically increasing `_chat_generation` counter avoids race conditions between old/new `chat_stream` calls.

### System Prompt
- Dynamic date/time injection: `Current date: ... Current time: ...` refreshed each turn.
- Explicit prohibition: `NEVER use tools for: time, date, calculations, math, or general knowledge.`
- `At MOST ONCE per question` tool usage rule.
- Post-tool-result: `answer immediately using those results` + `Do NOT call tools if you already have the answer`.

### SearXNG Integration
- Self-hosted SearXNG as Tier 1 search provider (no API key needed).
- Auto-detect news/time-sensitive queries for `time_range=day` and `categories=news`.
- Fallback order: SearXNG -> Exa -> Tavily -> DuckDuckGo.
- Query cleaning: strip conversational fluff before search.

### Windows CMD Fix
- Regex-based prefix matching for CMD built-in commands (date, time) that hang when run via `subprocess.run(shell=True)`.
- Translates to PowerShell equivalents automatically.

### Barge-in
- Command words (stop, wait, cancel) bypass echo filter for instant interruption.
- New content during TTS triggers `brain.cancel_chat()` + `voice.stop_tts()`.
- Post-barge-in cooldown prevents double-trigger.

### Code Quality
- All magic numbers extracted to named module-level constants (`_LLM_TEMPERATURE`, `_MAX_TOOL_ROUNDS`, `_TOOL_TIMEOUT_SEC`, `_TOOL_RESULT_MAX_CHARS`, `SEARCH_RESULT_LIMIT`, etc.).
- `import httpx` moved to module top level in tools.py.
- Chinese regex dead code removed from `strip_internal_reasoning`.
- All non-ASCII characters (em dashes, arrows, box drawing) replaced with ASCII equivalents.
- Exception handling hardened: no bare `except: pass`, all errors logged with `exc_info=True`.
- `asyncio.get_running_loop()` used instead of deprecated `get_event_loop()`.

### Voice Pipeline
- VAD pre-roll buffer (~0.8s) prepended at VAD onset to prevent clipping first words.
- Speculative ASR streams consume internal network streams to completion.
- Command word echo bypass for barge-in responsiveness.
- Text humanization: ellipsis handling, repeated punctuation normalization, dash-to-comma conversion, wrapper quote removal, list marker cleanup.
- Streaming TTS flush: sentence/clause/max-char boundaries (200 chars).

### Memory & History
- `MEMORY.md`: System context, user preferences, session facts.
- `SOUL.md`: Personality rules, voice commands, platform constraints, capabilities.
- `USER.md`: Concise user profile with preferences.
- Session store: SQLite with FTS5 search (falls back to SQL LIKE).

### Testing
- 63/63 pytest passing.
- ruff lint clean.
- AST parse clean across all Python files.

---

## [cfecc81] - 2026-06-22

### Added
- Personality system with emotion classification (keyword-based, zero-latency).
- Tool calling framework: `web_search`, `shell_execute`, `file_read`, `file_write`.
- Learning loop (deferred to background via `asyncio.create_task`).
- `SOUL.md` for persistent personality configuration.

### Changed
- Simplified to voice-first assistant (removed widget/buddy/dashboard/MCP/proactive systems).

---

## [793b5b7] - 2026-06-21

### Changed
- Complete rewrite: stripped UI, MCP, memory persistence, screen context, multi-backend fallback.
- Final product: headless voice-first loop (capture -> VAD -> Whisper STT -> LLM -> Kokoro TTS -> playback).
- Generic filenames for expansion (core.py, voice.py, etc.).

---

## [132fd32] - 2026-06-20

### Added
- Desktop buddy character mode (Qt/QPainter glass-orb).
- Voice latency optimization.

---

## [e4abe87] - 2026-06-19

### Fixed
- Thought tag leak into TTS output.
- Self-hearing (ASR picking up own TTS output).
- Dead code cleanup.
- Renamed cloud/local backends to fast/main.

---

## [c2ee8eb] - 2026-06-18

### Fixed
- Frequent self-interruptions.
- STT stability improvements.

---

## [6ee76fc] - 2026-06-15

### Added
- Hands-free wake word detection.
- Hybrid LLM routing.
- MCP tool agency.
