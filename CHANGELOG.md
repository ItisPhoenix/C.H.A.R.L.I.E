# Changelog
## [2026-06-20] — Local LLM Timeout Fix & Ollama Optimization

### Changed
- **`charlie/config.py`**: `LOCAL_LLM_TIMEOUT_SEC` default raised from 3.0s to 8.0s — Ollama warm TTFT ranges 300–1200ms; the previous 3s timeout was too tight and caused persistent `ReadTimeout` failures on every fast-path request.
- **`.env.example`**: Added timeout section (`LOCAL_LLM_TIMEOUT_SEC`, `CLOUD_LLM_TIMEOUT_SEC`), barge-in toggle (`ENABLE_BARGE_IN`), and memory config vars (`CHARLIE_MEMORY_MAX_CORE`, `CHARLIE_MEMORY_MAX_RECALL`, `CHARLIE_MEMORY_EXTRACT_WORDS`, `CHARLIE_MEMORY_CONSOLIDATE_AFTER`). Added embedding config section (`EMBEDDING_MODEL`, `EMBEDDING_DIMENSION`).

### Operational
- **Ollama `OLLAMA_KEEP_ALIVE`**: Set to `24h` via `setx` system env var. Prevents model eviction from VRAM between interactions (previously defaulted to 5m, causing ~36s cold-start TTFT).
## [2026-06-19] — Buddy Personality, Emotional Persistence & Fast-Path Latency

### Added
- **Reasoning disable toggle** (`charlie/config.py`): `llm_disable_reasoning` and `fast_llm_disable_reasoning` env vars. Default `true` — sends `"reasoning": {"effort": "none"}` payload to fast-path LLM, disabling `<thinking>` output for sub-200ms TTFT. Provider-agnostic; silently ignored by endpoints that don't support it.
- **Startup greeting**: Buddy greets user by name on first frame (visual bubble only, no TTS — TTS welcome handled by `main.py`).
- **Idle fidget system** (`charlie/buddy.py`): After 10s of no mouse movement, buddy enters fidget mode — subtle pupil saccades, gentle head tilt, and a "bored/sleepy" expression. Resets instantly on mouse click or move.
- **Mouse reactivity ±6px** (`charlie/buddy.py`): Pupil tracking range extended from 4px to 6px. Added relax-to-center when cursor is absent. Fidget saccade blends with mouse tracking when idle.
- **Emotional persistence** (`charlie/buddy.py`): Session count, total interactions, last emotion, last stance, timestamps, and favorite topics saved to `charlie/data/buddy_state.json`. Loaded on startup, saved every 30s and on window close. Last emotion restores buddy's visual state across sessions.
- **Memory-driven proactive remarks** (`charlie/proactive_remark.py`): `update_facts()` and `update_screen_category()` methods inject core memory facts and user preferences into LLM remark prompts. `main.py` wires `brain.memory_manager.get_core_facts(limit=3)` and `brain.profile_manager.get_user_facts()` into proactive check every 30s.
- **Screen category signal** (`charlie/widget_bridge.py`): `screen_category_changed` Qt signal emitted alongside raw title. Classifies via `ScreenContextMonitor._classify()` — used by buddy for expression adjustments.
- **"Work" screen category** (`charlie/screen_context.py`): Added `WORK_KEYWORDS` (Word, Excel, Slack, Teams, Notion, Jira, Figma, Canva, etc.) to classifier, mapping to `"work"` category.

### Changed
- **`charlie/core.py`**: `_call_llm_stream()` and `_llm_completion()` accept `disable_reasoning` param. `chat()` passes `fast_llm_disable_reasoning` when using fast client. Background tasks (memory extraction, consolidation, research summaries) also respect the toggle.
- **`charlie/buddy.py`**: `_update_pupils()` extended to 6px range with fidget saccade blending and cursor-absent relax. `_tick()` now tracks idle fidget state and overrides expression to "sleepy" when fidgeting. `mousePressEvent` resets fidget state on click.
- **`main.py`**: `_run_proactive_check()` injects memory facts via `proactive.update_facts()` and passes screen category to buddy via `buddy.set_screen_category()`. `on_speech` tracks `record_voice_interaction()` on buddy.
- **`.env.example`**: Added `LLM_DISABLE_REASONING` and `FAST_LLM_DISABLE_REASONING` commented entries.

