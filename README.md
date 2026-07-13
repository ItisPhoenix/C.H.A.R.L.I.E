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
- **Agentic OS Foundation:** Blackboard pattern agent coordination, MARVEL-named agent swarm, episodic + semantic memory (session history, vector store, and SQLite-backed knowledge graph).
- **Next.js Web Dashboard:** Responsive glassmorphism web UI with electric blue accent, three-column layout, and WebSocket real-time sync. Built with React 19, Zustand, and Tailwind CSS v4.
- **Reflection Engine:** Periodic self-reflection that consolidates memory, updates knowledge graph, and optimizes agent performance.
- **Model Context Protocol (MCP):** When enabled, register tools from external MCP servers at runtime and call them alongside the built-in tools.
- **Plugin system:** A hybrid plugin loader adds external integration tools when `PLUGINS_ENABLED` is on.
- **LLM failover:** Optionally configure a secondary "big" LLM that Charlie falls back to automatically if the primary (small/fast) model errors out.
- **Mood-aware voice:** Whether you sound annoyed, excited, or depressed changes Charlie's speech energy and pacing.

---

## Agents & Tools

Charlie is more than a chat loop. A **swarm of specialist agents** coordinates on a
shared blackboard to handle complex, multi-step requests:

| Agent | Focus |
|---|---|
| **J.A.R.V.I.S.** | Orchestrator -- analyzes requests, coordinates tasks, spawns planning |
| **Vision** | Planner -- decomposes requests into sub-task graphs and dependencies |
| **F.R.I.D.A.Y.** | Code generation and file operations specialist |
| **E.D.I.T.H.** | Research specialist -- gathers intelligence via web search |
| **A.I.D.A.** | Content creation specialist -- copy, emails, reports |
| **K.A.R.E.N.** | System diagnostics and health monitoring specialist |
| **H.E.R.B.I.E.** | Verification specialist -- checks deliverables against acceptance criteria |

The tools these agents (and the Brain) can call include:

- **Web search** — self-hosted SearXNG plus Exa / Tavily / DuckDuckGo fallbacks.
- **Shell execution** — sandboxed command runs with a protective blocklist.
- **File I/O** — read and write within allowed paths.
- **Memory search** — query the vector store and knowledge graph.
- **Session history search** — full-text (FTS5) search over past conversations.
- **MCP / plugin tools** — anything registered from external servers or plugins.

---

## Requirements

- **OS**: Windows 11 (PowerShell for system commands)
- **Python**: 3.12+
- **GPU**: NVIDIA GPU with CUDA (for Whisper ASR and Kokoro TTS)
- **LLM**: Any OpenAI-compatible API endpoint

---

## Quick Start

### 1. Install dependencies

Charlie uses [`uv`](https://docs.astral.sh/uv/) for its Python dependencies.

```bash
uv sync --locked
```

(For the dashboard UI: `cd frontend && npm ci && npm run build`.)

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
python run.py              # full mode: voice engine + web dashboard + LLM Brain
```

Charlie will initialize the voice engine, download models on first run, and start listening.
The web dashboard is served at http://localhost:8000 by default (override with `CHARLIE_PORT`).
`3000` is only the port used by the Next.js dev server (`npm run dev`); the production
dashboard runs on `8000`.

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

Charlie also understands a couple of typed directives:

| Command | Effect |
|---|---|
| `!search <query>` | Search your past conversations (full-text) and read back the matches |
| `/memory-review` (or `!memory-review`) | Print a summary of the knowledge graph Charlie has built from memory |

---

## Search Providers

Charlie tries search providers in this order:

1. **SearXNG** (self-hosted, no API key) -- recommended
2. **Exa** (requires `EXA_API_KEY`)
3. **Tavily** (requires `TAVILY_API_KEY`)
4. **DuckDuckGo** (free, no key needed, rate-limited)

Set `SEARXNG_URL` in `.env` for the best experience.

---

## License

MIT
