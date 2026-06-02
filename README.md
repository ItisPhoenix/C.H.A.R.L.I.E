<div align="center">

<br/>

```
 ██████╗██╗  ██╗ █████╗ ██████╗ ██╗     ██╗███████╗
██╔════╝██║  ██║██╔══██╗██╔══██╗██║     ██║██╔════╝
██║     ███████║███████║██████╔╝██║     ██║█████╗  
██║     ██╔══██║██╔══██║██╔══██╗██║     ██║██╔══╝  
╚██████╗██║  ██║██║  ██║██║  ██║███████╗██║███████╗
 ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚═╝╚══════╝
```

**Completely Helpful And Rather Local Intelligent Engine**

[![Status](https://img.shields.io/badge/STATUS-Development-yellow?style=for-the-badge&logo=github)](https://github.com/ItisPhoenix/CHARLIE)
[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Next.js](https://img.shields.io/badge/Next.js-15-000000?style=for-the-badge&logo=next.js&logoColor=white)](https://nextjs.org)
[![Platform](https://img.shields.io/badge/Platform-Windows-0078D6?style=for-the-badge&logo=windows)](https://microsoft.com/windows)
[![License](https://img.shields.io/badge/License-MIT-lightbluey?style=for-the-badge)](LICENSE)

**Charlie v0.1 — Personal AI Assistant (Development)**

*A privacy-first, voice-first AI assistant. Local sensory compute with cloud-assisted reasoning.*

</div>

---

## What is C.H.A.R.L.I.E.?

A hybrid, privacy-conscious AI assistant built for a single operator on a Windows workstation (16 GB RAM, RTX 4060 Ti 8 GB VRAM, Ryzen 5600X). It leverages **local compute** for all sensory data (voice, vision, memory) and uses cloud APIs (NVIDIA NIM, Gemini, OpenAI) for its core reasoning engine.

This is an active development project. Subsystems are functional but under hardening — expect rough edges.

---

## Install

```bash
# Python dependencies (requires uv)
uv sync

# Dashboard dependencies
cd dashboard && npm install && cd ..

# Configure environment
cp .env.example .env
# Edit .env with your API keys (NIM_API_KEY, GEMINI_API_KEY, etc.)
```

---

## Run

```bash
# Standard mode (foreground, with tray icon)
uv run python main.py

# Daemon mode (background supervisor with dashboard + control server)
uv run python main.py --daemon
```

Dashboard: `http://localhost:3000/`  
Control Server: `http://localhost:8090/`

---

## Doctor (Self-Check)

Charlie includes a built-in diagnostic tool that verifies subsystem health without modifying any files:

```bash
uv run python main.py doctor
```

This checks:
- NIM API reachability
- STT/TTS model files present
- Gmail credentials file
- MCP server configuration
- VRAM budget setting
- Canonical security tier assertions

Each check reports pass/warn/fail with actionable remediation on failure. The same check is available at `GET /api/doctor` when the daemon is running.

---

## VRAM Budget

Charlie manages GPU memory to avoid OOM crashes. Configure in `charlie_config.json`:

```json
{
  "resources": {
    "vram_budget_mb": 7168,
    "vram_warning_mb": 6500,
    "model_unload_delay_s": 30,
    "model_priority": { "text": "primary", "vision": "on_demand" }
  }
}
```

- `vram_budget_mb` — hard ceiling; models are unloaded if loading would exceed this
- `vram_warning_mb` — threshold for dashboard warnings
- `model_unload_delay_s` — seconds before on-demand models (vision) are unloaded after use
- `model_priority` — which models stay resident vs. load on demand

---

## Self-Modify & Auto-Patcher

Both capabilities default to **disabled** for safety:

```json
{
  "safety": {
    "self_modify_enabled": false,
    "auto_patcher_enabled": false
  }
}
```

- **self_modify_enabled** — when `false`, any tool that would alter Charlie's own source code is refused with a clear message. Enable only if you understand the risks.
- **auto_patcher_enabled** — when `false`, the watchdog supervisor will only restart and quarantine failing processes (no source patches). Enable to allow the self-healer to attempt code fixes on crash.

Changes to these flags in `charlie_config.json` take effect on next startup and are persisted back on toggle via the dashboard.

---

## Architecture

| Layer | Component | Description |
|-------|-----------|-------------|
| **Core** | `charlie/brain/` | Brain, Reactor, Chain Executor, Tool Handler, Model Router, Stream Handler |
| **Intelligence** | `charlie/intelligence/` | Frustration Detector, Pattern Tracker, Suggestion Engine, Briefing, Outcome Tracker, Calendar Intel |
| **Memory** | `charlie/memory/` | Working, Episodic (ChromaDB), Semantic (SQLite+ChromaDB), Procedural, RAG Indexer |
| **Automation** | `charlie/automation/` | Rule Engine, Autonomy Loop, Event Router, Risk Gate, Learning Tracker, Proactivity Engine |
| **Tools** | `charlie/tools/` | ~80 tools: system, web, media, file, coding, comms, research, security, dynamic builder |
| **Agents** | `charlie/agents/` | 7 agent manifests: research, writer, system, comms, vision, coding, redteam |
| **Integrations** | `charlie/integrations/` | Gmail, Google Calendar, GitHub, Notion, health tracker |
| **MCP** | `charlie/mcp/` | Model Context Protocol client, manager, bridge (stdio + SSE transport) |
| **Dashboard** | `dashboard/` | Next.js 15 cyberpunk dashboard with ~20 pages, real-time WebSocket sync |
| **Watchdog** | `charlie/watchdog/` | Phoenix Supervisor, Daemon Supervisor, Control Server, IPC Bridge |

*Note: Metrics (tool count, page count) are approximate and may drift as development continues.*

### Multi-Process Architecture

Charlie runs as a supervised multi-process system:
- **Audio** — Wake word detection (WebRTC VAD), STT (Whisper), TTS (Kokoro), Gemini Live API streaming
- **Brain** — LLM reasoning, tool execution, conversation management
- **Browser** — Headless Chromium for web automation
- **Vision** — Screen analysis, OCR, visual understanding
- **Dashboard** — Next.js web UI on port 3000 (proxies to Control Server on 8090)

---

## Dashboard Pages (approximate)

| Page | Description |
|------|-------------|
| `/` (Home) | Voice orb, system health, recent activity |
| `/status` | Real-time daemon status, CPU/RAM, subsystem health |
| `/chat` | Chat interface with push-to-talk |
| `/voice` | Voice activity monitor, STT/TTS status |
| `/automation` | Rule engine visualization, toggle rules |
| `/integrations` | Gmail, Calendar, GitHub, Notion health |
| `/tools` | Live tool execution feed |
| `/tasks` | Task management |
| `/agents` | Agent network view |
| `/memory` | Memory search across all types |
| `/search` | Unified search across chat, memory, tools, tasks |
| `/analytics` | Tool usage charts, response times |
| `/logs` | Live log viewer with filtering |
| `/mcp` | MCP server management |
| `/briefing` | Daily briefing |
| `/settings` | Configuration viewer |

---

## Testing

```bash
# Run all tests
uv run pytest tests/ -q

# Lint
uv run ruff check charlie/

# TypeScript check (dashboard)
cd dashboard && npx tsc --noEmit
```

---

## Configuration

Primary configuration via `.env` file. Runtime overrides via `charlie_config.json`.

| Variable | Required | Description |
|----------|----------|-------------|
| `NIM_API_KEY` | Yes | NVIDIA NIM API key (primary reasoning) |
| `NIM_BASE_URL` | No | NIM API endpoint (default: integrate.api.nvidia.com) |
| `GEMINI_API_KEY` | No | Google Gemini API key (voice streaming, fallback) |
| `VISION_MODEL` | No | Vision model name |
| `EMBEDDING_MODEL` | No | Embedding model name |
| `EMBEDDING_URL` | No | Embedding API endpoint |
| `TELEGRAM_TOKEN` | No | Telegram bot token (alerts) |
| `TELEGRAM_CHAT_ID` | No | Telegram chat ID |
| `GITHUB_TOKEN` | No | GitHub integration |
| `NOTION_TOKEN` | No | Notion integration |

---

<div align="center">

*Built by* **ItisPhoenix** · *Powered by open-source models*

</div>
