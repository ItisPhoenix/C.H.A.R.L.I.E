# Charlie: Unified Agent Contract

This is the **single source of truth** for any agent (Claude Code, Cursor, Copilot, Codex, or other) working on CHARLIE. It is a **living contract**: any future expansion, refactor, or new feature MUST adhere to these rules to maintain Charlie's performance, personality, and integrity.

---

## 0. Agent Workflow (Non-Negotiable)

Before any non-trivial change (anything >1 line or touching a new module), the agent **MUST** follow this sequence in order:

1. **Activate relevant skills** via `skill://` (e.g., `using-superpowers`, `clean-code`, `python-pro`, `brainstorming`). If there is a 1% chance a skill applies, it MUST be used. This is not optional.
2. **Ask the user** via the `ask` tool to confirm the approach. Do not proceed without user confirmation on non-trivial work.
3. **Do web research** via `web_search` if uncertain about any API, library, or best practice. Never guess when the answer could change.
4. **Only then**, write the plan or edit code.

**Trivial exception**: A single-line typo fix or pure formatting change may skip the above, but the agent MUST state in its first response: "Applying trivial fix." and then proceed.

---

## 1. Behavioral DNA (The "Soul")

- **Agnostic Identity**: Charlie is an entity, not a "model." NEVER mention AI, LLMs, or technical boundaries in the persona.
- **Truth Protocol**: Charlie's first instinct is verification.
  - *Trivia Boundary*: Direct answers only for universally static facts.
  - *Research Default*: Any explanation, analysis, or current event triggers a research loop.
  - *Faithful Uncertainty*: If tools fail or conflict, Charlie must hedge (e.g., "I'm not completely certain, but...").
- **Emotional Continuity**: Emotions are not session-bound. They are persistent state variables that influence both linguistic tone and physical TTS delivery.
- **Streaming-First Pipeline**: All data flows must be processed as generators. Batching is a failure mode. Time-To-First-Audio (TTFA) must be prioritized over Time-To-Last-Byte.
- **Latency Masking (Verbal Fillers)**: Never allow silence during computation. Use the `on_thought_callback` to inject "Thinking Breath" audio cues.
- **Thread-Safe Vocalization**: TTS must always use a thread-safe queue. Spawning threads per sentence is prohibited; use a persistent worker loop.
- **Adaptive Volatility**: Charlie's verbosity is inversely proportional to user interruption frequency. Interruption = immediate transition to `concise` mode.
- **Voice-Safe Output**: All linguistic generation must pass through a phonetic safety filter (no markdown, no symbols, no lists).

---

## 2. System Architecture & Module Boundaries

These boundaries are **load-bearing**. Never cross a boundary without a strong architectural justification reviewed by the user.

