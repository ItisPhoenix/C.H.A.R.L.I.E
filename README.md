# Charlie

A low-latency, voice-first AI assistant that runs entirely on your local machine.

**C**ompletely **H**elpful **A**nd **R**ather **L**ocal **I**ntelligent **E**ngine.

```
Voice in  -> VAD -> Whisper ASR -> LLM (streaming) -> Kokoro TTS -> Voice out
~1.2s       ~80ms   ~410ms        ~200ms              ~50ms         ~50ms
```

---

## Features

- **Voice-first**: Continuous listening, speaks responses aloud. No keyboard needed.
- **Streaming TTS**: Speaks as the LLM generates -- no waiting for full replies.
- **Barge-in**: Interrupt Charlie mid-sentence. Say "stop", "wait", or just start talking.
- **Tool calling**: Web search, shell commands, file I/O, persistent memory, session history search.
- **Premium Glassmorphism Web Dashboard**: Frost-glass UI with liquid depth, ambient glows, and responsive three-column layouts (Sidebar, Chat, Smart Panel).
- **Smart Activity Panel**: Live feed showing the assistant's intermediate thinking steps, active tool calls, and results.
- **Persistent Voice Dock**: Animate-on-state SVG waveform reflecting VAD listening, thinking, and speaking phases.
- **Deterministic Multi-App & Website Control**: High-speed fast-paths that bypass the LLM for opening/closing single or multiple local apps, popular websites, or arbitrary domain names.
- **Active Session Synchronization**: Real-time WebSocket syncing ensures background voice interactions are recorded directly in the active browser chat.
- **Cross-Browser SQLite Datetime Parsing**: Normalizes UTC timestamps to ISO-8601, ensuring relative time tickers render flawlessly on all browsers (including Safari/WebKit).
- **Emotional tone**: Adapts speech speed and energy based on your mood.
- **Persistent memory**: Remembers facts across sessions via `MEMORY.md` and `USER.md`.
- **Local-first**: All speech processing runs locally. Only the LLM call goes to the network.
- **Self-hosted search**: SearXNG integration for private web search with no API key.
- **Agentic OS Foundation:** Blackboard pattern agent coordination, MARVEL-named agent swarm, evolving 4-layer memory system (episodic, semantic, procedural, meta), and SQLite-backed knowledge graph.
- **Next.js Web Dashboard:** Responsive glassmorphism web UI with electric blue accent, three-column layout, and WebSocket real-time sync. Built with React 19, Zustand, and Tailwind CSS v4.
- **Reflection Engine:** Periodic self-reflection that consolidates memory, updates knowledge graph, and optimizes agent performance.

---

## Architecture

```
main.py                   Entry point, logging, voice loop, TTS flush
charlie/
  core.py                 Brain class -- LLM orchestration, tool loop, streaming
  voice.py                VoiceEngine -- VAD, ASR, TTS (Kokoro), audio I/O
  tools.py                ToolRegistry -- web_search, shell_execute, file_read/write, memory, session_search
  config.py               Config dataclass from .env
  personality.py          Emotion classification + voice command parsing
  asr_worker.py           Whisper ASR subprocess
  session_store.py        SQLite + FTS5 session history
```

**Pipeline**:
1. **VAD** (Voice Activity Detection) detects when you start/stop speaking
2. **Whisper ASR** transcribes your speech to text
3. **Brain** sends text to LLM with system prompt, memory, and tool definitions
4. **Streaming**: LLM response is flushed to TTS at sentence/clause boundaries
5. **Kokoro TTS** synthesizes speech audio
6. **Speaker** plays the audio

---

## Requirements

- **OS**: Windows 11 (PowerShell for system commands)
- **Python**: 3.12+
- **GPU**: NVIDIA GPU with CUDA (for Whisper ASR and Kokoro TTS)
- **LLM**: Any OpenAI-compatible API endpoint

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
# or
uv sync
```

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env` with your settings:

```env
# Required: Your LLM endpoint
SMALL_LLM_URL=https://your-api-endpoint/v1
SMALL_LLM_API_KEY=your-key-here
SMALL_LLM_MODEL=your-model-id

# Optional: Self-hosted SearXNG for private web search
SEARXNG_URL=http://localhost:8080

# Optional: Big LLM (auto-failover when small LLM fails)
# BIG_LLM_URL=https://your-big-endpoint/v1
# BIG_LLM_API_KEY=your-key
# BIG_LLM_MODEL=your-model

# Optional: Hardware overrides
MIC_INDEX=-1          # -1 = system default, >=0 = specific device
OUTPUT_INDEX=-1
GPU_DEVICE=cuda
```

### 3. Run

```bash
python main.py          # full mode: voice engine + web dashboard + LLM Brain
```

Charlie will initialize the voice engine, download models on first run, and start listening.
The web dashboard is served at http://127.0.0.1:8000 by default (configurable via `CHARLIE_HOST`/`CHARLIE_PORT`).

