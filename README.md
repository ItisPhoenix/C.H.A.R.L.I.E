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

[![Status](https://img.shields.io/badge/STATUS-PRODUCTION_READY-green?style=for-the-badge&logo=github)](https://github.com/ItisPhoenix/CHARLIE)
[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Next.js](https://img.shields.io/badge/Next.js-15-000000?style=for-the-badge&logo=next.js&logoColor=white)](https://nextjs.org)
[![Platform](https://img.shields.io/badge/Platform-Windows-0078D6?style=for-the-badge&logo=windows)](https://microsoft.com/windows)
[![License](https://img.shields.io/badge/License-Private-red?style=for-the-badge)](LICENSE)

*A privacy-first, voice-first AI assistant. Local sensory compute with cloud-assisted reasoning.*

</div>

---

## What is C.H.A.R.L.I.E.?

A hybrid, privacy-conscious AI assistant built for a single operator. It leverages **Local Compute** for all sensory data (Voice, Vision, Memory) and uses high-performance **Cloud APIs** (Gemini, OpenAI, NVIDIA NIM) for its core reasoning engine.

It listens. It thinks. It speaks. It acts.

---

## Quick Start

```bash
# Install dependencies
uv sync

# Install dashboard dependencies
cd dashboard && npm install && cd ..

# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Run CHARLIE
uv run python main.py
```

Dashboard: `http://localhost:3000/`  
Control Server: `http://localhost:8090/`

---

## Architecture

| Layer | Component | Description |
|-------|-----------|-------------|
| **Core** | `charlie/brain/` | Brain, Reactor, Chain Executor, Tool Handler, Model Router, Stream Handler |
| **Intelligence** | `charlie/intelligence/` | Frustration Detector, Pattern Tracker, Suggestion Engine, Briefing, Outcome Tracker, Calendar Intel |
| **Memory** | `charlie/memory/` | Working, Episodic (ChromaDB), Semantic (SQLite+ChromaDB), Procedural, RAG Indexer |
| **Automation** | `charlie/automation/` | Rule Engine, Autonomy Loop, Event Router, Risk Gate, Learning Tracker, Proactivity Engine |
| **Tools** | `charlie/tools/` | 80+ tools: system, web, media, file, coding, comms, research, security, dynamic builder |
| **Agents** | `charlie/agents/` | 7 agent manifests: research, writer, system, comms, vision, coding, redteam |
| **Skills** | `charlie/skills/` | 6 skill directories: deep-research, source-verification, nmap-mastery, web-exploitation, etc. |
| **Integrations** | `charlie/integrations/` | Gmail, Google Calendar, GitHub, Notion, health tracker |
| **MCP** | `charlie/mcp/` | Model Context Protocol client, manager, bridge (stdio + SSE transport) |
| **Dashboard** | `dashboard/` | Next.js 15 cyberpunk dashboard with 20 pages, real-time WebSocket sync |
| **Watchdog** | `charlie/watchdog/` | Phoenix Supervisor, Daemon Supervisor, Control Server, IPC Bridge |
| **Automation** | `charlie/automation/` | Autonomy loop, rule engine, risk gate, learning tracker, proactivity engine |

### Multi-Process Architecture

CHARLIE runs as a supervised multi-process system:
- **Audio** — Wake word detection (WebRTC VAD), STT (Whisper), TTS (Kokoro), Gemini Live API streaming
- **Brain** — LLM reasoning, tool execution, conversation management
- **Browser** — Headless Chromium for web automation
- **Vision** — Screen analysis, OCR, visual understanding
- **Dashboard** — Next.js web UI on port 3000 (proxies to Control Server on 8090)

---

## Dashboard — 20 Pages

The cyberpunk command center dashboard features:

| Page | Description |
|------|-------------|
| `/` (Home) | Voice orb, system health, recent activity, task progress |
| `/status` | Real-time daemon status, CPU/RAM, subsystem health |
| `/chat` | Full chat interface with push-to-talk, context panel |
| `/voice` | Voice activity monitor, STT/TTS models, persistent transcript log |
| `/automation` | Rule engine visualization, list/flow views, toggle with API sync |
| `/integrations` | Gmail, Calendar, GitHub, Notion health monitoring |
| `/tools` | Live tool execution feed with WS real-time updates |
| `/tasks` | Kanban board with drag-and-drop task management |
| `/agents` | Agent network view, card/grid layouts |
| `/skills` | Skill library with approve/reject workflow |
| `/evolution` | Self-evolution tracking with improvement proposals |
| `/memory` | Memory graph visualization, search across all memory types |
| `/search` | Unified search across chat, memory, tools, and tasks |
| `/analytics` | Tool usage charts, response times, agent activity |
| `/logs` | Live log viewer with process/level filtering |
| `/mcp` | MCP server management, tool listing |
| `/globe` | 3D world map for geographic context |
| `/briefing` | Daily briefing with agenda, health, tasks, intel |
| `/settings` | Configuration viewer |
| `/setup` | 6-step setup wizard |

### Dashboard Tech Stack
- **Next.js 15** with App Router
- **TypeScript** — 0 compilation errors
- **Tailwind CSS** — Cyberpunk theme with Orbitron + Exo 2 fonts
- **Zustand** — Global state management
- **WebSocket** — Real-time sync for voice, chat, tools, tasks
- **SWR-compatible** — Polling with configurable intervals

---

## Key Systems

### Voice System
- **Wake Word** — "Charlie" detection via WebRTC VAD
- **STT** — Whisper (local) or Gemini Live API (cloud streaming)
- **TTS** — Kokoro (local) or Gemini Live API (cloud streaming)
- **Push-to-Talk** — Browser-based Web Speech API in chat
- **Persistent Transcript** — All voice input logged and visible in voice page

### Learning Engine
- **OutcomeTracker** — SQLite-backed outcome tracking with implicit user signal detection
- **PatternDetector** — 5 pattern types: temporal, behavioral, workflow, agent_routing, preference
- **Learned Preferences** — Injected into system prompt via ContextBuilder

### Autonomy & Proactive
- **Suggestion Engine** — 8 trigger types: morning briefing, meeting reminder, idle resume, pattern automation, deadline alert, error recovery, predictive, tool health
- **Autonomy Loop** — Background polling with quiet hours, frustration monitoring, idle detection
- **Briefing** — 7 sections: agenda, health, tasks, intel, context, learned insights, yesterday

### Security
- **4-tier Risk Gate** — TIER_0 (auto-approve) through TIER_3 (always deny)
- **Guardian** — Pre-execution safety checks on all tools
- **AST-validated eval** — Rule engine conditions validated against dangerous AST nodes before evaluation
- **Privacy Redactor** — OCR-based PII blurring for screen/camera content

### Self-Evolution
- **EvolutionEngine** — Proposes improvements based on usage patterns
- **SkillCreator** — Auto-generates new skills from repeated workflows
- **PatternTracker** — Identifies automation opportunities

---

## Configuration

All configuration via `.env` file:

| Variable | Required | Description |
|----------|----------|-------------|
| `GEMINI_API_KEY` | Yes | Google Gemini API key (primary LLM) |
| `OPENAI_API_KEY` | No | OpenAI API key (fallback LLM) |
| `NIM_API_KEY` | No | NVIDIA NIM API key |
| `NIM_BASE_URL` | No | NIM API endpoint |
| `NIM_PRIMARY_MODEL` | No | Primary NIM model name |
| `VISION_MODEL` | Yes | Vision model name |
| `EMBEDDING_MODEL` | Yes | Embedding model name |
| `EMBEDDING_URL` | Yes | Embedding API endpoint |
| `TELEGRAM_TOKEN` | No | Telegram bot token |
| `TELEGRAM_CHAT_ID` | No | Telegram chat ID |
| `GITHUB_TOKEN` | No | GitHub integration |
| `NOTION_TOKEN` | No | Notion integration |

---

## Testing

```bash
# Run all tests
uv run pytest tests/ -q

# Run specific module
uv run pytest tests/test_automation.py -x --tb=short

# Ruff linting
uv run ruff check charlie/

# TypeScript check
cd dashboard && npx tsc --noEmit
```

---

## Codebase Metrics

| Metric | Value |
|--------|-------|
| Python files | 218 |
| TypeScript/React files | 77 |
| Dashboard pages | 20 |
| Tools | 80+ |
| Agent manifests | 7 |
| Skill directories | 6 |
| Integrations | 5 |
| Ruff F-errors | 0 |
| TS errors | 0 |

---

<div align="center">

*Built by* **ItisPhoenix** · *Powered by open-source models*

`PRODUCTION READY — 20 PAGES — VOICE-FIRST — CYBERPUNK HUD`

</div>