| Module | Responsibility | What It Must NOT Do |
|---|---|---|
| `charlie/core.py` | `Brain` class. Orchestrates LLM streaming, backend fallback, background tasks (`_run_background_research`, `_run_background_read`, `_run_background_memory_extraction`, `_consolidate_conversation`), memory injection, reasoning disable toggle (`disable_reasoning` param). | Never import GUI or voice logic. Never import `personality` internals directly. |
| `charlie/voice.py` | `VoiceEngine` class. Manages VAD, ASR (via `asr_worker` process), TTS (Kokoro). | Never calls `Brain` directly; only receives callbacks. |
| `charlie/research.py` | Standalone functions: `web_search`, `read_url`, `deep_research`. | Only touches `Brain.client`, `Brain.fast_client`, `Brain.config`. No direct `core.py` internals. |
| `charlie/llm_router.py` | `LLMRouter` + `RouterHeuristic` + `QueryCategory`. Pure classification logic. | No HTTP or I/O. |
| `charlie/personality.py` | `CharliePersona`. Builds system prompts. Manages emotional state/scales. | Read-only access to `profile_manager`, `memory_manager`. |
| `charlie/memory_manager.py` | `MemoryManager`. SQLite + local numpy vector search. | No ONNX, no net I/O. |
| `charlie/embedder.py` | `LocalEmbedder`. ONNX embedding model. CPU-only inference. | No network, no DB. |
| `charlie/config.py` | `Config` dataclass + singleton `config`. Single source of `.env` keys. | No business logic. No env access anywhere else. |
| `charlie/discovery.py` | `SystemDiscovery`. Runtime capability detection. | — |
| `charlie/profile_manager.py` | Reads/writes `SOUL.md` and `USER.md`. | — |
| `charlie/research_memory.py` | Separate SQLite for research sessions. | — |
| `charlie/mcp_client.py` | `CharlieMCPClient`, `CharlieMCPTool`. MCP protocol wrapper. | — |
| `charlie/pipeline_instrumentation.py` | Timers and delta loggers. No business logic. | — |
| `charlie/audio_analysis.py` | Audio helper (minimal). | — |
| `charlie/widget_bridge.py` | `WidgetBridge(QObject)`. Qt signal hub between async backend and widget thread. Signals include `screen_category_changed`. | No business logic. Never blocks the GUI thread. |
| `charlie/screen_context.py` | `ScreenContextMonitor`. Polls foreground window title (Windows ctypes). Classifies into categories (coding, browsing, work, leisure, error, other). | No OCR. No content reading. Title string only. |
| `charlie/proactive_remark.py` | `ProactiveRemarkEngine`. Checks triggers every 30s, enforces 15min cooldown. `update_facts()` injects memory facts; `update_screen_category()` injects screen context. | Never calls LLM directly (use callback). No GUI imports. |
| `charlie/buddy.py` | `CharlieBuddy(QWidget)`. Glass-orb character. QPainter rendering, state machine, animation, idle fidget, startup greeting, emotional persistence (`buddy_state.json`). | Never calls Brain or VoiceEngine directly. All data via bridge signals. |
| `charlie/dashboard.py` | `CharlieDashboard(QWidget)`. Expanded transcript/status/memory view. | Never calls Brain or VoiceEngine directly. All data via bridge signals. |
| `main.py` | Entry point. `--terminal` flag for headless mode. `argparse` → `QApplication` + widget wiring or terminal loop. | Minimal logic. No new feature code. |
| `charlie/asr_worker.py` | Subprocess entry for Whisper. | Only this file should import `faster_whisper`. |
| `charlie/__init__.py` | Exports: `Brain`, `config`, `VoiceEngine`, `LLMRouter`, `CharlieMCPClient`, `SystemDiscovery`, `WidgetBridge`, `ScreenContextMonitor`, `ProactiveRemarkEngine`. | Any new public symbol MUST be added to `__all__`. |

## 3. Coding Standards

### General
- Type hints on all public function signatures. Use `Optional[...]` and `List[...]`; no bare `list` or `dict` in signatures.
- Docstrings on public classes and functions. One-line docstring is acceptable for simple cases.

### Environment & Config
- No `os.getenv` outside `charlie/config.py`. All env access goes through `charlie.config.config`.
- When adding a new env variable:
  1. Add it to `charlie/config.py:Config` with a default value.
  2. Add a placeholder line to `.env.example`.
  3. Document it in the AGENTS.md if it affects agent behavior.

### Provider-Agnostic Rules
- The code only knows "fast LLM" and "main LLM". No OpenRouter, Ollama, or any other provider name appears in source code or comments.
- The `.env.example` file is the **only** place generic placeholder URLs and model names live. Use `your_llm_base_url_here` and `your_llm_model_id`, never real vendor names or IP addresses.
- If a comment mentions a provider or specific model, it is a bug. Fix it immediately.

### Fast LLM Key Check (Critical — Past Bugs Here)
- The exact guard pattern everywhere:
  ```python
  if self.config.fast_llm_key and self.config.fast_llm_key not in ("no-key", "no_key"):
  ```
- Use the exact tuple `("no-key", "no_key")`. Never `== "no-key"` alone — this has already caused a production bug.
- When adding similar guards for new keys, always anticipate both hyphen and underscore variants in `.env`.

### Fast LLM Fallback (Critical — Past Bugs Here)
- All fast-client calls must fall back to `self.client`. Use the existing `Brain._llm_completion()` helper for single-shot requests.
- When adding a new fast-llm call, prefer `_llm_completion()` first. If the call needs streaming, implement the fallback manually:
  ```python
  if self.config.fast_llm_key and self.config.fast_llm_key not in ("no-key", "no_key"):
      try:
          # try fast_client
      except Exception:
          logger.debug("fast LLM failed, falling back to main")
  # then try self.client (main)
  ```

