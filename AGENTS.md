# Agent Instructions for Charlie

This document governs all AI agents (Claude Code, Cursor, Copilot, etc.) working on Charlie.
Follow these rules in order. No exceptions.

---

## 1. Workflow (Non-Negotiable)

Before any non-trivial change (>1 line or touching a new module):

1. **Activate relevant skills** via `skill://` (e.g., `python-pro`, `clean-code`, `voice-latency-checklist`). If there is a 1% chance a skill applies, use it.
2. **Ask the user** via the `ask` tool to confirm your approach. Do not proceed without confirmation.
3. **Do web research** via `web_search` if uncertain about any API, library, or best practice. Never guess.
4. **Read the relevant code files first.** Understand the existing patterns before writing anything.
5. **Then** write the plan or edit code.

**Trivial exception**: A single-line typo fix or pure formatting change may skip the above.
State "Applying trivial fix." and proceed.

---

## 2. Code Standards

### Simplicity
- Write simple, direct code. No unnecessary abstractions.
- If you can solve it in 10 lines, do not write 50.
- Prefer boring solutions that work over clever ones that might break.
- No "while you're at it" changes -- only touch what was asked.

### Type Hints
- All public function signatures must have type hints.
- Use `Optional[...]` and `List[...]`; no bare `list` or `dict` in signatures.

### Error Handling
- Log all exceptions. Never use bare `except: pass`.
- Background tasks: wrap in `try/except` with `logger.warning(...)` or `logger.error(..., exc_info=True)`.
- Expected failures (queue timeout, VAD silence): use `logger.debug` or `except queue.Empty: continue`.
- Unexpected failures: always `exc_info=True` for tracebacks.

### Constants
- Extract magic numbers to named module-level constants.
- Module-level: implementation details (`_MAX_TOOL_ROUNDS`, `_CONTENT_MAX_CHARS`).
- Config dataclass: user-facing settings from env vars.
- If a number is used once and is self-documenting (e.g., `timeout=1.0`), inline is fine.

### No Slop
- No leftover debug prints, commented-out code, or TODO blocks in delivered code.
- No Chinese, non-ASCII characters, em dashes, arrows, or box-drawing in Python source.
  (Exception: documented regex patterns that must match external non-ASCII input.)
- No unused imports. Run `ruff check` after changes.
- No duplicate code -- extract to a function or constant.

---

## 3. Provider-Agnostic Rules

The code only knows "fast LLM" and "main LLM". No provider names (OpenRouter, Ollama, NVIDIA, etc.) appear in source code or comments.

- `.env.example` is the only place generic placeholder URLs and model names live.
- Use `your_llm_base_url_here` and `your_llm_model_id`, never real vendor names or IP addresses.
- If a comment mentions a provider or specific model name, it is a bug. Fix it.

---

## 4. Environment & Config

- No `os.getenv` outside `charlie/config.py`. All env access goes through `charlie.config.config`.
- When adding a new env variable:
  1. Add it to `charlie/config.py:Config` with a default value.
  2. Add a placeholder to `.env.example`.
  3. Document it in AGENTS.md if it affects agent behavior.

---

## 5. Fast LLM Key Check (Critical)

The exact guard pattern everywhere:
```python
if self.config.fast_llm_key and self.config.fast_llm_key not in ("no-key", "no_key"):
```
Use the exact tuple `("no-key", "no_key")`. Never `== "no-key"` alone -- this has caused a production bug.

---

## 6. Module Boundaries

| Module | Responsibility | Must NOT |
|---|---|---|
| `charlie/core.py` | `Brain` class. LLM streaming, tool loop, system prompt, cancel mechanism. | Import GUI or voice logic. |
| `charlie/voice.py` | `VoiceEngine`. VAD, ASR, TTS (Kokoro), audio I/O, text humanization. | Call `Brain` directly; receive callbacks only. |
| `charlie/tools.py` | `ToolRegistry` + built-in tools. Web search, shell execution, file I/O. | Business logic or LLM calls. |
| `charlie/config.py` | `Config` dataclass + singleton. Single source of `.env` keys. | Business logic. |
| `charlie/personality.py` | Emotion classification + voice command parsing. Pure keyword matching. | LLM calls or I/O. |
| `charlie/asr_worker.py` | Subprocess entry for Whisper. Only file that imports `faster_whisper`. | Other Charlie modules. |
| `charlie/session_store.py` | SQLite + FTS5 session history. Session isolation via `launch_id` column. | Anything outside session scope. |
| `main.py` | Entry point. Logging setup, voice loop, TTS flush logic, barge-in, text normalization for multi-app commands. | Feature code -- delegate to modules. |

---

## 7. Voice Pipeline Rules

- **PipelineTimer reset** MUST happen in `voice.py` at VAD onset, not in `main.py`.
- **Barge-in** must call `voice.stop_tts()`, `brain.cancel_chat()`, then set cooldown. Check `chat_generation >= generation` for cancel, not `cancel_chat_event.is_set()`.
- **TTS flush**: Sentence boundaries first, then clause, then force-flush at 100 chars.
- **Text humanization** runs in `voice.py:speak()` before queuing to Kokoro. Strip markdown, normalize unicode, convert dashes to commas, remove wrapper quotes.
- **Streaming-first**: All data flows as generators. Time-To-First-Audio must be prioritized.
- **Text normalization**: `_normalize_app_list()` in `main.py` inserts "and" between app names in multi-app commands before LLM call.

---

## 8. Testing & Verification

After any code change:
```bash
uv run ruff check .
uv run pytest -v
```
Both must pass cleanly before declaring done.

Additional checks for significant changes:
- `ast.parse` all modified Python files.
- Verify no non-ASCII characters in `.py` files.

