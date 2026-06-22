# C.H.A.R.L.I.E.

**Completely Helpful And Rather Local Intelligent Engine**

Charlie is a high-fidelity, voice-first local AI assistant built for speed, privacy, and agency.

## Architecture

```text
.
├── main.py                # Entry point, async event loop, widget wiring
├── charlie/
│   ├── core.py            # Brain — chat orchestration, backend fallback, research, tool use
│   ├── voice.py           # VoiceEngine — ASR (Whisper), TTS (Kokoro), VAD (Silero)
│   ├── llm_router.py      # Heuristic query classifier & backend selection
│   ├── asr_worker.py      # Whisper transcription in isolated subprocess
│   ├── mcp_client.py      # MCP tool server connectivity
│   ├── discovery.py       # Runtime system self-awareness
│   ├── personality.py     # Identity, emotional state, dynamic prompt builder
│   ├── profile_manager.py # Soul & user profile persistence
│   ├── memory_manager.py  # SQLite-backed long-term memory
│   ├── research_memory.py # Semantic knowledge layer across sessions
│   ├── research.py        # Web search + deep research pipeline
│   ├── embedder.py        # ONNX embedding model (CPU-only inference)
│   ├── audio_analysis.py  # Waveform → mouth-value mapping for lip sync
│   ├── pipeline_instrumentation.py  # Per-stage latency timers
│   ├── config.py          # Central configuration (single source of .env keys)
│   ├── widget_bridge.py   # Qt signal hub between async backend and widget thread
│   ├── buddy.py           # Glass-orb character widget (QPainter, state machine, animation)
│   ├── dashboard.py       # Expanded transcript/status/memory view
│   ├── screen_context.py  # Foreground window title monitor (Windows ctypes)
│   ├── proactive_remark.py # Anticipatory remark engine (30s triggers, 15min cooldown)
│   ├── __init__.py        # Package exports
│   └── data/              # Runtime state (stances, profiles, memories, buddy_state)
├── tests/
│   ├── test_router.py     # Router heuristic & backend selection tests
│   ├── test_strip.py      # Thinking-tag stripping tests
│   ├── test_memory_manager.py
│   └── test_research.py
├── SOUL.md                # Core personality & values
├── CHANGELOG.md           # Source of truth for all changes
├── AGENTS.md              # Unified agent contract (all instructions)
├── CLAUDE.md              # Claude Code–specific notes (points to AGENTS.md)
├── .env.example           # Environment variable reference
├── pyproject.toml         # Project metadata & dependencies
└── mcp_config.json        # External MCP tool configuration
```

## Core Systems

- **Routing**: Heuristic query classifier (`llm_router.py`) — categorizes every input as TRIVIAL/SIMPLE/COMPLEX/CREATIVE/TOOL and selects optimal backend (fast local model vs cloud reasoning model) without extra LLM calls.
- **Hearing**: `Whisper` (distil-large-v3) in an isolated subprocess via `multiprocessing` — avoids GIL contention during transcription.
- **Voice**: `Kokoro-ONNX` GPU-accelerated TTS with automated CUDA detection and CPU fallback.
- **Brain**: Multi-backend LLM orchestration with automatic fallback — iterates through backends on failure.
- **Memory**: SQLite-backed long-term semantic storage with keyword extraction and vector search.
- **Research**: Real-time web intelligence via `SearXNG` + `Crawl4AI` in non-blocking background threads.
- **Agency**: MCP (Model Context Protocol) client for external tool servers (80+ tools).
- **Buddy Widget**: Glass-orb holographic character (`buddy.py`) — QPainter-rendered, tracks mouse, shows emotion via color/expression, idle fidget, startup greeting, emotional persistence across sessions.
- **Screen Context**: Foreground window title monitor (`screen_context.py`) — classifies activity (coding, browsing, work, leisure) for context-aware expressions and proactive remarks.
- **Proactive Remarks**: Anticipatory remark engine (`proactive_remark.py`) — triggers on morning greeting, error windows, long silence, memory recall. Uses fast LLM with memory facts injection.
- **Reasoning Disable**: Fast-path LLM receives `reasoning.effort: none` payload by default to suppress `<thinking>` output and reduce TTFT.

## Quick Start

1. Configure `.env` (see `.env.example`).
2. Configure tools in `mcp_config.json`.
3. Run: `uv run python main.py`
4. Say **"Charlie"** to begin.

---

*"I treat data-driven skepticism as a moral imperative."* — Charlie