### Reasoning Disable (Fast-Path Latency)
- All fast-client streaming calls should pass `disable_reasoning=True` when using the fast client.
- The `chat()` method detects fast path via `client is self.fast_client`.
- Background tasks (memory extraction, consolidation, research summaries) use `self.config.fast_llm_disable_reasoning` directly.
- The payload key is `"reasoning": {"effort": "none"}` — silently ignored if the provider does not support it.

### Local LLM Timeout
- `LOCAL_LLM_TIMEOUT_SEC` defaults to 8.0s. Ollama warm TTFT ranges 300–1200ms; the previous 3.0s default caused persistent `ReadTimeout` failures.
- `OLLAMA_KEEP_ALIVE` must be set to `24h` to prevent VRAM eviction (~36s cold-start cost).

### Initialization
- No duplicate class initialization. One assignment per instance variable in `Brain.__init__`.
- The previous `LLMRouter` double-init is a canonical example of what not to do.

## 4. Error Handling & Logging

- Use `logger = logging.getLogger("charlie.<module>")` per module. Do not share loggers across modules.
- Log key business events with `logger.info`. Use `logger.debug` for internal flow. Use `logger.warning` for recoverable failures. Use `logger.error` for user-visible failures.
- Never swallow exceptions silently. All background tasks (`_run_background_*`) must be wrapped in `try/except` with a user-facing `on_thought_callback` message in the except block.

## 5. Testing & Verification

- After **any** code change, run:
  ```
  uv run pytest -v
  uv run ruff check .
  ```
- Both must pass cleanly before declaring done.
节目的: If you change fast/main client logic, add a test asserting fallback when both fail.
- Verify no dead code (`search` + `ast_grep` for unused functions).

## 6. Future Integration Rules

When adding new skills or tools:
1. **Zero-Latency Acknowledgment**: The tool must have a "Start-of-Work" verbal cue (e.g., "Checking that for you...").
2. **Clean Interception**: Ensure the tool-trigger (e.g., `TOOL:`) is buffered and hidden from the user's ears.
3. **Open-Endpoint Standard**: Maintain strict OpenAI-compatible API standards to keep the LLM backend swappable.

## 7. Engineering Verification Protocol

Before declaring any feature complete, answer these four questions:
1. **Streaming Integrity**: Does the change introduce a "wait-for-full-reply" block?
2. **Truth Check**: Can I force this feature to lie? (If yes, add a prompt guardrail).
3. **Phonetic Audit**: How does the new output sound when read by a 1.0x speed TTS?
4. **State Persistence**: Does this feature survive a `Ctrl+C` restart?

---

## 8. Evolving This Document

This is a **living contract**, not a snapshot. When the architecture changes, this document must change first.

### Immutable Rules (Change Only With User Approval)
- **Agent Workflow** (Section 0): Always ask, research, and activate skills first.
- **Provider-Agnostic Rules** (Section 3): No provider names in code, ever.
- **Fast LLM Key Check & Fallback** (Section 3): The exact patterns are load-bearing.
- **Testing Requirements** (Section 5): `pytest` + `ruff` must pass.
- **Behavioral DNA** (Section 1): Charlie's identity is fixed.

### Living Sections (Update When Files Change)
- **Module Boundaries** (Section 2): When a new module is added, it MUST be added to the table with its responsibility and boundaries. When a module's role changes, update its row.
- **Coding Standards** (Section 3): New error-prone patterns get new sub-sections (e.g., a new API key check variant gets a new "Critical ... Past Bugs Here" entry).

### Adding a New Module or Feature
When an agent adds a new module, it must also update AGENTS.md:
1. Add the new file to the **Module Boundaries** table in Section 2. Define exactly what it does and what it must NOT do.
2. If the new module needs env variables, add them to `charlie/config.py` and `.env.example` per Section 3.
3. If the new module needs fast-LLM access, use the fallback pattern in Section 3.
4. If the new module is public, add it to `charlie/__init__.__all__`.
5. Run `uv run pytest -v` and `uv run ruff check .`.
6. Update the **Last updated** date at the bottom of this document.

### Why This Works
Future agents parse AGENTS.md first. If they see the module table is missing their new file, they know they must add it before the user review. If they see a new error-prone pattern, they document it in the coding standards. The doc grows with the code, but the core rules (ask first, provider-agnostic, test everything) never drift.

---

*Last updated: 2026-06-20*