---

## 9. Safety Rules

- Never commit `.env`, `sessions.db`, or any file containing secrets.
- Never hardcode API keys, passwords, or tokens in source code.
- Never execute destructive shell commands without explicit user approval.
- The `_BLOCKED_KEYWORDS` list in `tools.py` must not be weakened.
- Shell command blocklist: no `rm -rf`, no `format`, no `shutdown`, no `pkill`/`killall`.
- NEVER use `write` on an existing code file. `write` overwrites the entire file and destroys its contents. Use the `edit` tool for surgical changes. If `edit` fails on a multi-hunk change, use `eval` with `str.replace` on a narrow old→new block. `write` is ONLY for new files that do not yet exist. There is no undo for `write`.

---

## 10. Git Conventions

- Use conventional commits: `feat:`, `fix:`, `chore:`, `security:`.
- Commit messages: imperative mood, lowercase, under 72 chars.
- One logical change per commit.
- Never commit without running `ruff check` and `pytest` first.

---

## 11. Live Bug Patterns (Lessons Learned)

### System Prompt Real-Time Data
When the system prompt injects a value (time, date, weather), add an explicit prohibition:
```
NEVER use tools for: time, date, weather, calculations -- the current value is provided above.
```
Without this, the model will call tools for data it already has.

### CMD Built-in Commands
Windows `date` and `time` commands hang when run via `subprocess.run(shell=True)` because they prompt for input.
Use regex prefix matching (not exact string match) to translate them to PowerShell equivalents.

### Edit Tool Limitations
The `edit` tool fails on multi-hunk changes to single files. For 3+ simultaneous changes to one file,
use `eval` with `str.replace` on narrow old->new blocks instead of sequential `edit` calls. NEVER use `write` to overwrite an existing file -- it destroys the entire file content and session history.

### Ollama Models
Ollama models do NOT support native function calling (`tools`/`tool_choice` payload).
Inject tool descriptions into system prompt text and parse text-based tool invocations.
Native `tool_calls` format only works with OpenAI/Claude-class models.

### Tool Result Format for Local Models
Local models (Ollama) expect tool results as `{"role": "tool", "content": ...}`, NOT `{"role": "assistant", "content": ...}`.
Using "assistant" causes models to deny capability after successfully executing tools.
Also add explicit confirmation in tool summary (e.g., "executed successfully, now running") so the model understands the action completed.

### Multi-App Voice Commands
STT transcriptions often omit conjunctions between items (e.g., "Open Chrome calculator notepad").
Small LLMs treat this as one entity. Fix: insert "and" between known app names BEFORE sending to LLM.
Implementation: `_normalize_app_list()` in `main.py` uses regex + known-app set to add conjunctions.
Cost: zero LLM calls, works with any model. Apply in `on_speech()` before `_process()`.

### Subprocess Shared State via Env Vars
When a parent process (main.py) spawns a child (web_server.py) and they need to share state
(e.g., launch_id for session isolation), pass it as an environment variable via the `env` param
to `subprocess.Popen`. Do NOT try to import shared module-level constants -- uuid4 generates
different values at import time in each process. The child reads `os.environ["CHARLIE_LAUNCH_ID"]`.

### Session Isolation Architecture
Every main.py invocation gets a `_LAUNCH_ID` (uuid4). This is passed to the web server subprocess
via env var. Sessions created during that launch are tagged with `launch_id`. The frontend fetches
launch_id from `/api/status` on mount and can filter sidebar to "This Launch" or "All". Backend
filtering happens in `SessionStore.get_sessions(launch_id=...)`.

### Forced Tool Calling via Fast-Paths (Local Models)
Local models (Ollama) frequently ignore system prompt tool instructions, hallucinating success or taking too long (prefill latency). The only 100% reliable pattern is **deterministic pre-detection fast-paths** before the LLM call. We support:
1.  **Opening Apps & Websites:** Upgraded `_detect_open_app()` matches starting verbs, scans for whitelisted local apps (Chrome, Notepad, VS Code), popular websites (YouTube, GitHub, Wikipedia), and generic domain names (using `_URL_RE` and `_is_probable_domain()`). It launches all matched targets via `start <cmd>` in a loop and returns a grammatical confirmation, bypassing the LLM.
2.  **Closing Apps:** Upgraded `_detect_close_app()` scans for process targets and terminates them via `taskkill /IM <process> /F` in a loop, handling running vs. not-running states.
Both support single-app, multi-app (e.g., "open chrome and notepad"), and website/domain queries, eliminating LLM prefill latency.

### Active Session Synchronization (Voice + Web)
In hybrid voice-first assistants, the background voice thread (which listens to the mic) and the web server run asynchronously. To prevent speech input from routing to a stale default session while the user is viewing another session in the browser:
1.  **Frontend Broadcaster:** The React UI must send `{ type: 'session_active', session_id: currentSessionId }` over the WebSocket on load or whenever the active session changes.
2.  **Backend Sync:** `main.py:consume_web_commands` must intercept this event and update `current_web_session_id`, ensuring subsequent microphone speech is recorded and processed directly in the active session.

### Cross-Browser SQLite DateTime Parsing
SQLite's `strftime('%Y-%m-%d %H:%M:%f', 'now')` returns UTC space-separated datetime strings. WebKit-based browsers (like Safari) fail to parse space-separated strings, resulting in `Invalid Date` and breaking UI date groupings and relative timers (like "2m ago").
*   **Solution:** Always normalize SQLite timestamps to ISO-8601 format by replacing the space with a `T` and appending a `Z` (e.g. `ts.replace(' ', 'T') + 'Z'`) before calling `new Date(ts)` in the frontend.
