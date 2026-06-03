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
# Edit .env with your LLM_URL and LLM_API_KEY (and optional LLM_VISION_*)
```

---

## Run

```bash
# Standard mode (foreground, with tray icon)
uv run python main.py

# Daemon mode (background supervisor with dashboard + control server)
uv run python main.py --daemon

# One-click launcher (double-click)
start-charlie.bat
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

Charlie auto-detects GPU VRAM via nvidia-smi and calculates a budget after fixed costs (STT ~2500MB, TTS ~1000MB, headroom 500MB). Configure overrides in `charlie_config.json`:

```json
{
  "resources": {
    "vram_budget_mb": 4188,
    "vram_warning_mb": 3500
  }
}
```

---

## Architecture

| Layer | Component | Description |
|-------|-----------|-------------|
| **Core** | `charlie/brain/` | Brain, Reactor, Chain Executor, Tool Handler, Model Router, Stream Handler |
| **Agents** | `charlie/agents/` | 7 manifest-driven agents with coordinator pattern, LLM goal decomposition, parallel execution |
| **Intelligence** | `charlie/intelligence/` | Skill Nudge, Evolution Engine, Pattern Tracker, Suggestion Engine, Outcome Tracker |
| **Memory** | `charlie/memory/` | Working, Episodic (ChromaDB), Semantic (SQLite+ChromaDB), Procedural (seen_ids), RAG Indexer |
| **Automation** | `charlie/automation/` | Rule Engine, Autonomy Loop, Event Router, Risk Gate, Learning Tracker |
| **Tools** | `charlie/tools/` | 87 tools: system, web, media, file, coding, comms, research, security, browser |
| **Skills** | `charlie/skills/` | 6 skills in SKILL.md format (agentskills.io spec) |
| **MCP** | `charlie/mcp/` | Model Context Protocol client, manager, bridge (stdio + SSE transport) |
| **Dashboard** | `dashboard/` | Next.js 15 cyberpunk dashboard with ~20 pages, real-time WebSocket sync |
| **Watchdog** | `charlie/watchdog/` | Phoenix Supervisor, Daemon Supervisor, Control Server, IPC Bridge |

### Multi-Process Architecture

Charlie runs as a supervised multi-process system:
- **Audio** — Wake word detection (WebRTC VAD), STT (Whisper), TTS (Kokoro), barge-in with playback lock
- **Brain** — LLM reasoning, tool execution, conversation management, agent orchestration
- **Browser** — Headless Chromium via CloakBrowser for web automation
- **Vision** — Screen analysis, OCR, visual understanding
- **Dashboard** — Next.js web UI on port 3000 (proxies to Control Server on 8090)

### Agent Coordination

Charlie uses a coordinator pattern for complex goals:
1. **Decompose** — LLM breaks complex goals into sub-tasks
2. **Route** — Each sub-task dispatched to the best-fit specialist agent
3. **Execute** — Agents run in parallel via asyncio.gather
4. **Merge** — Results combined into a unified response via LLM

---

## Dashboard Pages

| Page | Description |
|------|-------------|
| `/` (Home) | Voice orb, system health, recent activity |
| `/status` | Real-time daemon status, CPU/RAM, subsystem health |
| `/chat` | Chat interface with push-to-talk |
| `/voice` | Voice activity monitor, STT/TTS status |
| `/automation` | Rule engine visualization, toggle rules |
| `/integrations` | Gmail, Calendar, GitHub, Notion health (live WS updates) |
| `/tools` | Live tool execution feed |
| `/tasks` | Task management |
| `/agents` | Agent network view with orchestrator feed |
| `/memory` | Memory search + stats (live WS updates) |
| `/evolution` | Self-evolution history (live WS updates) |
| `/search` | Unified search across chat, memory, tools, tasks |
| `/analytics` | Tool usage charts, response times |
| `/logs` | Live log viewer with filtering |
| `/mcp` | MCP server management |
| `/briefing` | Daily briefing |
| `/settings` | Configuration viewer |

---

## Testing

```bash
# Run all tests (59 tests)
uv run pytest tests/ -v

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
| `LLM_URL` | Yes | Full URL of any OpenAI-compatible LLM endpoint (LM Studio, NIM, OpenRouter, Ollama, vLLM, etc.) |
| `LLM_API_KEY` | No | Bearer token for the LLM endpoint (empty for local servers like LM Studio) |
| `LLM_MODEL` | Yes | Model name served by `LLM_URL` |
| `LLM_VISION_URL` | No | Full URL of an OpenAI-compatible vision endpoint (leave empty to disable vision) |
| `LLM_VISION_API_KEY` | No | Bearer token for the vision endpoint |
| `LLM_VISION_MODEL` | No | Vision model name served by `LLM_VISION_URL` |
| `TELEGRAM_TOKEN` | No | Telegram bot token (alerts) |
| `TELEGRAM_CHAT_ID` | No | Telegram chat ID |
| `GITHUB_TOKEN` | No | GitHub integration |
| `NOTION_TOKEN` | No | Notion integration |

---

<div align="center">

*Built by* **ItisPhoenix** · *Powered by open-source models*

</div>
