# Charlie

A low-latency, voice-first AI assistant that runs entirely on your local machine.

**C**ompletely **H**elpful **A**nd **R**ather **L**ocal **I**ntelligent **E**ngine.

[![CI](https://github.com/ItisPhoenix/C.H.A.R.L.I.E/actions/workflows/ci.yml/badge.svg)](https://github.com/ItisPhoenix/C.H.A.R.L.I.E/actions/workflows/ci.yml)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Node 20+](https://img.shields.io/badge/node-20%2B-339933.svg?logo=node.js&logoColor=white)](https://nodejs.org/)
[![Platform: Windows](https://img.shields.io/badge/platform-Windows-0078D6.svg?logo=windows&logoColor=white)](#requirements)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

```
Voice in  -> VAD -> Whisper ASR -> LLM (streaming) -> Kokoro TTS -> Voice out
~1.2s       ~80ms   ~410ms        ~200ms              ~50ms         ~50ms
```

## Table of Contents

- [Features](#features)
- [Tools](#tools)
- [Requirements](#requirements)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Architecture](#architecture)
- [Voice Commands](#voice-commands)
- [Search Providers](#search-providers)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [License](#license)

---

## Features

### 🎙️ Voice & Conversation
| | |
|---|---|
| **Voice-first** | Continuous listening, speaks responses aloud -- no keyboard needed. |
| **Streaming TTS** | Speaks as the LLM generates -- no waiting for full replies. |
| **Barge-in** | Interrupt Charlie mid-sentence. Say "stop", "wait", or just start talking. |
| **Mood-aware voice** | Sounding annoyed, excited, or depressed changes Charlie's speech energy and pacing. |
| **Hallucination-resistant ASR** | Whisper's own confidence signals plus a known-phrase denylist filter out "Thank you."/"Bye." style hallucinations Whisper produces on silence or room noise -- without dropping genuine short replies. |
| **Click-resistant VAD** | Speech onset requires two consecutive loud audio frames (~128ms), so an isolated keyboard click can't trigger a false listen while real speech still registers instantly. |

### 🖥️ Desktop & Automation
| | |
|---|---|
| **Deterministic app & website control** | Fast-paths that bypass the LLM for opening/closing local apps, popular sites, or arbitrary domains. |
| **Focus, don't relaunch** | Asking to open an app that's already running focuses its window instead of spawning a duplicate instance -- true for both the deterministic fast-path and any shell command the model reaches for on its own (`start`, `cmd /c start`, `powershell Start-Process`, etc. all resolve to the same running-process check). |
| **Agentic desktop control** *(Windows, opt-in)* | Sees and operates your desktop -- click, type, key chords via native UI Automation, with OCR and a local vision model as fallback tiers. Off by default; needs `DESKTOP_CONTROL_ENABLED=true`, a global panic hotkey, and auto-halts on repeated failures. |
| **Helm operator persona** | Address desktop control by name ("Helm, open my email") for a narrated, step-by-step operator voice -- same panic hotkey, just a distinct identity for the task. |
| **Vision-first screen queries** | "What's on my screen?"-style questions route to the configured vision model with a freshly captured screenshot, not a stale OCR/UIA text summary -- covers both narrow ("what am I looking at") and broad ("what's on my screen") phrasings. |

### 🧠 Memory & Intelligence
| | |
|---|---|
| **Persistent memory** | Remembers facts across sessions via `MEMORY.md` and `USER.md`. |
| **Episodic + semantic memory** | Session history, a ChromaDB vector store, and a SQLite knowledge graph of facts. |
| **Reflection engine** | Periodic self-reflection (every ~5 turns) consolidates memory and updates the knowledge graph. |
| **Single-LLM architecture** | One text model handles everything (no fast/small vs. big/slow split) plus a separate, opt-in vision model for screen/image understanding. |

### 🪟 Dashboard
| | |
|---|---|
| **Glassmorphism web dashboard** | Frost-glass layout built with Vite, React 19, TypeScript, Zustand, and Tailwind CSS v4, synced live over WebSocket. See `DASHBOARD_HANDOFF.md` for the current in-progress redesign toward an on-demand floating-panel desktop layout at `/dashboard`. |
| **Smart Activity Panel** | Live feed of the assistant's intermediate thinking, active tool calls, and results. |
| **Persistent Voice Dock** | Animated waveform reflecting listening, thinking, and speaking phases. |
| **Active session sync** | Background voice interactions land directly in the active browser chat, in real time -- gated to the visible tab so a background tab can't steal routing. |
| **Toast notifications & system-status polling** | Non-blocking alerts and live CPU/RAM/GPU telemetry, hardware/files/services/local-model views. |

### 🔌 Extensibility & Reliability
| | |
|---|---|
| **Local-first** | All speech processing runs locally -- only the LLM call goes to the network. |
| **Web research engine** | Structured SearXNG search, HTTPX fetching, Trafilatura extraction, bounded evidence/citations, and optional Crawl4AI escalation. |
| **Model Context Protocol (MCP)** | Register tools from external MCP servers at runtime, callable alongside the built-ins. |
| **Plugin system** | A hybrid plugin loader adds external integration tools (filesystem, browser fetch, calendar, sandboxed Python) when `PLUGINS_ENABLED=true`. |
| **Extension install gate** | Content-hash + prompt-injection heuristic scan gates any new extension (OpenAPI import, `SKILL.md`) before it's registered. |
| **Autonomous by design** | As of the current build, the only remaining approval gate is a shell-command keyword list (hard-blocked vs. approve/decline); background tasks and desktop control run without a standing wait state. The panic hotkey and per-turn auto-halt are what stop things instead. |

---

## Tools

Every tool the Brain can call lives in `charlie/tools.py`, grouped by area:

**Web & knowledge**
- `web_search` -- compatibility wrapper for quick structured research
- `web_research` -- bounded QUICK/STANDARD/DEEP research with source IDs, citations, products, and media results
- `session_search` -- full-text (FTS5) search over past conversation history
- `memory` -- add/replace/remove/consolidate entries in `MEMORY.md`/`USER.md`/`OPINIONS.md`
- `vector_memory` -- semantic remember/recall across sessions (ChromaDB)
- `graph_add_fact` / `graph_query` / `graph_consolidate` -- knowledge-graph triples

**System & shell**
- `shell_execute` -- runs a command; voice mode restricts to an allowlist, risky keywords/metacharacters are always blocked, gated keywords need approval, and a bare launch of an already-running known app focuses it instead of relaunching
- `system_diagnostics` -- fixed, safe read-only checks (disk/memory/cpu/processes/network)
- `system_control` -- volume/media keys
- `file_read` / `file_write` -- read or write a file's text content

**Desktop perception** *(needs `DESKTOP_CONTROL_ENABLED=true`)*
- `desktop_observe` / `desktop_read_screen` / `desktop_screenshot` / `desktop_windows`

**Desktop action** *(same flag; all require explicit approval on gated keywords)*
- `desktop_click` / `desktop_invoke` / `desktop_type` / `desktop_key`
- `desktop_click_at` / `desktop_move` / `desktop_drag` / `desktop_scroll`
- `desktop_focus` / `desktop_window` / `desktop_move_window`

**Dynamic, config-gated** -- not fixed code, vary by setup:
- `plugin_*` tools (filesystem, browser fetch/screenshot, calendar, sandboxed Python) when `PLUGINS_ENABLED=true`
- MCP server tools when `MCP_ENABLED=true`
- Extension tools installed via the dashboard's Extensions tab

Every call is capped by `IterationBudget` (12/turn interactive, higher for background tasks).

---

## Requirements

- **OS**: Windows 11 (PowerShell for system commands; desktop control is Windows-only)
- **Python**: 3.12+
- **Node.js**: 20+ (for the dashboard)
- **GPU**: NVIDIA GPU with CUDA recommended (for Whisper ASR and Kokoro TTS; CPU fallback works but is slower)
- **LLM**: Any OpenAI-compatible API endpoint
- **Package manager**: [`uv`](https://docs.astral.sh/uv/) for Python

---

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/ItisPhoenix/C.H.A.R.L.I.E.git
cd C.H.A.R.L.I.E
```

### 2. Install dependencies

```bash
uv sync --locked
```

For the dashboard UI:

```bash
cd frontend && npm ci && cd ..
```

For agentic desktop control (optional, Windows only):

```bash
uv sync --locked --extra desktop
```

For optional JavaScript-heavy research escalation:

```bash
uv sync --extra research --extra browser
playwright install chromium
```

Install [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) too if you want the OCR fallback tier
(`DESKTOP_OCR_ENABLED=true`).

### 3. Configure

```bash
cp .env.example .env
```

Edit `.env` with your settings:

```env
# Required: Your LLM endpoint
LLM_URL=https://your-api-endpoint/v1
LLM_API_KEY=your-key-here
LLM_MODEL=your-model-id

# Optional: Self-hosted SearXNG for private web search
SEARXNG_URL=http://localhost:8080

# Optional: Hardware overrides
MIC_INDEX=-1          # -1 = system default, >=0 = specific device
GPU_DEVICE=cuda
```

See [Configuration](#configuration) below and `.env.example` for the full list of ~50 tunable
settings (VAD/ASR tuning, wake word, memory, MCP/plugins, desktop control, vision).

### 4. Run

```bash
python run.py              # full mode: voice engine + web dashboard + LLM Brain
```

Charlie will initialize the voice engine, download models on first run (Whisper, Kokoro), build
the dashboard if it's stale, and start listening. The web dashboard is served at
http://localhost:8000 by default. `CHARLIE_PORT` is configurable; `CHARLIE_HOST`
must remain a loopback address because Charlie's local dashboard has no remote authentication.

> **Note on the dashboard and chat:** the web UI and the LLM Brain are one system.
> In **full mode** the dashboard is fully live -- chat and voice both route through the
> Brain. The `--web-only` flag serves the UI without the Brain, so chat will not get
> a reply (use it only for static UI inspection).

```bash
python run.py --web-only   # UI only, no voice/LLM backend
```

For frontend-only iteration (hot reload), run the dev server separately -- it proxies `/api/*`
to the FastAPI backend on `:8000`:

```bash
cd frontend && npm run dev   # http://localhost:3000
```

---

## Configuration

All settings are via environment variables (`.env` file, loaded through `charlie/config.py`).
See `.env.example` for the complete, commented list. The most commonly tuned ones:

| Variable | Default | Description |
|---|---|---|
| `LLM_URL` | (required) | OpenAI-compatible API base URL |
| `LLM_API_KEY` | (required) | API key for the LLM |
| `LLM_MODEL` | (required) | Model ID to use |
| `SEARXNG_URL` | (empty) | Self-hosted SearXNG URL for private search |
| `WHISPER_MODEL` | `large-v3` | Whisper model for ASR |
| `KOKORO_VOICE` | `af_heart` | Kokoro TTS voice |
| `VAD_THRESHOLD` | `0.25` | Voice activity detection sensitivity (RMS) |
| `VAD_SILENCE_TIMEOUT` | `1.5` | Seconds of silence before processing |
| `ENABLE_BARGE_IN` | `true` | Allow interrupting Charlie mid-response |
| `LLM_DISABLE_REASONING` | `true` | Disable chain-of-thought for lower latency |
| `NATIVE_TOOL_CALLING` | `true` | JSON tool calling (OpenAI/Anthropic-class); `false` for local models needing text-based `TOOL:` parsing |
| `WAKE_WORD_ENABLED` | `false` | Hands-free wake-word activation |
| `MCP_ENABLED` | `false` | Register tools from external MCP servers |
| `PLUGINS_ENABLED` | `false` | Enable the hybrid plugin loader |
| `DESKTOP_CONTROL_ENABLED` | `false` | Enable native click/type/invoke/key desktop control |
| `DESKTOP_OCR_ENABLED` | `true` | OCR fallback tier (needs Tesseract installed) |
| `VISION_ENABLED` | `false` | Local vision model tier for icon/canvas/screen targets |
| `VISION_LLM_URL` | (empty) | Vision model endpoint (separate from the text LLM) |

---

## Architecture

### Directory structure

```
├── charlie/
│   ├── core.py              # Brain class -- LLM streaming, tool loop, system prompt, fast-paths
│   ├── voice.py              # VoiceEngine -- audio capture, VAD, ASR dispatch, TTS, humanization
│   ├── asr_worker.py         # Whisper subprocess -- the only file importing faster_whisper
│   ├── tools.py               # ToolRegistry + all built-in tools (web, shell, file, desktop, memory)
│   ├── known_apps.py          # Single source of truth for known local apps/websites
│   ├── config.py              # Config dataclass -- the only place that reads os.getenv
│   ├── personality.py         # Emotion classification + voice command parsing
│   ├── session_store.py       # SQLite + FTS5 session history
│   ├── memory_store.py        # ChromaDB vector memory
│   ├── memory_graph.py        # SQLite knowledge graph
│   ├── ipc.py                  # ZeroMQ EventBus bridging the voice process and web process
│   ├── mcp_client.py          # MCP client (external tool servers)
│   ├── plugins.py             # Hybrid plugin system (filesystem/browser/calendar/code-exec)
│   ├── extensions/            # Extension-install safety gate + adapters
│   ├── recovery.py            # Shell-command recovery pipeline (auto-fix + approval)
│   ├── budget.py               # IterationBudget -- caps tool-loop rounds per turn
│   ├── web_server.py           # FastAPI app + WebSocket handlers
│   ├── web_server_entry.py    # Subprocess entry point for the web server
│   └── desktop/                # UIA tree, OCR, vision grounding, window management, actions
├── frontend/                   # Vite / React 19 / TypeScript / Zustand dashboard
│   └── src/
│       ├── App.tsx              # react-router: "/" + "/dashboard" -> Dashboard, "/surface/:id" -> SurfaceRoute
│       ├── dashboard/           # /dashboard page -- floating-panel desktop UI (see DASHBOARD_HANDOFF.md)
│       ├── surfaces/            # Qt-HUD-hosted surface routing (widgets/modals/workspaces/notifications)
│       ├── store/charlie.ts     # single shared Zustand store, WS-event-driven
│       └── runtime/bridge.ts    # WebSocket connect/reconnect + typed event decode
├── tests/                      # pytest suite (40 files) -- ruff + pytest must pass before commit
├── run.py                      # Unified entry point (full mode / --web-only)
└── main.py                     # Voice loop orchestration, spawns the web server subprocess
```

### Data flow

```
Mic audio -> VoiceEngine (VAD) -> ASR worker subprocess (Whisper) -> Brain.chat_stream()
                                                                          |
                              tool loop (shell/file/desktop/memory/search/MCP/plugins)
                                                                          |
                                                LLM (streaming) -> TextStreamFilter
                                                                          |
                                          Kokoro TTS (speak) <-> WebSocket -> Dashboard
```

The voice process and the web dashboard process are separate (`main.py` spawns
`web_server_entry.py` as a subprocess), bridged over ZeroMQ PUB/SUB + PUSH/PULL
(`charlie/ipc.py`, ports 5555/5556 by default). The dashboard talks to the web process over
HTTP/WebSocket; voice input and desktop-control results flow through the same `Brain` instance
either way, so a chat message typed in the browser and a spoken command produce identical tool
calls.

### Key design decisions

- **One text LLM, one optional vision LLM.** No fast/slow model split -- `LLM_URL`/`LLM_KEY`/`LLM_MODEL`
  handle every text turn; `VISION_LLM_*` is a separate, opt-in endpoint only used for screen/image
  understanding follow-ups.
- **Deterministic fast-paths beat prompted tool calls.** Opening/closing known apps, background-task
  status queries, and screen-content forcing all bypass the LLM entirely where possible -- faster,
  and immune to a model ignoring its own tool instructions.
- **Streaming-first.** Every data path is a generator; time-to-first-audio is prioritized over
  waiting for a complete reply.
- **Session isolation via `launch_id`.** Each `main.py` run gets a UUID passed to the web subprocess
  via environment variable; the dashboard can filter to "this launch" or "all history."

---

## Voice Commands

Say these while Charlie is speaking to control behavior:

| Command | Effect |
|---|---|
| "stop" / "wait" / "cancel" | Interrupt and stop speaking |
| "be energetic" / "speak faster" | Increase speech energy and speed |
| "calm down" / "speak slower" | Slow down and speak calmly |
| "stop controlling my desktop" | Revoke desktop-control approval for the rest of the session |

Charlie also understands a couple of typed directives:

| Command | Effect |
|---|---|
| `!search <query>` | Search your past conversations (full-text) and read back the matches |
| `/memory-review` (or `!memory-review`) | Print a summary of the knowledge graph Charlie has built from memory |

---

## Web Research

Fresh, news, trend, shopping, media, and broad research requests are routed through the
`ResearchEngine`. Stable explanatory questions stay on the normal LLM path. The engine uses
bounded asynchronous work and these modes:

- `quick`: SearXNG snippets only; never starts the interactive browser.
- `standard`: fetches and extracts selected public pages, then may escalate to Crawl4AI and finally
  the existing Playwright Browser Executor when HTTP extraction is insufficient.
- `deep`: standard research plus one bounded evidence-thin follow-up iteration.

SearXNG is the default provider. Exa and Tavily are optional fallbacks when their keys are configured;
DuckDuckGo is used only as a graceful free fallback and can be disabled with
`RESEARCH_DDG_ENABLED=false`. Set `SEARXNG_URL` in `.env` for the best experience. Public URLs are
validated against local/private network targets before fetching, and fetched pages are treated as
untrusted evidence rather than instructions. `RESEARCH_JINA_ENABLED` remains disabled and is not part
of the required path.

---

## Testing

Backend:

```bash
uv run ruff check .
uv run pytest -v
```

Frontend (run in `frontend/`, in order):

```bash
npx tsc --noEmit
npm run lint
npm test
```

All of the above run in CI (`.github/workflows/ci.yml`) on every push/PR to `main`. Both backend
and frontend checks must pass cleanly before a change is considered done.

---

## Troubleshooting

**"Command not on the allowed list for voice mode"**
Voice-mode `shell_execute` restricts to a safe prefix allowlist (`start`, `notepad`, `calc`,
`explorer`, `code`, `dir`, `cmd`, `taskkill`, `move`, `copy`). Anything else needs the web UI
(text chat isn't voice-restricted) or should go through a dedicated tool (`desktop_*`, `file_write`).

**Notepad (or another app) keeps reopening instead of coming to the front**
Should no longer happen -- both the deterministic app-opener and any `shell_execute` launch
attempt check for an already-running process first and focus it instead. If you still see it,
the app's process name may not be in `charlie/known_apps.py`'s `APP_REGISTRY` yet.

**Charlie mishears keyboard typing as speech**
Onset detection requires two consecutive loud audio frames (~128ms) before it registers as
speech, which filters out isolated click transients. If your mic gain is very hot and typing is
still triggering it, raise `VAD_THRESHOLD` in `.env` (default `0.25`) -- but note this also
raises the bar for real speech, so tune gradually.

**Vision model never gets used for screen questions**
Requires both `VISION_ENABLED=true` and `DESKTOP_CONTROL_ENABLED=true`, with `VISION_LLM_URL`/
`VISION_LLM_KEY`/`VISION_LLM_MODEL` pointing at a real endpoint. Without a vision model
configured, screen questions fall back to OCR/UIA text description.

**`uvicorn`/pyzmq `RuntimeError` on startup (Windows)**
Known Windows-specific issue: uvicorn's `loop="asyncio"` hardcodes `ProactorEventLoop`, which
pyzmq's asyncio integration can't use. Already fixed via `loop="none"` in both `run.py` and
`charlie/web_server.py` -- if you see this, check nothing reintroduced `loop="asyncio"`.

**Frontend build looks stale**
`run.py` only rebuilds the dashboard when a source file under `frontend/src` is newer than
`frontend/out/index.html`. Force a rebuild with `cd frontend && npm run build`, or delete
`frontend/out` and rerun `python run.py`.

**Fresh model downloads take a long time on first run**
Expected -- Whisper (`large-v3` by default) and Kokoro TTS models are pulled on first use. Set
a smaller `WHISPER_MODEL` (e.g. `medium` or `small`) in `.env` if startup latency matters more
than transcription accuracy.

---

## License

MIT
