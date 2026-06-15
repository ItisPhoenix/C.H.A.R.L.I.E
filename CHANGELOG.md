# Changelog

## [2026-06-16] — Passive Attention, Hybrid Routing & Tool Agency

### Added
- **Hands-free Wake Word**: Integrated `openWakeWord` with custom `charlie.onnx` model. Passive background listening with immediate "Barge-in" support.
- **Hybrid LLM Router**: New `LLMRouter` logic that automatically shifts simple queries (time, weather, jokes) to a local Ollama model while keeping complex research in the cloud.
- **MCP (Model Context Protocol) Client**: Native agency through external tool servers. 87 tools discovered and active (Playwright, Obsidian, FileSystem, etc.).
- **Procedural Audio Interface**: Synthesized non-file-based chimes for wake (upward) and smart-mode-timeout (downward) signals.
- **Smart Conversational Mode**: Continuous listening window for 15 seconds after responses, allowing follow-ups without wake-word repetition.
- **Dynamic System Manifest**: Personality now automatically understands its own architectural state, hardware senses, and recent upgrades.

### Fixed
- **Brain Logger Crash**: Restored missing `logger` definition in `core.py` that caused startup failure.
- **openWakeWord API Mismatch**: Corrected `wakeword_model_paths` parameter and removed invalid `inference_framework` kwarg.
- **ASR Continuity**: Hardened the Whisper worker to handle back-to-back phrase capture in smart-mode.

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