> **Note on the dashboard and chat:** the web UI and the LLM Brain are one system.
> In **full mode** the dashboard is fully live - chat and voice both route through the
> Brain. The `--web-only` flag serves the UI without the Brain, so chat will not get
> a reply (use it only for static UI inspection).

```bash
python run.py --web-only   # UI only, no voice/LLM backend
```

---

## Configuration

All settings are via environment variables (`.env` file). See `.env.example` for the full list.

| Variable | Default | Description |
|---|---|---|
| `SMALL_LLM_URL` | (required) | OpenAI-compatible API base URL |
| `SMALL_LLM_API_KEY` | (required) | API key for the small LLM |
| `SMALL_LLM_MODEL` | (required) | Model ID to use |
| `SEARXNG_URL` | (empty) | Self-hosted SearXNG URL for private search |
| `WHISPER_MODEL` | `large-v3` | Whisper model for ASR |
| `KOKORO_VOICE` | `af_heart` | Kokoro TTS voice |
| `VAD_THRESHOLD` | `0.25` | Voice activity detection sensitivity (RMS) |
| `VAD_SILENCE_TIMEOUT` | `1.5` | Seconds of silence before processing |
| `ENABLE_BARGE_IN` | `true` | Allow interrupting Charlie mid-response |
| `SMALL_LLM_DISABLE_REASONING` | `true` | Disable chain-of-thought for lower latency |
| `BIG_LLM_URL` | (empty) | Secondary LLM endpoint for automatic failover |
| `BIG_LLM_API_KEY` | `no-key` | API key for the big LLM |
| `BIG_LLM_MODEL` | (empty) | Model ID for the big LLM |

---

## Voice Commands

Say these while Charlie is speaking to control behavior:

| Command | Effect |
|---|---|
| "stop" / "wait" / "cancel" | Interrupt and stop speaking |
| "be energetic" / "speak faster" | Increase speech energy and speed |
| "calm down" / "speak slower" | Slow down and speak calmly |

---

## Search Providers

Charlie tries search providers in this order:

1. **SearXNG** (self-hosted, no API key) -- recommended
2. **Exa** (requires `EXA_API_KEY`)
3. **Tavily** (requires `TAVILY_API_KEY`)
4. **DuckDuckGo** (free, no key needed, rate-limited)

Set `SEARXNG_URL` in `.env` for the best experience.

---

## Project Structure

```
.
  main.py               # Entry point
  .env                  # Your configuration (not tracked)
  .env.example          # Configuration template
  MEMORY.md             # Persistent system context
  SOUL.md               # Personality and behavior rules
  USER.md               # User preferences
  sessions.db           # Conversation history (SQLite)
  charlie/
    __init__.py
    core.py             # LLM orchestration
    voice.py            # Voice pipeline
    tools.py            # Tool definitions
    config.py           # Configuration
    personality.py      # Emotion detection
    asr_worker.py       # ASR subprocess
    session_store.py    # Session database
    web_server.py       # FastAPI web server + WebSocket
  frontend/             # Next.js 16 + React 19 + Zustand dashboard
    src/app/page.tsx    # Main page (WebSocket, layout)
    src/components/     # VoiceDock, Sidebar, SmartPanel, ErrorBoundary
    src/store/          # Zustand store (useCharlieStore)
  models/
    kokoro-v1.0.onnx    # Kokoro TTS model (~310MB)
    voices-v1.0.bin     # Voice embeddings (~27MB)
  tests/
    test_personality.py
    test_tools.py
    test_fastpaths.py
  logs/
    charlie.log
```

---

## Development

```bash
# Backend: lint + test
uv run ruff check .
uv run pytest -v

# Frontend: type-check + test
cd frontend && npx tsc --noEmit && npm test

# Frontend: dev server (for dashboard)
cd frontend && npm run dev
```

---

## How It Works

### Streaming TTS
Charlie does not wait for the full LLM response before speaking. As tokens arrive:
- **Sentence boundaries** (`.`, `!`, `?`) trigger immediate TTS flush
- **Clause boundaries** (`,`, `;`, `:`) also trigger flush for faster response
- **Force flush** at 250 characters prevents long pauses

### Tool Loop
When the LLM wants to use a tool (e.g., web search):
1. Tool executes with a 15-second timeout
2. Result is injected into the conversation as `{"role": "tool", "content": ...}`
3. LLM generates a final answer from the result
- Max 12 tool rounds per question
5. Text normalization: multi-app commands (e.g., "Open Chrome calculator notepad") get "and" inserted between app names before LLM call
6. Multi-argument tools (memory, session_search) parsed from text-based TOOL: format

### Barge-in
When you speak during Charlie's response:
1. Command words ("stop", "wait") trigger immediate stop
2. New speech cancels the current LLM generation
3. Cooldown prevents double-trigger within 0.8s

### Text Humanization
Before TTS, Charlie cleans LLM output for natural speech:
- Ellipsis converted to natural pause
- Dashes converted to clause breaks (commas)
- Repeated punctuation normalized
- Markdown artifacts stripped
- Numbers converted to words

---

## License

MIT