### Fixed
- **Duplicate `FAST_LLM_MODEL` in `.env.example`**: Removed duplicate line.

## [2026-06-19] — Desktop Widget / Buddy Character Mode

### Added
- **Buddy Character Widget (`charlie/buddy.py`)**: Living 'C' circle character rendered via QPainter on a transparent always-on-top frameless Qt widget. Eyes blink, pupils track mouse, expressions shift with emotional state, body breathes and bounces during speech, stance poses (skeptical, passionate, etc.) triggered by Charlie's opinions.
- **Dashboard (`charlie/dashboard.py`)**: Expanded view toggled by double-clicking buddy. Shows live transcript, latency stats (ASR→LLM, LLM→TTS, total E2E), memory sidebar, mic mute, barge-in toggle. Dark theme.
- **Widget Bridge (`charlie/widget_bridge.py`)**: Qt signal hub connecting async Brain/VoiceEngine threads to Qt GUI thread. Signals: transcript_chunk, emotional_state_changed, audio_level, stance_expressed, proactive_remark, greeting_ready, screen_context_changed.
- **Screen Context Monitor (`charlie/screen_context.py`)**: Polls foreground window title every 2s via Windows ctypes. Classifies into error/coding/browsing/leisure/other. No OCR — title only for privacy.
- **Proactive Remark Engine (`charlie/proactive_remark.py`)**: Anticipatory speech system. Checks triggers (morning greeting, error windows, long silence, memory recall) every 30s with 15-minute cooldown. Uses fast LLM for generation, falls back to hardcoded templates.
- **Sleep System**: Buddy enters sleep after 5min idle (2min at night 22:00–06:00). Eyes close, Zzz animation, reduced glow. Wakes on click/voice/mouse. Daily greeting on wake.
- **Adaptive Sizing**: Idle 80px, speaking 200px, sleeping 64px, listening 160px. Smooth lerp transitions.
- **Interaction System**: Click toggles listen/deactivate/barge-in. Drag repositions. Right-click context menu (Dashboard, Mute, Quit). Double-click toggles dashboard. Hover tooltip with state/emotion/screen context.
- **Time-of-Day Awareness**: Morning (energetic, 1.2x speed), afternoon (normal), evening (warmer, 0.9x), night (dimmer, 0.7x, earlier sleep).
- **Fidget Animations**: Tiny pen tapping, ball spinning, book reading — triggered randomly during idle.
- **`--terminal` flag**: `python main.py --terminal` runs headless without PySide6. Widget mode is default. PySide6 import guarded with try/except.
- **ProfileManager.get_user_name()**: Extracts user name from USER.md or SOUL.md for personal greetings.
- **Widget tests (`tests/test_buddy.py`)**: 22 tests covering bridge, screen context classifier, proactive engine triggers/cooldowns, buddy state machine logic, expression mapping, size targets, stance map, time-of-day modifiers.

### Changed
- **`main.py`**: Added `argparse` for `--terminal` flag. Widget mode creates `QApplication`, `WidgetBridge`, `CharlieBuddy`, `CharlieDashboard`, `ScreenContextMonitor`, `ProactiveRemarkEngine` with full signal wiring. Terminal mode unchanged.
- **`charlie/voice.py`**: Added `_current_rms`, `_rms_callback`, `_widget_callback` fields. RMS computed in mic callback, emitted via `_rms_callback`. Mode changes (listening/speaking/idle) emitted via `_widget_callback`.
- **`charlie/core.py`**: Added `_emotional_state_callback` field. Emitted after `persona.detect_emotion()` in `chat()`.
- **`charlie/personality.py`**: Added `_stance_callback` field. Emitted when new stance is added to `expressed_stances` in `build_system_prompt()`.
- **`charlie/__init__.py`**: Added `WidgetBridge`, `ScreenContextMonitor`, `ProactiveRemarkEngine` to exports.
- **`pyproject.toml`**: Added `PySide6>=6.6.0` dependency.
- **AGENTS.md**: Updated module table with 5 new modules, updated main.py and __init__.py descriptions.


