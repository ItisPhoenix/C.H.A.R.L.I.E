# Changelog

All notable changes to Charlie (the voice-first AI assistant) are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Current State
Charlie is a headless, voice-first local AI assistant running on Windows 11.
Pipeline: **VAD -> Whisper ASR -> LLM (streaming) -> Kokoro TTS -> playback**.
Single explicit LLM backend via async httpx (OpenAI-compatible API).

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
- Streaming TTS flush: sentence/clause/max-char boundaries (100 chars).

### Memory & History
- `MEMORY.md`: System context, user preferences, session facts.
- `SOUL.md`: Personality rules, voice commands, platform constraints, capabilities.
- `USER.md`: Concise user profile with preferences.
- Session store: SQLite with FTS5 search (falls back to SQL LIKE).

### Testing
- 32/32 pytest passing.
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