## [2026-06-18] — Heuristic LLM Router & Buddy UI Purge

### Added
- **Heuristic Query Classifier**: `llm_router.py` rewritten with `RouterHeuristic.classify()` — zero-latency prefix-based routing (TRIVIAL/SIMPLE/COMPLEX/CREATIVE/TOOL) without extra LLM calls.
- **`select_backends()` method**: Replaces old `route()` + `_fast_fn` pattern. Returns ordered backend list based on query category; fast-first for trivial/simple, main-first for complex/creative/tool.
- **`strip_internal_reasoning()` shared helper**: Consolidated thinking-tag stripping (Chinese `<` `>`, `<thinking>`, `<thought>`, untagged reasoning) in `core.py`, used by both `Brain.chat()` and `VoiceEngine.speak()`.
- **Router tests**: `tests/test_router.py` — 17 tests for classifier and backend selection.
- **Strip tests**: `tests/test_strip.py` — 9 tests for reasoning tag removal.

### Removed
- **Buddy UI (charlie-buddy)**: Deleted `charlie/bridge.py`, `charlie/ui_launcher.py`, removed all imports and init from `core.py` — no more Electron bridge or buddy UI.
- **Dead code**: Removed `route()` method and `_fast_fn` nested function — replaced by `select_backends()` and backend iteration loop.
- **Inline reasoning regexes**: Removed duplicate thinking-tag stripping in `voice.py` — now uses shared `strip_internal_reasoning()`.
- **Unused imports**: Cleaned `asyncio` from `voice.py`, `os` from `asr_worker.py`, `Dict` from `research_memory.py`, `asyncio` from `test_research.py`.

### Changed
- **VoiceEngine.speak()**: Consolidates three inline regex strips into one shared helper call.
- **Close method**: Removed bridge/ui_launcher stop from `Brain.close()`.

---

## [2026-06-17] — Audio Pipeline Hardening & Hardware Recovery

### Added
- **Resilient TTS Provider**: Automated CUDA 13.x detection in `voice.py`. Automatically falls back to `CPUExecutionProvider` if `cublasLt64_13.dll` is missing, preventing ONNX Runtime initialization crashes.
- **Mic Health Check**: Real-time variance monitoring of microphone signal. Charlie now detects and logs alerts for "Dead/Static" signals (e.g., constant hum) which prevents silent speech detection failure.

### Fixed
- **VAD Model Corruption**: Restored the correct Silero VAD v3.1 ONNX model (797KB, `h0/c0` interface) after it was accidentally replaced by an incompatible v5 model.
- **High-Speed Research**: Verified and optimized SearXNG connectivity. JSON format access is now fully operational, providing Charlie with unrestricted high-speed search results.

### Changed
- **Logs**: Cleared logs and pruned temporary diagnostic files for a fresh system state.

---

## [2026-06-16] — Passive Attention, Hybrid Routing & Tool Agency

### Added
- **Hybrid LLM Router**: New `LLMRouter` logic that automatically shifts simple queries (time, weather, jokes) to a local Ollama model while keeping complex research in the cloud.
- **MCP (Model Context Protocol) Client**: Native agency through external tool servers. 87 tools discovered and active (Playwright, Obsidian, FileSystem, etc.).
- **Dynamic System Manifest**: Personality now automatically understands its own architectural state, hardware senses, and recent upgrades.

### Fixed
- **Brain Logger Crash**: Restored missing `logger` definition in `core.py` that caused startup failure.

### Changed
- **Dependencies**: Added `mcp` SDK and fixed `uv` environment synchronization.

## [2026-06-15] — Fillers removed, word-by-word fix, default mic

### Fixed
- **Word-by-word TTS**: Max-char guard split at every space via `(?<=.)\s+` — replaced with single split at last word boundary before limit; threshold raised from 80 to 200 chars
- **Startup crash**: Restored `_run` method header, `try:` block, `audio_buffer` and `processing_thread` attrs lost during filler removal

### Removed
- **Backchannel fillers**: Removed `BACKCHANNEL_FILLERS`, `play_filler()`, `filler_cache` — eliminates startup synthesis overhead and filler interruption logic
- **TTS timing from INFO**: Demoted pipeline TTS latency log to `DEBUG`

### Changed
- **Default mic**: `mic_index` now defaults to `-1` (system default); wired into `RawInputStream` via `device=` parameter
## [2026-06-15] — GPU Acceleration & Latency Optimization

### Added
- **GPU TTS**: Kokoro ONNX on `onnxruntime-gpu 1.26.0` with CUDA 12.4 — ~700ms vs 7.4s CPU
- **Pre-synthesized filler audio**: Backchannel phrases cached at startup, instant `sd.play()` bypass
- **Per-stage timing logs**: Structured `pipeline_stage | stage=… | latency_ms=…` output for ASR, LLM TTFT, TTS
- **Symbol-to-word conversion**: 29 symbol mappings (° → degrees, % → percent, etc.) before TTS phonemization
- **ASR warm-up**: Silent audio sent on init to pre-compile Whisper CUDA graph
- **ONNX log suppression**: `ORT_LOG_LEVEL=3` hides noisy CUDA provider warnings

### Changed
- **LLM routing**: Dual-path (fast/slow) with automatic fallback; fast path for summarization
- **ASR model**: `large-v3` → `distil-large-v3`, `beam_size=1`, `best_of=1`, `vad_filter=True` — 26ms vs 150ms
- **Response length**: Default 2-3 lines, detailed mode only when user asks or topic requires depth
- **Backchannel fillers**: Replaced hesitant fillers with natural responses (Sure., Right., Okay., I see., Go on.)

## [2026-06-14] — Major Personality & Research Upgrade

### Audit - Architectural & Behavioral improvements
- **ASR Subprocess Isolation**: Moved Whisper transcription to a dedicated worker process via `multiprocessing` to eliminate GIL contention during voice processing.
- **Persistent Stance Pruning**: Opinions expressed by Charlie are now persisted in `charlie/data/expressed_stances.json` and pruned from future prompts to reduce repetition.
- **Emotion-Verbosity Matrix**: Config-driven mapping between emotional states and response modes (`concise`, `normal`, `detailed`).
- **Stance Re-injection**: Added detection for explicit opinion requests to re-inject pruned stances when relevant.

### Added
- **Asynchronous Research Pipeline**: Research tasks now run in non-blocking background threads.
- **Immediate Feedback**: Charlie now acknowledges research requests instantly and alerts the user when complete.
- **Background Chime**: Spoken "Ding!" notification when background tasks finish.
- **Semantic Memory Layer**: Persistent `semantic_knowledge` table for long-term learning across sessions.
- **Dual-Model Routing**: Support for a faster backend model for research steps and a high-reasoning model for chat.
- **Semantic Recall**: Automatic injection of past research insights and semantic summaries into the system prompt.

### Fixed
- **SearXNG 403 Forbidden**: Improved request headers with browser-mimicking `User-Agent` and `Accept` fields.
- **System Prompt Deduplication**: Cleaned up redundant prompt generation logic in `personality.py`.

### Changed
- **Personality Refinement**: Updated Charlie to be more brilliant, cynical, and tech-focused.
- **Synthesis Optimization**: `deep_research` now generates more concise, structured reports.
- **Emotional Injection**: Current emotional state is now explicitly enforced in every system prompt.
